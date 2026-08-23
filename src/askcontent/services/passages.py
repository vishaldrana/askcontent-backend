"""Passage recovery (CNT-RET-09..13).

The index returns *documents*. Citations need *spans*. So passages are produced
locally: fetch from the ECM, sniff, parse, chunk, select.

This is the stage people do not expect when they hear "we already have a vector
index", and it is why the parsing subsystem is on the critical path of every
PGP answer rather than being a feature of the custom-ingestion path.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..adapters.embedders.hashing import cosine
from ..adapters.parsers.registry import parse_document
from ..domain.chunks import CHUNKER_VERSION, Chunk, chunk_document
from ..domain.documents import (
    DocMetadata,
    DocRef,
    ParseHints,
    ParsePath,
    ParsedDocument,
    ParseQuality,
)


class CacheEntry(BaseModel):
    parsed: ParsedDocument
    chunks: tuple[Chunk, ...]


class PassageCacheStats(BaseModel):
    hits: int = 0
    misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class StoredPassages:
    """Chunks read from our own store, when we have them.

    This is what makes the index worth building. A document that has been
    indexed needs no fetch, no parse and no chunk at query time — the three
    expensive stages — and the vectors were computed once rather than per
    question.

    The fetch-parse-chunk path stays as the fallback, because a document can be
    a candidate before the indexer has reached it, and a question arriving in
    that window should be answered rather than refused.
    """

    def __init__(self, engine, connector_uuid) -> None:
        self._engine = engine
        self._connector = connector_uuid

    def load_many(self, doc_ids: list[str]) -> dict[str, tuple[Chunk, ...]]:
        """Every candidate's chunks in one query.

        One query per document is the same SQL twenty times, and against a
        remote database each is a ~95 ms round trip — two seconds of a
        question's latency spent on nothing but network. The set is bounded by
        the candidate count, so a single `IN` is safe.
        """
        from sqlalchemy import text

        from ..config import settings

        if not doc_ids:
            return {}

        schema = settings.db_schema
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT d.doc_id, c.chunk_id, c.ordinal, c.text, c.heading_path,
                           c.parent_text, c.page, c.is_table, c.vector
                    FROM {schema}.document_chunk c
                    JOIN {schema}.document d ON d.id = c.document_id
                    WHERE c.connector_id = :c AND d.doc_id = ANY(:docs)
                    ORDER BY d.doc_id, c.ordinal
                    """
                ),
                {"c": self._connector, "docs": list(doc_ids)},
            ).mappings().all()

        out: dict[str, list[Chunk]] = {}
        for row in rows:
            out.setdefault(row["doc_id"], []).append(
                Chunk(
                    chunk_id=row["chunk_id"], doc_id=row["doc_id"], text=row["text"],
                    heading_path=tuple(row["heading_path"] or ()),
                    ordinal=row["ordinal"], page=row["page"],
                    is_table=row["is_table"], parent_text=row["parent_text"] or "",
                    vector=list(row["vector"]) if row["vector"] is not None else None,
                )
            )
        return {doc: tuple(chunks) for doc, chunks in out.items()}

    def load(self, doc_id: str) -> tuple[Chunk, ...] | None:
        from sqlalchemy import text

        from ..config import settings

        schema = settings.db_schema
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT c.chunk_id, c.ordinal, c.text, c.heading_path,
                           c.parent_text, c.page, c.is_table, c.vector
                    FROM {schema}.document_chunk c
                    JOIN {schema}.document d ON d.id = c.document_id
                    WHERE c.connector_id = :c AND d.doc_id = :doc
                    ORDER BY c.ordinal
                    """
                ),
                {"c": self._connector, "doc": doc_id},
            ).mappings().all()

        if not rows:
            return None
        return tuple(
            Chunk(
                chunk_id=row["chunk_id"], doc_id=doc_id, text=row["text"],
                heading_path=tuple(row["heading_path"] or ()),
                ordinal=row["ordinal"], page=row["page"],
                is_table=row["is_table"], parent_text=row["parent_text"] or "",
                vector=list(row["vector"]) if row["vector"] is not None else None,
            )
            for row in rows
        )


class PassageService:
    """Fetch → parse → chunk, with a cache keyed on everything that can change
    the output (CNT-RET-10).

    Without this cache every question re-parses every candidate document, and a
    300-page PDF appearing in the candidate set of a common question makes that
    question permanently slow. With it, the second asker pays nothing. It is
    the single largest latency lever in the system.
    """

    def __init__(self, repository, embedder, *, sandbox: bool = False, stored=None) -> None:
        self.repository = repository
        self.embedder = embedder
        self.sandbox = sandbox
        #: Our own indexed chunks. When present they short-circuit the whole
        #: fetch-parse-chunk path.
        self.stored = stored
        self._cache: dict[str, CacheEntry] = {}
        self.stats = PassageCacheStats()
        self.from_store = 0

    def _key(self, metadata: DocMetadata, parser_version: str = "*") -> str:
        # CNT-RET-11: where the ECM exposes no version we fall back to the
        # content hash, which means the fetch cannot be skipped — the cache
        # then saves parsing but not retrieval.
        version = metadata.version or "content-hash"
        return f"{metadata.doc_id}|{version}|{parser_version}|{CHUNKER_VERSION}"

    def prime(self, metadatas: list) -> None:
        """Load every candidate's chunks in one query, into the cache.

        Called before the per-document loop so that the loop finds everything
        already there. Without it the loop issues one query per document, and
        against a remote database that is seconds of pure latency.
        """
        if self.stored is None:
            return
        wanted = [m for m in metadatas if self._key(m) not in self._cache]
        if not wanted:
            return
        loaded = self.stored.load_many([m.doc_id for m in wanted])
        for metadata in wanted:
            chunks = loaded.get(metadata.doc_id)
            if not chunks:
                continue
            self.from_store += 1
            self._cache[self._key(metadata)] = CacheEntry(
                parsed=ParsedDocument(
                    doc_id=metadata.doc_id, blocks=(),
                    parser_id="stored", parser_version="-",
                    parse_path=ParsePath.HTML_TRAFILATURA, quality=ParseQuality(),
                ),
                chunks=chunks,
            )

    def load(self, ref: DocRef, metadata: DocMetadata, principal: str) -> CacheEntry:
        key = self._key(metadata)
        cached = self._cache.get(key)
        if cached is not None:
            self.stats.hits += 1
            return cached

        if self.stored is not None:
            chunks = self.stored.load(metadata.doc_id)
            if chunks:
                # Indexed already: no fetch, no parse, no chunking. The parsed
                # artefact is not reconstructed because nothing downstream reads
                # it — the chunks are the citable unit.
                self.from_store += 1
                entry = CacheEntry(
                    parsed=ParsedDocument(
                        doc_id=metadata.doc_id, blocks=(),
                        parser_id="stored", parser_version="-",
                        parse_path=ParsePath.HTML_TRAFILATURA, quality=ParseQuality(),
                    ),
                    chunks=chunks,
                )
                self._cache[key] = entry
                self.stats.hits += 1
                return entry

        self.stats.misses += 1
        raw = self.repository.fetch(ref, principal)
        parsed = parse_document(
            metadata.doc_id,
            raw.blob,
            declared_mime=raw.mime,
            hints=ParseHints(base_url=metadata.url),
            sandbox=self.sandbox,
        )
        chunks = tuple(chunk_document(parsed))
        entry = CacheEntry(parsed=parsed, chunks=chunks)
        self._cache[key] = entry
        return entry

    def select(
        self,
        question_vector: list[float],
        chunks: tuple[Chunk, ...],
        limit: int,
    ) -> list[tuple[Chunk, float]]:
        """Deterministic within-document selection with a per-document cap
        (CNT-RET-12).

        Without the cap one long, densely relevant document fills the entire
        context budget and the answer silently rests on a single source — which
        reads exactly like a well-grounded answer and is not one.
        """
        if not chunks:
            return []

        # Chunks loaded from the index already carry the vector they were
        # indexed with. Only the ones parsed on the fly — a document not yet
        # indexed — need embedding, and embedding just those turns eighty
        # network calls per question into none in the common case.
        pending = [i for i, c in enumerate(chunks) if c.vector is None]
        fresh = (
            self.embedder.embed([chunks[i].embed_text for i in pending])
            if pending else []
        )
        vectors: list[list[float]] = [c.vector or [] for c in chunks]
        for slot, vector in zip(pending, fresh):
            vectors[slot] = vector

        scored = [
            (chunk, cosine(question_vector, vector))
            for chunk, vector in zip(chunks, vectors)
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0].ordinal))
        return scored[:limit]
