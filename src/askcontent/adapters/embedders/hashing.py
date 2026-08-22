"""Deterministic offline embedder.

Hashed character-n-gram bag projected into a fixed dimension and L2-normalised.
It captures lexical and morphological overlap but no real semantics — it is
*not* a quality embedder and is not intended to be.

Its job is to let the full test suite and the evaluation gate run with no
network, no API key and no model download (the root package's offline rule),
and to keep the mock index's behaviour reproducible.

REPLACE WITH: the embedding provider configured for the deployment, behind this
same port. Note ARC-TEC-14 — the text-generation provider and the embedding
provider are configured independently and are routinely different vendors.
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    model_id = "hashing-ngram-v1"

    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        tokens = _TOKEN.findall(text.lower())
        for token in tokens:
            self._add(vec, token, 1.0)
            # Character trigrams give partial credit for morphology, so
            # "refunded" and "refund" are not orthogonal.
            padded = f"^{token}$"
            for i in range(len(padded) - 2):
                self._add(vec, padded[i : i + 3], 0.35)
        for a, b in zip(tokens, tokens[1:]):
            self._add(vec, f"{a}_{b}", 0.6)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    def _add(self, vec: list[float], key: str, weight: float) -> None:
        digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % self.dimension
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[bucket] += sign * weight

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
