"""Cross-encoder reranker (CNT-RNK-02).

The model reads (question, passage) *jointly* and emits a relevance score. That
joint read is the point: it is the only stage in this pipeline entitled to
compare a PGP hit against an ECM hit against a locally indexed chunk, because
every other stage is consuming a score from a scale it does not own.

Default:  BAAI/bge-reranker-v2-m3            Apache-2.0, multilingual, stronger
Fallback: cross-encoder/ms-marco-MiniLM-L-6-v2  Apache-2.0, ~10x faster, EN only

Both weights and the sentence-transformers runtime are Apache-2.0 and recorded
in LICENCES.md. Import is lazy: the runtime is an optional extra, and the test
suite runs offline against LexicalReranker instead (CNT-RNK-03).

DEPLOYMENT NOTE
---------------
Model weights are baked into the image, not fetched at boot. A reranker that
downloads on first request is a reranker that fails on the day the egress rules
change, and it fails as a latency spike rather than an error.
"""

from __future__ import annotations

import logging

from ...ports.reranker import RerankResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
FAST_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    reranker_id = "cross-encoder"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        score_floor: float = 0.05,
        batch_size: int = 32,
        max_pairs: int = 100,
        max_length: int = 512,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.reranker_version = model_name
        self.score_floor = score_floor
        self.batch_size = batch_size
        self.max_pairs = max_pairs
        self.max_length = max_length
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name, max_length=self.max_length, device=self.device
            )
        return self._model

    def rerank(self, question: str, passages: list[str]) -> list[RerankResult]:
        if not passages:
            return []

        # Bounded in pair count (CNT-RNK-04). Beyond the cap the tail keeps its
        # fusion order rather than being dropped, so a large candidate set
        # degrades in quality, not in coverage.
        head = passages[: self.max_pairs]
        model = self._load()
        pairs = [(question, passage) for passage in head]
        scores = model.predict(
            pairs, batch_size=self.batch_size, show_progress_bar=False
        )

        results = [
            RerankResult(index=i, score=float(_sigmoid(s)))
            for i, s in enumerate(scores)
        ]
        # Tail keeps fusion order, scored below the floor so it cannot displace
        # a genuinely reranked passage.
        results.extend(
            RerankResult(index=i, score=-1.0)
            for i in range(len(head), len(passages))
        )
        results.sort(key=lambda r: (-r.score, r.index))
        return results


def _sigmoid(x: float) -> float:
    import math

    # bge-reranker emits logits; ms-marco emits scores in a similar range.
    # Squashing gives one comparable scale, which is what the floor is set on.
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(x)))))
