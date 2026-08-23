"""Reranking by embedding similarity.

Between the two extremes there is a middle that is cheap and works. The
deterministic lexical reranker scores word overlap, so a question phrased
differently from the document loses: "what is Qwary about" ranks "Turn off
Qwary Branding" above the overview page, because the overview says "experience
management platform" and shares almost no vocabulary with the question. A
cross-encoder fixes that properly and costs a GPU and a model download.

This scores the question against each passage with the embedding model already
configured for indexing. It is a bi-encoder, so it is genuinely weaker than a
cross-encoder — it cannot attend across the pair, and near-duplicate passages
score near-identically. It is also enormously better than counting shared
words, and it needs nothing that is not already running.

Scores are cosine similarity mapped from [-1, 1] onto [0, 1], because the
retrieval config carries a `rerank_floor` that a deployment has tuned against a
0..1 scale and a silently different range would quietly change what is cited.
"""

from __future__ import annotations

import math


class EmbeddingReranker:
    reranker_id = "embedding-bi-encoder"

    def __init__(self, embedder) -> None:
        self._embedder = embedder
        self.reranker_version = getattr(embedder, "model_id", "unknown")
        # Cosine mapped to 0..1 puts unrelated text around 0.5, not 0. A floor
        # meant for lexical scores would pass everything, so it is raised to
        # sit just below "plausibly on topic" for this scale.
        self.score_floor = 0.55

    def rerank(self, question: str, texts: list[str]) -> list:
        from ...ports.reranker import RerankResult

        if not texts:
            return []

        query = self._embedder.embed_query(question)
        vectors = self._embedder.embed(list(texts))

        scored = [
            RerankResult(index=i, score=(_cosine(query, v) + 1.0) / 2.0)
            for i, v in enumerate(vectors)
        ]
        scored.sort(key=lambda r: -r.score)
        return scored


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
