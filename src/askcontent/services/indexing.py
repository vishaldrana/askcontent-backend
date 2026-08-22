"""Building our own index: chunks and vectors, persisted.

This is what was missing. Passage selection used to embed every chunk of every
candidate document **on every question** and throw the vectors away — correct
answers, but the embedding cost paid per question instead of once, no benefit
from content hashing, and an HNSW index that had never held a row.

Three properties this must have, and each is a requirement rather than a
nicety:

  * **Incremental by construction.** Unchanged content costs neither a parse
    nor an embedding call. The text hash includes the parser and chunker
    versions, so a parser upgrade re-embeds exactly the documents whose
    extracted text actually changed — and nothing else.
  * **One distance expression.** The vector written here and the query that
    reads it go through `db.vector_ops`, so the index and the query cannot
    disagree and silently fall back to a sequential scan.
  * **Padded, with the true dimension recorded.** One storage width for every
    model, so two embedding models can coexist during a rebuild.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text

from ..adapters.parsers.registry import parse_document
from ..domain.chunks import CHUNKER_VERSION, chunk_document
from ..domain.documents import DocMetadata, DocRef, ParseHints
from ..domain.ids import file_hash, text_hash
from ..domain.scope import evaluate
from ..db.models import VECTOR_WIDTH
from ..config import settings

S = settings.db_schema

#: The corpus "now". Fixed, matching the retrieval service, so a document's
#: staleness does not depend on when it happened to be indexed.
_NOW = dt.datetime(2026, 8, 22, 12, 0, 0, tzinfo=dt.UTC)


@dataclass
class IndexReport:
    connector: str
    seen: int = 0
    parsed: int = 0
    skipped_unchanged: int = 0
    refused: int = 0
    out_of_scope: int = 0
    unreadable: int = 0
    chunks: int = 0
    embedded: int = 0
    embeddings_reused: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"{self.connector}: {self.parsed} parsed, {self.skipped_unchanged} unchanged, "
            f"{self.chunks} chunks, {self.embedded} embedded "
            f"({self.embeddings_reused} reused), {self.refused} refused"
        )


def _pad(vector: list[float]) -> list[float]:
    """One storage width for every model.

    Shorter vectors are padded and the true dimension is recorded on the row,
    so a deployment can hold two models at once during a rebuild rather than
    having to migrate the column.
    """
    if len(vector) > VECTOR_WIDTH:
        raise ValueError(f"vector of {len(vector)} exceeds the storage width {VECTOR_WIDTH}")
    return vector + [0.0] * (VECTOR_WIDTH - len(vector))


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


class IndexingService:
    def __init__(self, platform, sessions, org_id: uuid.UUID) -> None:
        self.platform = platform
        self.sessions = sessions
        self.org_id = org_id

    def index_connector(self, connector, principal: str = "service", limit: int | None = None) -> IndexReport:
        from .retrieval import scope_population_detailed

        report = IndexReport(connector=connector.connector_id)
        result = scope_population_detailed(self.platform.index, connector)
        population = result.documents
        if result.mapping_failures:
            report.notes.append(
                f"{result.mapping_failures} documents could not be mapped — "
                f"check the field map. " + " | ".join(result.sample_errors)
            )
        embedder = self.platform.embedder

        with self.sessions() as session:
            connector_id = session.execute(text(
                f"SELECT id FROM {S}.connector WHERE org_id = :o AND slug = :s"
            ), {"o": self.org_id, "s": connector.connector_id}).scalar_one()

            # What we already hold, so unchanged content costs nothing.
            existing = {
                row.doc_id: row
                for row in session.execute(text(f"""
                    SELECT doc_id, text_hash, file_hash FROM {S}.document
                    WHERE connector_id = :c
                """), {"c": connector_id}).all()
            }

            for meta in population[: limit or len(population)]:
                report.seen += 1
                decision = evaluate(connector.scope, meta)
                if not decision.in_scope:
                    report.out_of_scope += 1
                    continue

                ref = DocRef(doc_id=meta.doc_id, kb_id=meta.kb_id)
                try:
                    raw = self.platform.repository.fetch(ref, principal)
                except Exception as exc:  # noqa: BLE001
                    # A document we cannot read is recorded, not skipped: a
                    # silent skip is indistinguishable from one that was never
                    # discovered.
                    report.unreadable += 1
                    report.notes.append(f"{meta.doc_id}: {exc}")
                    continue

                fhash = file_hash(raw.blob)
                prior = existing.get(meta.doc_id)
                if prior is not None and prior.file_hash == fhash:
                    report.skipped_unchanged += 1
                    continue

                parsed = parse_document(
                    meta.doc_id, raw.blob, declared_mime=raw.mime,
                    hints=ParseHints(base_url=meta.url), sandbox=False,
                )
                report.parsed += 1

                thash = (
                    text_hash(parsed.full_text(), parsed.parser_id,
                              parsed.parser_version, CHUNKER_VERSION)
                    if not parsed.refused else None
                )

                document_id = self._upsert_document(
                    session, connector_id, meta, raw, parsed, fhash, thash, connector
                )

                if parsed.refused:
                    report.refused += 1
                    continue

                # The text hash covers parser and chunker versions, so an
                # unchanged hash means the chunks *and* their embeddings are
                # still correct.
                if prior is not None and prior.text_hash == thash:
                    report.embeddings_reused += 1
                    continue

                chunks = chunk_document(parsed)
                report.chunks += len(chunks)
                self._replace_chunks(session, connector_id, document_id, chunks)

                vectors = embedder.embed([c.embed_text for c in chunks])
                self._write_embeddings(
                    session, connector_id, chunks, vectors, embedder
                )
                report.embedded += len(chunks)

            session.commit()
        return report

    # -- writes ------------------------------------------------------------

    def _upsert_document(self, session, connector_id, meta: DocMetadata, raw, parsed,
                         fhash, thash, connector=None):
        """Write the document *and* its catalog entry.

        Classification, authority and staleness are computed here rather than at
        query time: they are pure functions of data we already hold, and
        recomputing them per question would make a stored corpus that still
        cannot be browsed, sorted or reviewed without running a query.
        """
        from ..domain.catalog import assign_authority, classify, staleness

        classification = classify(meta, parsed)
        freshness = connector.retrieval.freshness if connector else None
        state = staleness(meta, freshness, _NOW) if freshness else "unknown_age"
        tier, reason = (
            assign_authority(meta, list(connector.authority_rules),
                             connector.authority_pins, state)
            if connector else ("supporting", "no connector context")
        )

        return session.execute(text(f"""
            INSERT INTO {S}.document (
                org_id, connector_id, doc_id, title, url, path, space, owner,
                labels, source_version, source_updated_at, sensitivity,
                acl_principals, mime, size_bytes, file_hash, text_hash,
                parser_id, parser_version, parse_path, parse_quality,
                refusal_reason, extras, doc_type, doc_type_confidence,
                doc_type_source, doc_type_evidence, authority, authority_reason,
                staleness, in_scope, last_seen_at
            ) VALUES (
                :org, :c, :doc, :title, :url, :path, :space, :owner,
                :labels, :ver, :updated, :sens, :acl, :mime, :size, :fh, :th,
                :pid, :pv, :pp, :pq, :rr, :extras, :dtype, :dconf,
                :dsource, :devidence, :authority, :areason, :staleness,
                true, now()
            )
            ON CONFLICT (connector_id, doc_id) DO UPDATE SET
                title = EXCLUDED.title, url = EXCLUDED.url, path = EXCLUDED.path,
                space = EXCLUDED.space, owner = EXCLUDED.owner,
                labels = EXCLUDED.labels, source_version = EXCLUDED.source_version,
                source_updated_at = EXCLUDED.source_updated_at,
                sensitivity = EXCLUDED.sensitivity,
                acl_principals = EXCLUDED.acl_principals,
                mime = EXCLUDED.mime, size_bytes = EXCLUDED.size_bytes,
                file_hash = EXCLUDED.file_hash, text_hash = EXCLUDED.text_hash,
                parser_id = EXCLUDED.parser_id, parser_version = EXCLUDED.parser_version,
                parse_path = EXCLUDED.parse_path, parse_quality = EXCLUDED.parse_quality,
                refusal_reason = EXCLUDED.refusal_reason, extras = EXCLUDED.extras,
                doc_type = EXCLUDED.doc_type,
                doc_type_confidence = EXCLUDED.doc_type_confidence,
                doc_type_source = EXCLUDED.doc_type_source,
                doc_type_evidence = EXCLUDED.doc_type_evidence,
                authority = EXCLUDED.authority,
                authority_reason = EXCLUDED.authority_reason,
                staleness = EXCLUDED.staleness,
                in_scope = true, missing_since = NULL, last_seen_at = now()
            RETURNING id
        """), {
            "org": self.org_id, "c": connector_id, "doc": meta.doc_id,
            "title": meta.title, "url": meta.url, "path": meta.path,
            "space": meta.space, "owner": meta.owner, "labels": list(meta.labels),
            "ver": meta.version, "updated": meta.updated_at,
            "sens": str(meta.sensitivity), "acl": list(meta.acl_principals),
            "mime": raw.mime, "size": len(raw.blob), "fh": fhash, "th": thash,
            "pid": parsed.parser_id, "pv": parsed.parser_version,
            "pp": str(parsed.parse_path),
            "pq": __import__("json").dumps(parsed.quality.model_dump(mode="json")),
            "rr": parsed.refusal_reason,
            # Unmapped source fields, retained verbatim: the field nobody
            # mapped is routinely the one carrying the authority signal.
            "extras": __import__("json").dumps(meta.extras or {}),
            "dtype": str(classification.doc_type),
            "dconf": classification.confidence,
            "dsource": classification.source,
            "devidence": list(classification.evidence),
            "authority": str(tier),
            "areason": reason,
            "staleness": str(state),
        }).scalar_one()

    def _replace_chunks(self, session, connector_id, document_id, chunks) -> None:
        # Replace rather than merge. Chunk ids are content-derived, so a
        # document whose text changed has different chunks, and leaving the old
        # ones would let a citation resolve to text the document no longer says.
        session.execute(text(
            f"DELETE FROM {S}.document_chunk WHERE document_id = :d"
        ), {"d": document_id})
        for chunk in chunks:
            session.execute(text(f"""
                INSERT INTO {S}.document_chunk (
                    org_id, connector_id, document_id, chunk_id, ordinal, text,
                    heading_path, parent_text, page, is_table, token_estimate,
                    chunker_version
                ) VALUES (
                    :org, :c, :d, :cid, :ord, :text, :head, :parent, :page,
                    :table, :tokens, :cv
                )
            """), {
                "org": self.org_id, "c": connector_id, "d": document_id,
                "cid": chunk.chunk_id, "ord": chunk.ordinal, "text": chunk.text,
                "head": list(chunk.heading_path), "parent": chunk.parent_text,
                "page": chunk.page, "table": chunk.is_table,
                "tokens": max(1, len(chunk.text) // 4), "cv": CHUNKER_VERSION,
            })

    def _write_embeddings(self, session, connector_id, chunks, vectors, embedder) -> None:
        for chunk, vector in zip(chunks, vectors):
            session.execute(text(f"""
                INSERT INTO {S}.embedding (
                    org_id, connector_id, kind, ref_id, parent_ref, content_hash,
                    model_id, dimension, vector
                ) VALUES (
                    :org, :c, 'chunk', :ref, :parent, :hash, :model, :dim,
                    CAST(:vec AS vector)
                )
                ON CONFLICT (connector_id, kind, ref_id, model_id) DO UPDATE SET
                    content_hash = EXCLUDED.content_hash,
                    vector = EXCLUDED.vector,
                    dimension = EXCLUDED.dimension
            """), {
                "org": self.org_id, "c": connector_id, "ref": chunk.chunk_id,
                "parent": chunk.doc_id,
                "hash": text_hash(chunk.embed_text, "chunk", "1", CHUNKER_VERSION),
                "model": embedder.model_id,
                # The *true* dimension, not the padded width.
                "dim": embedder.dimension,
                "vec": _literal(_pad(vector)),
            })


def search_chunks(engine, connector_id, query_vector: list[float], k: int = 20) -> list[dict]:
    """kNN over our own chunks, through the same expression the index was built on.

    `vector_ops` owns that expression; if it ever needs a narrowing cast, both
    this query and the migration pick it up together. A query that drifts from
    the index does not error — it silently sequential-scans.
    """
    from ..db.vector_ops import cosine_distance  # noqa: F401  (documents the contract)

    literal = _literal(_pad(query_vector))
    with engine.connect() as connection:
        rows = connection.execute(text(f"""
            SELECT e.ref_id AS chunk_id, e.parent_ref AS doc_id,
                   1 - (e.vector <=> CAST(:q AS vector)) AS score,
                   c.text, c.heading_path, c.parent_text, c.page
            FROM {S}.embedding e
            JOIN {S}.document_chunk c ON c.chunk_id = e.ref_id
                                     AND c.connector_id = e.connector_id
            WHERE e.connector_id = :c AND e.kind = 'chunk'
            ORDER BY e.vector <=> CAST(:q AS vector)
            LIMIT :k
        """), {"q": literal, "c": connector_id, "k": k}).mappings().all()
    return [dict(r) for r in rows]
