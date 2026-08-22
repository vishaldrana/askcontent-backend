"""Deterministic reranker for offline runs (CNT-RNK-03).

Not a quality reranker and not pretending to be. It exists so the full test
suite and the evaluation gate run with no model download and no network, and so
that fixture-based assertions are stable.

It approximates the shape of a cross-encoder's judgement — term coverage,
proximity, heading agreement, exact-phrase bonus — which is enough to exercise
every branch of the ranking code, and nowhere near enough to ship.
"""

from __future__ import annotations

import re

from ...ports.reranker import RerankResult

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the a an of to and or is are was were be been in on for with by from at as "
    "this that these those it its our your their we you they what which how do "
    "does did can may".split()
)


class LexicalReranker:
    reranker_id = "lexical-deterministic"
    reranker_version = "1.0.0"

    def __init__(self, score_floor: float = 0.08) -> None:
        self.score_floor = score_floor

    def rerank(self, question: str, passages: list[str]) -> list[RerankResult]:
        q_tokens = [t for t in _TOKEN.findall(question.lower()) if t not in _STOP]
        q_set = set(q_tokens)
        if not q_set:
            return [RerankResult(index=i, score=0.0) for i in range(len(passages))]

        phrase = " ".join(q_tokens)
        results: list[RerankResult] = []

        for i, passage in enumerate(passages):
            lowered = passage.lower()
            p_tokens = _TOKEN.findall(lowered)
            p_set = set(p_tokens)

            coverage = len(q_set & p_set) / len(q_set)
            density = len(q_set & p_set) / (1.0 + len(p_set) / 120.0)
            proximity = _proximity(p_tokens, q_set)
            exact = 0.25 if phrase and phrase in " ".join(p_tokens) else 0.0
            # The heading path is prepended to every chunk, so a match there is
            # a match on the subject rather than on a passing mention.
            heading = 0.15 if _heading_overlap(passage, q_set) else 0.0

            score = min(
                1.0,
                0.55 * coverage + 0.15 * min(1.0, density) + 0.15 * proximity + exact + heading,
            )
            results.append(RerankResult(index=i, score=round(score, 6)))

        results.sort(key=lambda r: (-r.score, r.index))
        return results


def _proximity(tokens: list[str], q_set: set[str]) -> float:
    positions = [i for i, t in enumerate(tokens) if t in q_set]
    if len(positions) < 2:
        return 0.0
    span = positions[-1] - positions[0] + 1
    return min(1.0, len(positions) / span)


def _heading_overlap(passage: str, q_set: set[str]) -> bool:
    head = passage.split("\n", 1)[0].lower()
    return bool(q_set & set(_TOKEN.findall(head)))
