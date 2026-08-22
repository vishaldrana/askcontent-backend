"""The ECM, backed by the `ecm_stub` schema.

WHAT THIS STANDS IN FOR
=======================
The system of record. Holds the bytes and the authoritative metadata; ranks
nothing.

REPLACING THIS ADAPTER
======================
  fetch_metadata(ref, principal) -> GET  {ECM_BASE}/v2/documents/{id}?fields=metadata
  fetch(ref, principal)          -> GET  {ECM_BASE}/v2/documents/{id}/content
  search(query, principal, ...)  -> POST {ECM_BASE}/v2/search
  authorize(principal, ref)      -> POST {ECM_BASE}/v2/documents/{id}:checkAccess

The open questions in `mock_ecm.py` still stand. Two of them are answered here in
the way that is *least* favourable to us, on purpose:

  * Permission checks are evaluated per document, not in batch. Across a
    40-candidate fan-out that is 40 round trips, which is exactly the cost that
    makes a batch endpoint worth asking for.
  * `Content-Type` is not trusted. The stub stores a declared mime and this
    adapter passes it as a *hint*; the parser sniffs (CNT-PAR-03).
"""

from __future__ import annotations

from sqlalchemy import text

from ...domain.documents import DocMetadata, DocRef, RawDocument, Sensitivity
from ...ports.content_repository import (
    RepositoryUnavailable,
    Resolution,
    ResolutionOutcome,
)


_STOPWORDS = frozenset(
    "the a an of to and or in on for with by from at as is are was were be been "
    "this that these those it its our your their we you they what which how do "
    "does did can may many much get gets got need needs about into over under "
    "when where who whom why not no yes if then than there here".split()
)


def _significant_terms(query: str) -> list[str]:
    """Terms worth searching for, escaped for `to_tsquery`.

    Punctuation is stripped rather than escaped: an unescaped `?` or `&` in a
    tsquery is a syntax error, and a search endpoint that 500s on a question
    mark is worse than one that returns nothing.
    """
    import re

    words = re.findall(r"[A-Za-z0-9_]+", query.lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS][:12]


