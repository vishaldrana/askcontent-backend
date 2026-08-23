"""Publishing crawled pages into the index and the store.

WHY THIS EXISTS
===============
A crawl that only records *that* a page exists produces a knowledgebase you can
enumerate and cannot ask anything. The pages have to become documents the normal
pipeline can see, and the normal pipeline reads two systems:

    ContentIndex       (PGP)  — "which documents match", returns ids
    ContentRepository  (ECM)  — "give me that document", returns bytes

So a crawled page is published to both. Nothing downstream is special-cased:
once published, a connector indexes it, retrieval finds it, and a citation
resolves it, by exactly the same code paths that serve content nobody crawled.

REPLACING THIS WITH THE REAL THING
==================================
Here it writes to `ecm_stub`, which stands in for both systems. In production
the two halves separate and neither is a database write:

    ecm_document     ->  POST {ECM_BASE}/v1/spaces/{space}/documents
                         (the store is the system of record; it mints the id,
                         the version, and the ACL from the space's own rules)
    pgp_index_entry  ->  POST {PGP_BASE}/v1/knowledgebases/{kb}/documents
                         (PGP embeds it with *its* model — do not send vectors,
                         send text, or the corpus ends up embedded two ways
                         and the ANN distances stop meaning one thing)

The ordering matters and survives the rewrite: **store first, index second.**
An index entry pointing at a document the store does not have is the one
failure the whole design is built to avoid — a hit that cannot be turned into
a passage. The reverse, a stored document not yet indexed, is invisible for a
few seconds and then correct.

There is a real argument, which the customer will make, that content should
reach PGP through PGP's own ingestion rather than through us. That is the right
end state and this module is not an argument against it — it is what makes a
group's own pages answerable in the meantime, on the same contract.
"""

from __future__ import annotations

import json

from sqlalchemy import text

#: The stub schema stands in for two separate systems. Named once so the
#: rewrite has a single place to start from.
STUB = "ecm_stub"

#: Sources publish a classification in their own vocabulary; the field map is
#: what turns it back into ours. Writing our enum straight into the payload
#: would make the mapping step look unnecessary because the fixture had
#: quietly pre-solved it.
_CLASSIFICATION = {
    "public": "Public",
    "internal": "Internal Use Only",
    "confidential": "Confidential",
    "restricted": "Restricted",
}


