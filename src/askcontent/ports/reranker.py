"""Reranker port (CNT-RNK-*).

Load-bearing here in a way it is not in a single-index system: this is the only
stage entitled to compare a PGP result against an ECM result against a locally
indexed chunk, because it is the only one that reads the text rather than
consuming a score from a scale it does not own.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RerankResult(BaseModel):
    index: int
    score: float


@runtime_checkable
class Reranker(Protocol):
    reranker_id: str
    reranker_version: str
    score_floor: float

    def rerank(self, question: str, passages: list[str]) -> list[RerankResult]: ...
