"""PGP, backed by the `ecm_stub` schema.

WHAT THIS STANDS IN FOR
=======================
The same system `mock_pgp.py` stands in for, but reachable over the network and
answering real SQL. Where the mock proves the *shape* of the design, this proves
the parts the mock cannot: that the scope predicate really can be pushed into
the query, that a kNN search really runs against an ANN index, and that the
distance expression really matches the one the index was built on.

REPLACING THIS ADAPTER
======================
  list_knowledgebases()  ->  GET  {PGP_BASE}/v1/knowledgebases
  describe(kb_id)        ->  GET  {PGP_BASE}/v1/knowledgebases/{kb}/schema
  search(...)            ->  POST {PGP_BASE}/v1/knowledgebases/{kb}/search
  list_documents(...)    ->  GET  {PGP_BASE}/v1/knowledgebases/{kb}/documents

The open questions in `mock_pgp.py` all still stand. This adapter answers one of
them optimistically — it accepts metadata filters in the query — because the
stub is a database we control. **If the real PGP is kNN-only, the filters below
cannot be pushed down**, and the scope predicate has to be enforced against a
local metadata store instead. That is the single most important thing to settle
before this file is rewritten against the real service.
"""

from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import text

from ...ports.content_index import (
    FieldSample,
    IndexFilters,
    IndexHit,
    IndexPage,
    IndexUnavailable,
    KnowledgeBaseDescriptor,
)

# The knowledgebase display names live with the fixture, not in the index.
_KB_NAMES = {
    "kb-hr-policies": ("HR Policies", "People Operations policy library", True),
    "kb-eng-runbooks": ("Engineering Runbooks", "On-call runbooks and decision records", True),
    "kb-fin-controls": ("Finance Controls", "SOX control narratives and finance policy", True),
    "kb-legal-holds": ("Legal Holds", "Litigation holds and matter instructions", True),
    "kb-marketing-web": ("Public Web", "Published marketing and trust pages", False),
}