class ContentPublisher:
    """Publishes one parsed document into the store, then the index."""

    def __init__(self, engine, embedder) -> None:
        self._engine = engine
        self._embedder = embedder

    def publish(
        self,
        *,
        doc_id: str,
        kb_id: str,
        space: str,
        title: str,
        path: str,
        url: str,
        version: str,
        body: bytes,
        mime: str,
        updated_at,
        labels: list[str] | None = None,
        sensitivity: str = "public",
        acl_principals: list[str] | None = None,
        owner: str = "crawler",
        text_body: str = "",
    ) -> None:
        labels = labels or []
        # A crawled public help centre is readable by everyone who can reach the
        # site. Saying so explicitly beats leaving it null and letting the
        # retrieval gate guess.
        acl_principals = acl_principals or ["group:all-staff"]

        facets = {
            "space": space,
            "labels": labels,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "title": title,
            # URL resolution runs against the *index*: someone pastes a link and
            # the question is which indexed document it names. For a crawl the
            # answer is exact, because the URL is where the page came from.
            "url": url,
            "alt_urls": [],
        }

        with self._engine.begin() as connection:
            # -- the store, first ---------------------------------------------
            connection.execute(text(f"""
                INSERT INTO {STUB}.ecm_document
                    (doc_id, kb_id, title, path, space, owner, labels, sensitivity,
                     acl_principals, version, updated_at, mime, body)
                VALUES (:doc_id, :kb_id, :title, :path, :space, :owner,
                        :labels, :sensitivity, :acl, :version, :updated_at,
                        :mime, :body)
                ON CONFLICT (doc_id) DO UPDATE SET
                    title = EXCLUDED.title, path = EXCLUDED.path,
                    space = EXCLUDED.space, labels = EXCLUDED.labels,
                    sensitivity = EXCLUDED.sensitivity,
                    acl_principals = EXCLUDED.acl_principals,
                    updated_at = EXCLUDED.updated_at, mime = EXCLUDED.mime,
                    body = EXCLUDED.body,
                    -- The version is what tells a consumer the bytes moved, so
                    -- it changes only when they did. A version that ticks on
                    -- every crawl turns an incremental re-index into a full one.
                    version = CASE
                        WHEN {STUB}.ecm_document.body IS DISTINCT FROM EXCLUDED.body
                        THEN EXCLUDED.version
                        ELSE {STUB}.ecm_document.version END
            """), {
                "doc_id": doc_id, "kb_id": kb_id, "title": title, "path": path,
                "space": space, "owner": owner, "labels": labels,
                "sensitivity": sensitivity, "acl": acl_principals,
                # The content hash *is* the version: two crawls that fetch the
                # same bytes produce the same version, on any machine.
                "version": version, "updated_at": updated_at, "mime": mime,
                "body": body,
            })

            # -- then the index -----------------------------------------------
            # Title plus body is what a document-level index embeds. The real
            # PGP would do this itself from the text we hand it.
            vector = self._embedder.embed_query(f"{title}\n\n{text_body}")
            connection.execute(text(f"""
                INSERT INTO {STUB}.pgp_index_entry
                    (doc_id, kb_id, raw_metadata, facets, embedding, stale)
                VALUES (:doc_id, :kb_id, CAST(:raw AS jsonb), CAST(:facets AS jsonb),
                        CAST(:embedding AS vector), false)
                ON CONFLICT (kb_id, doc_id) DO UPDATE SET
                    raw_metadata = EXCLUDED.raw_metadata,
                    facets = EXCLUDED.facets, embedding = EXCLUDED.embedding,
                    stale = false
            """), {
                "doc_id": doc_id, "kb_id": kb_id,
                # The shape a source actually hands over: its own field names,
                # which the field map is what translates. Inventing our names
                # here would hide the mapping step the console exists to expose.
                "raw": json.dumps({
                    "documentId": doc_id, "webui": url, "title": title,
                    "spaceKey": space, "path": path,
                    "lastModified": updated_at.isoformat() if updated_at else None,
                    # A source states its own classification and who may read
                    # it. Omitting them does not make a document unclassified,
                    # it makes it *default*-classified — and the default is
                    # `internal`, which silently puts every page of a public
                    # help centre above a public connector's ceiling.
                    "classification": _CLASSIFICATION.get(sensitivity, "Internal Use Only"),
                    "readGroups": ",".join(acl_principals),
                    "tags": ",".join(labels),
                }),
                "facets": json.dumps(facets),
                "embedding": "[" + ",".join(f"{v:.6f}" for v in vector) + "]",
            })

    def strip_title_suffix(self, kb_id: str, suffix: str) -> int:
        """Drop a site-wide title suffix from everything already published.

        The suffix can only be recognised by looking at the whole set — one
        page titled "... | Product Guide" might genuinely be about the product
        guide — so it is found after the crawl, by which point the documents
        are already in the store. Correcting them in place is cheaper and less
        error-prone than holding 114 pages back until the set is complete.
        """
        if not suffix:
            return 0
        with self._engine.begin() as connection:
            updated = connection.execute(text(f"""
                UPDATE {STUB}.ecm_document
                   SET title = left(title, length(title) - :n)
                 WHERE kb_id = :kb AND title LIKE :pattern
                RETURNING doc_id
            """), {"kb": kb_id, "n": len(suffix), "pattern": f"%{suffix}"}).rowcount
            # The index keeps its own copy of the title, for filtering and for
            # URL resolution. Leaving it stale would make the console and the
            # citation disagree about what a document is called.
            connection.execute(text(f"""
                UPDATE {STUB}.pgp_index_entry e
                   SET facets = jsonb_set(e.facets, '{{title}}', to_jsonb(d.title)),
                       raw_metadata = jsonb_set(e.raw_metadata, '{{title}}', to_jsonb(d.title))
                  FROM {STUB}.ecm_document d
                 WHERE d.doc_id = e.doc_id AND e.kb_id = :kb
            """), {"kb": kb_id})
        return updated
