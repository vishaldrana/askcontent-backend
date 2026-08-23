"""Embedder selection.

Same shape as the answerer: configuration decides, the fallback is visible, and
the choice is reported so an offline deployment is never mistaken for the
product.
"""

from __future__ import annotations

from ...config import settings
from .hashing import HashingEmbedder


def build_embedder():
    """The configured embedder, or the deterministic offline one.

    The test suite runs with no network by requirement, so a missing key is not
    an error — but it *is* a materially worse product, because a hashed n-gram
    bag can only match documents that repeat the words of the question.
    """
    provider = (settings.embedding_provider or "auto").lower()

    if provider in ("hashing", "none", "offline", "null"):
        return HashingEmbedder()

    key = settings.embedding_api_key or settings.llm_api_key
    if not key and provider == "auto":
        return HashingEmbedder()

    from .openai_embedder import OpenAIEmbedder

    try:
        return OpenAIEmbedder(
            provider="openai" if provider == "auto" else provider,
            model=settings.embedding_model,
            api_key=key,
            dimension=settings.embedding_dim,
        )
    except Exception:
        if provider != "auto":
            raise
        return HashingEmbedder()