class PgEcmRepository:
    def __init__(self, engine) -> None:
        self._engine = engine
        self.call_counts: dict[str, int] = {}

    # -- resolution --------------------------------------------------------

    def fetch_metadata(self, ref: DocRef, principal: str) -> Resolution:
        self._count("fetch_metadata")
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT doc_id, kb_id, title, path, space, owner, labels,
                           sensitivity, acl_principals, version, updated_at, mime,
                           forbidden_for, octet_length(body) AS size_bytes
                    FROM ecm_stub.ecm_document WHERE doc_id = :id
                    """
                ),
                {"id": ref.doc_id},
            ).one_or_none()

        if row is None:
            # The index holds an identifier the store no longer has. Counted per
            # knowledgebase as the stale-index metric (CNT-RET-07) — a rising
            # rate is the earliest signal that the index's sync is broken, and
            # it is otherwise invisible.
            return Resolution(
                ref=ref,
                outcome=ResolutionOutcome.NOT_FOUND,
                detail="document not present in the ECM",
            )

        if self._forbidden(row, principal):
            # Dropped before ranking; its existence is never disclosed beyond
            # what CNT-ACL-04 permits.
            return Resolution(
                ref=ref,
                outcome=ResolutionOutcome.FORBIDDEN,
                detail=f"{principal} has no read grant",
            )

        return Resolution(ref=ref, outcome=ResolutionOutcome.RESOLVED, metadata=self._metadata(row))

    def fetch_metadata_batch(
        self, refs: list[DocRef], principal: str
    ) -> dict[str, Resolution]:
        """One query for every candidate.

        REAL CALL: POST {ECM_BASE}/v2/documents:batchGet
        OPEN Q:    does the ECM expose a batch form at all? If not, the caller
                   falls back to `fetch_metadata` and pays 2 x k round trips.
        """
        if not refs:
            return {}
        self._count("fetch_metadata_batch")
        by_id = {ref.doc_id: ref for ref in refs}

        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT doc_id, kb_id, title, path, space, owner, labels,
                           sensitivity, acl_principals, version, updated_at, mime,
                           forbidden_for, octet_length(body) AS size_bytes
                    FROM ecm_stub.ecm_document WHERE doc_id = ANY(:ids)
                    """
                ),
                {"ids": list(by_id)},
            ).all()

        found = {row.doc_id: row for row in rows}
        out: dict[str, Resolution] = {}
        for doc_id, ref in by_id.items():
            row = found.get(doc_id)
            if row is None:
                out[doc_id] = Resolution(
                    ref=ref, outcome=ResolutionOutcome.NOT_FOUND,
                    detail="document not present in the ECM",
                )
            elif self._forbidden(row, principal):
                out[doc_id] = Resolution(
                    ref=ref, outcome=ResolutionOutcome.FORBIDDEN,
                    detail=f"{principal} has no read grant",
                )
            else:
                out[doc_id] = Resolution(
                    ref=ref, outcome=ResolutionOutcome.RESOLVED,
                    metadata=self._metadata(row),
                )
        return out

    def _forbidden(self, row, principal: str) -> bool:
        if principal in (row.forbidden_for or []):
            return True
        acl = list(row.acl_principals or [])
        if acl == ["group:all-staff"]:
            return False
        # A real implementation resolves group membership through the identity
        # provider. The fixture carries membership in the principal string.
        return not any(p == principal or p in principal for p in acl)

    def _metadata(self, row) -> DocMetadata:
        return DocMetadata(
            doc_id=row.doc_id,
            kb_id=row.kb_id,
            title=row.title,
            url=f"https://ecm.example.com{row.path}",
            updated_at=row.updated_at,
            space=row.space,
            owner=row.owner,
            labels=tuple(row.labels or ()),
            sensitivity=Sensitivity(row.sensitivity),
            acl_principals=tuple(row.acl_principals or ()),
            version=row.version,
            mime=row.mime,
            size_bytes=row.size_bytes,
            path=row.path,
        )

    # -- content -----------------------------------------------------------

    def fetch(self, ref: DocRef, principal: str) -> RawDocument:
        resolution = self.fetch_metadata(ref, principal)
        if resolution.outcome is not ResolutionOutcome.RESOLVED:
            raise RepositoryUnavailable(f"cannot fetch {ref.doc_id}: {resolution.outcome}")

        self._count("fetch")
        with self._engine.connect() as connection:
            body = connection.execute(
                text("SELECT body FROM ecm_stub.ecm_document WHERE doc_id = :id"),
                {"id": ref.doc_id},
            ).scalar_one()

        return RawDocument(
            ref=ref,
            blob=bytes(body),
            # Passed as a hint only. The parser sniffs (CNT-PAR-03), because
            # content managers mislabel routinely.
            mime="application/octet-stream",
            metadata=resolution.metadata,
        )

    # -- the second channel ------------------------------------------------

    def search(
        self, query: str, principal: str, k: int = 20, spaces: tuple[str, ...] = ()
    ) -> list[DocRef]:
        """Full-text over title and path.

        This channel exists because vector search misses exact identifiers,
        rare tokens, part numbers and ticket references — and that is the
        failure users notice. Its *scores* are deliberately discarded by the
        caller: they are BM25-family relevance on a scale we do not own, and
        fusion is by rank (CNT-RET-04).
        """
        self._count("search")

        # `plainto_tsquery` ANDs every term, which makes this channel useless
        # for a natural-language question: "how many weeks of paid parental
        # leave does a primary caregiver get" requires all eleven words to
        # appear in the title, and nothing ever matches. The mock did not show
        # this because its keyword channel counted substrings.
        #
        # OR the significant terms instead and let ts_rank order them. That
        # keeps the property this channel exists for — an exact identifier or a
        # rare token still matches, and now scores highest because it is rare —
        # while a question no longer returns nothing.
        terms = _significant_terms(query)
        if not terms:
            return []
        params: dict[str, object] = {"q": " | ".join(terms), "k": k}
        clauses = ["to_tsvector('english', title || ' ' || path) @@ to_tsquery('english', :q)"]
        if spaces:
            clauses.append("space = ANY(:spaces)")
            params["spaces"] = list(spaces)

        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT doc_id, kb_id,
                           ts_rank(to_tsvector('english', title || ' ' || path),
                                   to_tsquery('english', :q)) AS rank
                    FROM ecm_stub.ecm_document
                    WHERE {' AND '.join(clauses)}
                    ORDER BY rank DESC, doc_id LIMIT :k
                    """
                ),
                params,
            ).all()

        return [DocRef(doc_id=row.doc_id, kb_id=row.kb_id) for row in rows]

    def authorize(self, principal: str, ref: DocRef) -> ResolutionOutcome:
        return self.fetch_metadata(ref, principal).outcome

    def _count(self, op: str) -> None:
        self.call_counts[op] = self.call_counts.get(op, 0) + 1