class PgPgpIndex:
    def __init__(self, engine, embedder) -> None:
        self._engine = engine
        self._embedder = embedder

    # -- capability listing ------------------------------------------------

    def list_knowledgebases(self) -> list[KnowledgeBaseDescriptor]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT kb_id, count(*) AS documents
                    FROM ecm_stub.pgp_index_entry GROUP BY kb_id ORDER BY kb_id
                    """
                )
            ).all()
            return [
                self._descriptor(connection, row.kb_id, row.documents) for row in rows
            ]

    def describe(self, kb_id: str) -> KnowledgeBaseDescriptor:
        with self._engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM ecm_stub.pgp_index_entry WHERE kb_id = :kb"),
                {"kb": kb_id},
            ).scalar_one()
            if not count:
                raise KeyError(f"unknown knowledgebase: {kb_id}")
            return self._descriptor(connection, kb_id, count)

    def _descriptor(self, connection, kb_id: str, count: int) -> KnowledgeBaseDescriptor:
        name, description, exposes_acl = _KB_NAMES.get(
            kb_id, (kb_id, "", False)
        )
        return KnowledgeBaseDescriptor(
            kb_id=kb_id,
            name=name,
            description=description,
            document_count=count,
            last_indexed_at=dt.datetime(2026, 8, 22, 3, 0, 0),
            embedding_model="pgp-stub-hashed-ngram",
            embedding_dimension=384,
            exposes_acl=exposes_acl,
            fields=self._field_samples(connection, kb_id, count),
        )

    def _field_samples(self, connection, kb_id: str, total: int) -> tuple[FieldSample, ...]:
        """Coverage and live samples, computed over the real rows.

        This is what the mapping editor renders (CNT-MAP-03). Note that it is
        derived from the data rather than declared: a knowledgebase whose date
        field is present on 40% of documents shows as 0.40, and a required field
        below the threshold blocks activation.
        """
        rows = connection.execute(
            text(
                """
                SELECT f.key AS name,
                       count(*) AS present,
                       count(DISTINCT f.value::text) AS distinct_values,
                       (array_agg(f.value::text ORDER BY e.doc_id))[1:3] AS samples
                FROM ecm_stub.pgp_index_entry e,
                     LATERAL jsonb_each(e.raw_metadata) f
                WHERE e.kb_id = :kb
                GROUP BY f.key ORDER BY f.key
                """
            ),
            {"kb": kb_id},
        ).all()
        return tuple(
            FieldSample(
                name=row.name,
                observed_type="string",
                coverage=row.present / total if total else 0.0,
                distinct_estimate=row.distinct_values,
                samples=tuple(s.strip('"')[:80] for s in (row.samples or [])),
            )
            for row in rows
        )

    # -- search ------------------------------------------------------------

    def search(
        self,
        kb_id: str,
        query: str,
        filters: IndexFilters,
        k: int = 20,
        cursor: str | None = None,
    ) -> IndexPage:
        vector = self._embedder.embed_query(query)
        literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"

        # Filters go *into* the query (CNT-SCP-14). Post-filtering would mean
        # excluded documents influenced ranking and occupied the k budget.
        #
        # They are evaluated against the index's OWN facet copy (`e.facets`),
        # never against the store. Filtering by joining to the store cannot
        # return an identifier the store has dropped, which silently removes the
        # stale-index signal — and that signal is the only warning that a sync
        # is broken. The index's copy may lag; that is not a defect here, it is
        # the reason the resolution gate re-checks against the ECM (CNT-RET-08).
        clauses = ["e.kb_id = :kb", "e.embedding IS NOT NULL"]
        params: dict[str, object] = {"kb": kb_id, "q": literal, "k": k}

        if filters.spaces:
            clauses.append("e.facets->>'space' = ANY(:spaces)")
            params["spaces"] = list(filters.spaces)
        if filters.labels_any:
            clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(e.facets->'labels') l"
                " WHERE l = ANY(:labels_any))"
            )
            params["labels_any"] = list(filters.labels_any)
        if filters.labels_none:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(e.facets->'labels') l"
                " WHERE l = ANY(:labels_none))"
            )
            params["labels_none"] = list(filters.labels_none)
        if filters.updated_after:
            clauses.append(
                "(e.facets->>'updated_at' IS NULL"
                " OR (e.facets->>'updated_at')::timestamptz >= :after)"
            )
            params["after"] = filters.updated_after
        if filters.updated_before:
            clauses.append(
                "(e.facets->>'updated_at' IS NULL"
                " OR (e.facets->>'updated_at')::timestamptz <= :before)"
            )
            params["before"] = filters.updated_before

        offset = int(cursor) if cursor else 0
        params["offset"] = offset

        # No join at all. The index is a separate system; reaching into the
        # store from here is the mistake this whole file exists to avoid.
        sql = f"""
            SELECT e.doc_id, e.raw_metadata,
                   1 - (e.embedding <=> CAST(:q AS vector)) AS score
            FROM ecm_stub.pgp_index_entry e
            WHERE {' AND '.join(clauses)}
            ORDER BY e.embedding <=> CAST(:q AS vector), e.doc_id
            LIMIT :k OFFSET :offset
        """

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(text(sql), params).all()
        except Exception as exc:  # noqa: BLE001
            raise IndexUnavailable(f"PGP search failed for {kb_id}: {exc}") from exc

        hits = tuple(
            IndexHit(
                doc_id=row.doc_id,
                kb_id=kb_id,
                score=float(row.score),
                metadata=row.raw_metadata
                if isinstance(row.raw_metadata, dict)
                else json.loads(row.raw_metadata),
            )
            for row in rows
            if row.score > 0.02
        )
        next_cursor = str(offset + len(rows)) if len(rows) == k else None
        return IndexPage(hits=hits, cursor=next_cursor, total_estimate=None)

    def list_documents(
        self, kb_id: str, cursor: str | None = None, page_size: int = 500
    ) -> IndexPage:
        offset = int(cursor) if cursor else 0
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT doc_id, raw_metadata FROM ecm_stub.pgp_index_entry
                    WHERE kb_id = :kb ORDER BY doc_id LIMIT :limit OFFSET :offset
                    """
                ),
                {"kb": kb_id, "limit": page_size, "offset": offset},
            ).all()
            total = connection.execute(
                text("SELECT count(*) FROM ecm_stub.pgp_index_entry WHERE kb_id = :kb"),
                {"kb": kb_id},
            ).scalar_one()

        return IndexPage(
            hits=tuple(
                IndexHit(
                    doc_id=row.doc_id,
                    kb_id=kb_id,
                    score=0.0,
                    metadata=row.raw_metadata
                    if isinstance(row.raw_metadata, dict)
                    else json.loads(row.raw_metadata),
                )
                for row in rows
            ),
            cursor=str(offset + len(rows)) if offset + len(rows) < total else None,
            total_estimate=total,
        )

    # -- URL resolution ----------------------------------------------------

    def resolve_urls(
        self, urls: list[str], kb_ids: tuple[str, ...] = ()
    ) -> dict[str, list[dict]]:
        """Which indexed document does each URL name?

        One request for the whole batch, which is only possible because this
        index filters on the URL field. Where PGP cannot, every URL degrades to
        rung 5/6 and the paste screen becomes review work — see the port's
        open question.

        Rungs 1, 2, 4, 5 and 6 are implemented here. Rung 3 (redirect) needs a
        network call the platform deliberately does not make: following a
        redirect means fetching, and this feature exists precisely so that
        nothing is fetched. A deployment that wants it resolves redirects in the
        crawler, not here.
        """
        from ...domain.urls import Candidate, Rung, normalise
        from ...services.url_resolution import DEFAULT_POLICY

        if not urls:
            return {}

        keys = {url: normalise(url, DEFAULT_POLICY) for url in urls}
        slugs = {
            url: (key.rstrip("/").rsplit("/", 1)[-1] or "").lower()
            for url, key in keys.items()
        }

        clauses = ["e.facets ? 'url'"]
        params: dict[str, object] = {}
        if kb_ids:
            clauses.append("e.kb_id = ANY(:kbs)")
            params["kbs"] = list(kb_ids)

        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT e.doc_id, e.kb_id,
                           e.facets->>'title' AS title,
                           e.facets->>'url'   AS url,
                           coalesce(
                             (SELECT array_agg(a) FROM jsonb_array_elements_text(
                                 coalesce(e.facets->'alt_urls', '[]'::jsonb)) a),
                             '{{}}'::text[]) AS alt_urls
                    FROM ecm_stub.pgp_index_entry e
                    WHERE {' AND '.join(clauses)}
                    """
                ),
                params,
            ).all()

        # Built once for the batch. A per-URL query would be one round trip per
        # pasted link, and people paste eighty at a time.
        by_url: dict[str, list] = {}
        by_alt: dict[str, list] = {}
        by_title: dict[str, list] = {}
        for row in rows:
            by_url.setdefault(normalise(row.url, DEFAULT_POLICY), []).append(row)
            for alt in row.alt_urls or []:
                by_alt.setdefault(normalise(alt, DEFAULT_POLICY), []).append(row)
            by_title.setdefault(_slugify(row.title), []).append(row)

        out: dict[str, list[dict]] = {}
        for url in urls:
            key = keys[url]
            found: list[Candidate] = []

            for row in by_url.get(key, []):
                found.append(_candidate(row, Rung.EXACT))
            if not found:
                for row in by_alt.get(key, []):
                    found.append(_candidate(row, Rung.ALIAS))
            if not found:
                # Path match: same document, different query string entirely.
                path = "//" + key.split("//", 1)[-1].split("?", 1)[0] if "//" in key else key
                for row in by_url.get(path.split("?")[0], []):
                    found.append(_candidate(row, Rung.PATH))
            if not found and slugs[url]:
                for row in by_title.get(_slugify(slugs[url]), []):
                    found.append(_candidate(row, Rung.TITLE, 1.0))
            if not found and slugs[url]:
                for row in self._search_slug(slugs[url], kb_ids):
                    found.append(_candidate(row, Rung.SEARCH, 1.0))

            out[url] = [c.model_dump() for c in found]
        return out

    def _search_slug(self, slug: str, kb_ids: tuple[str, ...]):
        terms = [t for t in slug.replace("-", " ").replace("_", " ").split() if len(t) > 2]
        if not terms:
            return []
        clauses = ["e.facets ? 'title'"]
        params: dict[str, object] = {"q": " | ".join(terms)}
        if kb_ids:
            clauses.append("e.kb_id = ANY(:kbs)")
            params["kbs"] = list(kb_ids)
        with self._engine.connect() as connection:
            return connection.execute(
                text(
                    f"""
                    SELECT e.doc_id, e.kb_id, e.facets->>'title' AS title,
                           e.facets->>'url' AS url, '{{}}'::text[] AS alt_urls
                    FROM ecm_stub.pgp_index_entry e
                    WHERE {' AND '.join(clauses)}
                      AND to_tsvector('english', e.facets->>'title')
                          @@ to_tsquery('english', :q)
                    ORDER BY e.doc_id LIMIT 5
                    """
                ),
                params,
            ).all()


def _slugify(value: str) -> str:
    import re

    return "-".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _candidate(row, rung, rung_score: float = 1.0):
    from ...domain.urls import Candidate

    return Candidate(
        doc_id=row.doc_id, kb_id=row.kb_id, title=row.title or row.doc_id,
        url=row.url or "", rung=rung, rung_score=rung_score,
    )
