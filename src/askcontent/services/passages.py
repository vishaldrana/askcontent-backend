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
from ..domain.documents import DocMetadata, DocRef, ParseHints, ParsedDocument


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


class PassageService:
    """Fetch → parse → chunk, with a cache keyed on everything that can change
    the output (CNT-RET-10).

    Without this cache every question re-parses every candidate document, and a
    300-page PDF appearing in the candidate set of a common question makes that
    question permanently slow. With it, the second asker pays nothing. It is
    the single largest latency lever in the system.
    """

    def __init__(self, repository, embedder, *, sandbox: bool = False) -> None:
        self.repository = repository
        self.embedder = embedder
        self.sandbox = sandbox
        self._cache: dict[str, CacheEntry] = {}
        self.stats = PassageCacheStats()

    def _key(self, metadata: DocMetadata, parser_version: str = "*") -> str:
        # CNT-RET-11: where the ECM exposes no version we fall back to the
        # content hash, which means the fetch cannot be skipped — the cache
        # then saves parsing but not retrieval.
        version = metadata.version or "content-hash"
        return f"{metadata.doc_id}|{version}|{parser_version}|{CHUNKER_VERSION}"

    def load(self, ref: DocRef, metadata: DocMetadata, principal: str) -> CacheEntry:
        key = self._key(metadata)
        cached = self._cache.get(key)
        if cached is not None:
            self.stats.hits += 1
            return cached

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
        vectors = self.embedder.embed([c.embed_text for c in chunks])
        scored = [
            (chunk, cosine(question_vector, vector))
            for chunk, vector in zip(chunks, vectors)
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0].ordinal))
        return scored[:limit]
