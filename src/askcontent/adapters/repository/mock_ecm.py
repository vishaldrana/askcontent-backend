"""Mock ECM — the enterprise content manager, and the system of record.

WHAT THIS STANDS IN FOR
=======================
The ECM holds the documents themselves and the authoritative metadata. PGP
hands us identifiers; this is where we go and get the thing.

REPLACING THIS ADAPTER
======================
  fetch_metadata(ref, principal)
      REAL CALL:  GET {ECM_BASE}/v2/documents/{doc_id}?fields=metadata
                  with the *end user's* authorization, not the service account.
      ASSUMED:    200 with metadata | 404 | 403 | 5xx.
      OPEN Q:     Can we pass through end-user identity (OBO / delegated
                  token), or must we call as a service account and enforce ACLs
                  ourselves? If the latter, CNT-ACL-02 requires us to store
                  resolved principal sets at ingest and keep them fresh, and
                  CNT-ACL-05's revocation interval becomes our SLA rather than
                  the ECM's.

  fetch(ref, principal)
      REAL CALL:  GET {ECM_BASE}/v2/documents/{doc_id}/content
      ASSUMED:    bytes + Content-Type. Content-Type is NOT trusted — we sniff
                  (CNT-PAR-03), because ECMs mislabel routinely.
      OPEN Q:     Is there a version/etag we can key the passage cache on? If
                  not, CNT-RET-11 applies: we hash the fetched bytes, which
                  saves parsing but not the fetch, and the cache becomes much
                  less valuable. Ask for an etag; it is a small request with a
                  large payoff.

  search(query, principal, k, spaces)
      REAL CALL:  POST {ECM_BASE}/v2/search  (BM25 / metadata search)
      ASSUMED:    [{docId, score}] ordered by the ECM's own relevance.
      NOTE:       We use the ORDER and discard the score (CNT-RET-04) — it is
                  on a scale we do not own and cannot compare to PGP's cosine.
      OPEN Q:     Does ECM search support the same metadata predicates as PGP?
                  Where they differ, the compiled scope predicate has to be
                  expressed twice, and any concept only one of them supports
                  must be enforced at the resolution gate instead.

  authorize(principal, ref)
      REAL CALL:  POST {ECM_BASE}/v2/documents/{doc_id}:checkAccess
      OPEN Q:     Is a batch form available? Per-document authorization calls
                  across a 40-candidate fan-out will dominate our latency
                  budget. If there is no batch endpoint, this is the first
                  thing to ask for.

BEHAVIOUR REPRODUCED
====================
404 on identifiers PGP still holds; 403 on documents PGP happily returned;
metadata that disagrees with the index; documents with no version; intermittent
5xx and hard timeouts (CNT-FED-03).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time

from ...domain.documents import (
    DocMetadata,
    DocRef,
    RawDocument,
    Sensitivity,
)
from ...fixtures.corpus import SEED_BY_ID, SeedDoc
from ...ports.content_repository import (
    RepositoryUnavailable,
    Resolution,
    ResolutionOutcome,
)


def _stable_unit(*parts: str) -> float:
    digest = hashlib.blake2b("|".join(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


class MockEcmRepository:
    """A ContentRepository implementation over the seed corpus."""

    def __init__(
        self,
        *,
        simulate_latency: bool = True,
        failure_rate: float = 0.0,
    ) -> None:
        self.simulate_latency = simulate_latency
        self.failure_rate = failure_rate
        self.call_counts: dict[str, int] = {}

    # -- resolution --------------------------------------------------------

    def fetch_metadata(self, ref: DocRef, principal: str) -> Resolution:
        self._simulate_call("fetch_metadata", ref.doc_id)

        doc = SEED_BY_ID.get(ref.doc_id)
        if doc is None or doc.missing_from_store:
            # The index holds an identifier the store no longer has. Aggregated
            # per knowledgebase as the stale-index metric (CNT-RET-07): a rising
            # rate is the earliest signal that PGP's sync is broken, and it is
            # otherwise invisible — the product keeps answering, from a
            # shrinking corpus, with no error anywhere.
            return Resolution(
                ref=ref,
                outcome=ResolutionOutcome.NOT_FOUND,
                detail="document not present in the ECM",
            )

        if self._forbidden(doc, principal):
            # Dropped before ranking. Its existence is never disclosed beyond
            # what CNT-ACL-04 permits.
            return Resolution(
                ref=ref,
                outcome=ResolutionOutcome.FORBIDDEN,
                detail=f"{principal} has no read grant",
            )

        return Resolution(
            ref=ref,
            outcome=ResolutionOutcome.RESOLVED,
            metadata=self._metadata(doc),
        )

    def _forbidden(self, doc: SeedDoc, principal: str) -> bool:
        if principal in doc.forbidden_for:
            return True
        if doc.acl_principals == ("group:all-staff",):
            return False
        # A real implementation resolves the principal's group memberships
        # through the identity provider; here the principal string carries them.
        return not any(p in principal or p == principal for p in doc.acl_principals)

    def _metadata(self, doc: SeedDoc) -> DocMetadata:
        """Authoritative metadata. Note the title here is the *real* one; the
        index may still be serving a stale copy (see index_title_override)."""
        return DocMetadata(
            doc_id=doc.doc_id,
            kb_id=doc.kb_id,
            title=doc.title,
            url=f"https://ecm.example.com{doc.path}",
            updated_at=doc.updated_at,
            space=doc.space,
            owner=doc.owner,
            labels=doc.labels,
            sensitivity=Sensitivity(doc.sensitivity),
            acl_principals=doc.acl_principals,
            version=doc.version,
            mime=doc.mime,
            size_bytes=len(doc.body_html.encode()),
            path=doc.path,
        )

    # -- content -----------------------------------------------------------

    def fetch(self, ref: DocRef, principal: str) -> RawDocument:
        resolution = self.fetch_metadata(ref, principal)
        if resolution.outcome is not ResolutionOutcome.RESOLVED:
            raise RepositoryUnavailable(
                f"cannot fetch {ref.doc_id}: {resolution.outcome}"
            )
        self._simulate_call("fetch", ref.doc_id)
        doc = SEED_BY_ID[ref.doc_id]
        return RawDocument(
            ref=ref,
            blob=doc.body_html.encode("utf-8"),
            # Deliberately vague, as ECMs routinely are. We sniff rather than
            # trust this (CNT-PAR-03).
            mime="application/octet-stream",
            metadata=resolution.metadata or self._metadata(doc),
        )

    # -- native search channel --------------------------------------------

    def search(
        self, query: str, principal: str, k: int = 20, spaces: tuple[str, ...] = ()
    ) -> list[DocRef]:
        """Exact-string and metadata matching — the channel that finds an error
        code or a policy number when the vector channel returns thematically
        similar documents that do not contain it."""
        self._simulate_call("search", query)

        terms = [t for t in query.lower().split() if len(t) > 2]
        scored: list[tuple[float, SeedDoc]] = []
        for doc in SEED_BY_ID.values():
            if doc.missing_from_store:
                continue
            if spaces and doc.space not in spaces:
                continue
            haystack = f"{doc.title} {doc.path} {' '.join(doc.labels)} {doc.body_html}".lower()
            # Crude BM25-shaped scoring: term presence with a length penalty.
            hits = sum(haystack.count(t) for t in terms)
            if hits == 0:
                continue
            score = hits / (1.0 + len(haystack) / 5000.0)
            # Exact title match is what this channel is *for*.
            if any(t in doc.title.lower() for t in terms):
                score *= 2.5
            scored.append((score, doc))

        scored.sort(key=lambda pair: (-pair[0], pair[1].doc_id))
        return [
            DocRef(doc_id=doc.doc_id, kb_id=doc.kb_id) for _, doc in scored[:k]
        ]

    def authorize(self, principal: str, ref: DocRef) -> ResolutionOutcome:
        return self.fetch_metadata(ref, principal).outcome

    # -- failure injection -------------------------------------------------

    def _simulate_call(self, op: str, key: str) -> None:
        self.call_counts[op] = self.call_counts.get(op, 0) + 1
        roll = _stable_unit("ecm", op, key)
        if self.failure_rate and roll < self.failure_rate:
            raise RepositoryUnavailable(f"ECM {op} failed for {key} (simulated 503)")
        if self.simulate_latency:
            tail = _stable_unit("ecm-latency", op, key)
            # Fetch is materially slower than metadata lookup, which is the
            # whole reason the passage cache exists (CNT-RET-10).
            base = 0.012 if op == "fetch" else 0.003
            time.sleep(base + (0.4 if tail > 0.96 else 0.0) * tail)
