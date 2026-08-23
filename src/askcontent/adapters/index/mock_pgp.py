"""Mock PGP — the company-wide vector index.

WHAT THIS STANDS IN FOR
=======================
PGP holds vector representations of company content and returns **document
identifiers**. It does not return the documents. Everything hard in this
product lives in the gap between this port and ContentRepository.

REPLACING THIS ADAPTER
======================
Each method below maps to one PGP call. Nothing outside this directory changes
(CNT-FED-05).

  list_knowledgebases()
      REAL CALL:  GET  {PGP_BASE}/v1/knowledgebases
      ASSUMED:    [{id, name, description, documentCount, lastIndexedAt,
                    embeddingModel, dimension}]
      OPEN Q:     Does the listing respect the *calling credential's* grants,
                  or does it list every knowledgebase in the company? If the
                  latter, the discovery screen must distinguish "visible" from
                  "queryable" or administrators will register KBs that return
                  nothing.

  describe(kb_id)
      REAL CALL:  GET  {PGP_BASE}/v1/knowledgebases/{kb_id}/schema
      ASSUMED:    field names with observed types and value samples.
      OPEN Q:     Does PGP expose field *samples*? If not, CNT-MAP-03 (live
                  samples in the mapping editor) has to be satisfied by
                  sampling through search() instead — plan for that, it is the
                  likely answer.

  search(kb_id, query, filters, k, cursor)
      REAL CALL:  POST {PGP_BASE}/v1/knowledgebases/{kb_id}/search
                  body: {query | vector, filters, topK, cursor}
      ASSUMED:    {hits: [{docId, score, snippet?, metadata}], nextCursor}
      OPEN Qs, all of which change the architecture:
        1. Does PGP accept **metadata filters** in the query, or only kNN?
           If only kNN, the scope predicate cannot be pushed down, and
           CNT-SCP-14 (never post-filter) forces us to hold our own metadata
           store keyed by PGP doc id. Settle this first.
        2. Does PGP enforce ACLs **per calling user**, or does it return
           everything the service credential can see? If the latter, we carry
           the permission predicate ourselves and the resolution gate in
           services/retrieval.py is the only thing preventing a leak.
        3. Which embedding model, what dimension, and who triggers rebuilds?
           A model change on their side silently changes our ranking.
        4. Rate limits and p99 latency at our k, with fan-out across KBs.

THE MOCK IS DELIBERATELY UNPLEASANT
===================================
It injects latency, pagination, stale identifiers, metadata that disagrees with
the ECM, and intermittent failure (CNT-FED-03). A mock that always succeeds in
a millisecond and agrees with itself lets a design through that cannot survive
either real system.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time

from ...domain.documents import DocType
from ...fixtures.corpus import (
    KB_DESCRIPTIONS,
    KB_FIELD_VOCABULARY,
    SEED,
    SeedDoc,
)
from ...ports.content_index import (
    FieldSample,
    IndexFilters,
    IndexHit,
    IndexPage,
    IndexUnavailable,
    KnowledgeBaseDescriptor,
)
from ..embedders.hashing import HashingEmbedder, cosine


def _strip_html(html: str) -> str:
    out, depth = [], 0
    for ch in html:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return " ".join("".join(out).split())


def _stable_unit(*parts: str) -> float:
    """Deterministic pseudo-random in [0, 1). Seeded by content, never by a
    clock, so a run is reproducible (the evaluation gate depends on it)."""
    digest = hashlib.blake2b("|".join(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


class MockPgpIndex:
    """A ContentIndex implementation over the seed corpus."""

    def __init__(
        self,
        *,
        simulate_latency: bool = True,
        failure_rate: float = 0.0,
        page_size: int = 10,
        embedder: HashingEmbedder | None = None,
    ) -> None:
        self.simulate_latency = simulate_latency
        self.failure_rate = failure_rate
        self.page_size = page_size
        self._embedder = embedder or HashingEmbedder()
        self._vectors: dict[str, list[float]] = {}
        for doc in SEED:
            text = f"{doc.title}\n{_strip_html(doc.body_html)}"
            self._vectors[doc.doc_id] = self._embedder.embed_query(text)

    # -- capability listing ------------------------------------------------

    def list_knowledgebases(self) -> list[KnowledgeBaseDescriptor]:
        out = []
        for kb_id, (name, description, exposes_acl) in KB_DESCRIPTIONS.items():
            docs = [d for d in SEED if d.kb_id == kb_id]
            out.append(
                KnowledgeBaseDescriptor(
                    kb_id=kb_id,
                    name=name,
                    description=description,
                    document_count=len(docs),
                    last_indexed_at=dt.datetime(2026, 8, 22, 3, 0, 0),
                    embedding_model="pgp-text-embed-3",
                    embedding_dimension=1024,
                    exposes_acl=exposes_acl,
                    fields=self._field_samples(kb_id, docs),
                )
            )
        return out

    def describe(self, kb_id: str) -> KnowledgeBaseDescriptor:
        for kb in self.list_knowledgebases():
            if kb.kb_id == kb_id:
                return kb
        raise KeyError(f"unknown knowledgebase: {kb_id}")

    def _field_samples(
        self, kb_id: str, docs: list[SeedDoc]
    ) -> tuple[FieldSample, ...]:
        """What the mapping editor renders (CNT-MAP-03).

        Coverage is real, computed over the documents: a field present on 40%
        of documents shows as 0.40, and a required field below the threshold
        blocks activation (CNT-MAP-04).
        """
        vocabulary = KB_FIELD_VOCABULARY[kb_id]
        samples: list[FieldSample] = []
        raw_rows = [self._raw_metadata(d) for d in docs]
        for canonical, source_field in vocabulary.items():
            present = [r[source_field] for r in raw_rows if r.get(source_field) not in (None, "")]
            observed = {
                "labels": "string[] or csv",
                "updated_at": "string (format varies by KB)",
                "acl_principals": "string[]",
            }.get(canonical, "string")
            samples.append(
                FieldSample(
                    name=source_field,
                    observed_type=observed,
                    coverage=len(present) / len(raw_rows) if raw_rows else 0.0,
                    distinct_estimate=len({str(v) for v in present}),
                    samples=tuple(str(v)[:80] for v in present[:3]),
                )
            )
        return tuple(samples)

    # -- the raw shape PGP would return ------------------------------------

    def _raw_metadata(self, doc: SeedDoc) -> dict[str, object]:
        """Metadata in the knowledgebase's own vocabulary and own formats.

        Every knowledgebase spells and shapes these differently, on purpose.
        The platform never learns these names; the field map does.
        """
        vocabulary = KB_FIELD_VOCABULARY[doc.kb_id]
        row: dict[str, object] = {}
        row[vocabulary["doc_id"]] = doc.doc_id
        # The index's copy of the title can lag the store's (CNT-RET-08).
        row[vocabulary["title"]] = doc.index_title_override or doc.title
        row[vocabulary["url"]] = f"https://ecm.example.com{doc.path}"
        row[vocabulary["space"]] = doc.space
        row["path"] = doc.path

        if doc.updated_at is not None:
            field = vocabulary["updated_at"]
            if doc.kb_id == "kb-eng-runbooks":
                row[field] = int(doc.updated_at.timestamp())          # epoch
            elif doc.kb_id == "kb-fin-controls":
                row[field] = doc.updated_at.strftime("%d/%m/%Y")      # DD/MM/YYYY
            else:
                row[field] = doc.updated_at.isoformat()               # ISO-8601

        if "owner" in vocabulary:
            row[vocabulary["owner"]] = doc.owner
        if "labels" in vocabulary:
            field = vocabulary["labels"]
            # csv in some knowledgebases, a list in others
            row[field] = (
                ",".join(doc.labels)
                if doc.kb_id in ("kb-hr-policies", "kb-fin-controls")
                else list(doc.labels)
            )
        if "sensitivity" in vocabulary:
            row[vocabulary["sensitivity"]] = {
                "public": "Public",
                "internal": "Internal Use Only",
                "confidential": "Confidential",
                "restricted": "Restricted",
            }[doc.sensitivity]
        if "acl_principals" in vocabulary:
            row[vocabulary["acl_principals"]] = list(doc.acl_principals)
        return row

    # -- search ------------------------------------------------------------

    def search(
        self,
        kb_id: str,
        query: str,
        filters: IndexFilters,
        k: int = 20,
        cursor: str | None = None,
        rerank: bool = False,
    ) -> IndexPage:
        # Accepted and ignored. The offline mock advertises no reranking
        # capability, and a caller must not have to branch on that — silently
        # not reranking is safe, in the way silently double-reranking is not.
        self._simulate_call(kb_id, query)

        query_vec = self._embedder.embed_query(query)
        candidates: list[tuple[float, SeedDoc]] = []
        for doc in SEED:
            if doc.kb_id != kb_id:
                continue
            # Filters are applied *in the query*, never to the results
            # afterwards (CNT-SCP-14). In the real adapter these become the
            # request's filter clause; if PGP cannot accept them, see OPEN Q 1.
            if not self._passes(doc, filters):
                continue
            score = cosine(query_vec, self._vectors[doc.doc_id])
            if score <= 0.02:
                continue
            candidates.append((score, doc))

        candidates.sort(key=lambda pair: (-pair[0], pair[1].doc_id))

        offset = int(cursor) if cursor else 0
        window = candidates[offset : offset + min(k, self.page_size)]
        next_cursor = (
            str(offset + len(window))
            if offset + len(window) < min(len(candidates), k)
            else None
        )

        hits = tuple(
            IndexHit(
                doc_id=doc.doc_id,
                kb_id=kb_id,
                score=round(score, 6),
                passage_hint=self._snippet(doc, query),
                metadata=self._raw_metadata(doc),
            )
            for score, doc in window
        )
        return IndexPage(hits=hits, cursor=next_cursor, total_estimate=len(candidates))

    def list_documents(
        self, kb_id: str, cursor: str | None = None, page_size: int = 500
    ) -> IndexPage:
        """REAL CALL: GET {PGP_BASE}/v1/knowledgebases/{kb_id}/documents?cursor=
        OPEN Q: does PGP expose enumeration at all? If not, the scope preview
        is built from ECM enumeration instead (see the port docstring)."""
        docs = sorted(
            (d for d in SEED if d.kb_id == kb_id), key=lambda d: d.doc_id
        )
        offset = int(cursor) if cursor else 0
        window = docs[offset : offset + page_size]
        next_cursor = str(offset + len(window)) if offset + len(window) < len(docs) else None
        return IndexPage(
            hits=tuple(
                IndexHit(
                    doc_id=doc.doc_id, kb_id=kb_id, score=0.0,
                    metadata=self._raw_metadata(doc),
                )
                for doc in window
            ),
            cursor=next_cursor,
            total_estimate=len(docs),
        )

    def _passes(self, doc: SeedDoc, filters: IndexFilters) -> bool:
        if filters.spaces and doc.space not in filters.spaces:
            return False
        if filters.labels_any and not set(filters.labels_any) & set(doc.labels):
            return False
        if filters.labels_none and set(filters.labels_none) & set(doc.labels):
            return False
        if filters.updated_after and doc.updated_at is not None:
            if doc.updated_at.date() < filters.updated_after:
                return False
        if filters.updated_before and doc.updated_at is not None:
            if doc.updated_at.date() > filters.updated_before:
                return False
        # NOTE: principals are deliberately *not* enforced here. That models the
        # pessimistic answer to OPEN Q 2 — PGP returning everything the service
        # credential can see. The resolution gate in services/retrieval.py is
        # what prevents the leak, and the mock exists in this shape so that a
        # regression there is caught by a test rather than by a customer.
        return True

    def _snippet(self, doc: SeedDoc, query: str) -> str:
        """Advisory fragment (CNT-FED-02). Truncated mid-word on purpose: this
        is what an index chunker we do not control actually returns, and it is
        why passage_hint is never cited."""
        text = _strip_html(doc.body_html)
        terms = [t for t in query.lower().split() if len(t) > 3]
        lowered = text.lower()
        position = next((lowered.find(t) for t in terms if lowered.find(t) >= 0), 0)
        start = max(0, position - 60)
        return text[start : start + 180]

    # -- failure injection -------------------------------------------------

    def _simulate_call(self, kb_id: str, query: str) -> None:
        roll = _stable_unit("call", kb_id, query)
        if self.failure_rate and roll < self.failure_rate:
            raise IndexUnavailable(
                f"PGP search failed for {kb_id} (simulated upstream 503)"
            )
        if self.simulate_latency:
            # Long-tailed: most calls fast, a few slow. Timeout and budget
            # logic is untestable without this shape.
            tail = _stable_unit("latency", kb_id, query)
            seconds = 0.004 + (0.25 if tail > 0.94 else 0.0) * tail
            time.sleep(seconds)


DOC_TYPE_HINTS: dict[str, DocType] = {}
