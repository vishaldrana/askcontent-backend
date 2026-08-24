"""One reranker per (choice, model), built once and reused.

Reranking is chosen per connector rather than per deployment, because the right
answer depends on where the content came from and one process serves both
kinds. That means building rerankers on demand — and not per question: an LLM
reranker opens a client, and a cross-encoder loads a model into memory.

Keyed by choice and model together, so two connectors asking for the same
LLM reranker share one client and a connector that switches models gets a new
one rather than a stale one.
"""

from __future__ import annotations

import logging
import threading

from ...config import settings

logger = logging.getLogger("askcontent.rerank")

LIMIT = 8

_lock = threading.Lock()
_cache: dict[tuple[str, str], object] = {}

#: What a connector may ask for. Closed, unlike the model that goes with it:
#: the system branches on this value, so an unknown one has to be a refusal
#: rather than a string passed hopefully to a factory.
CHOICES = ("index", "llm", "cross-encoder", "embedding", "lexical")


def for_choice(choice: str | None, model: str | None, fallback, embedder=None):
    """The reranker this connector asked for, or the deployment's own.

    `index` returns the fallback and is not a bug: it means the platform
    reranked during search, so the local stage is deliberately skipped and the
    trace says so. Building anything here would be paying twice to be worse —
    the platform's reranker reads the fragment, ours reads an extract of it.
    """
    if not choice or choice not in CHOICES or choice == "index":
        return fallback

    key = (choice, model or "")
    with _lock:
        existing = _cache.get(key)
    if existing is not None:
        return existing

    built = _build(choice, model, fallback, embedder)
    if built is None:
        return fallback

    with _lock:
        if len(_cache) >= LIMIT:
            _cache.pop(next(iter(_cache)), None)
        _cache[key] = built
    return built


def _build(choice: str, model: str | None, fallback, embedder):
    try:
        if choice == "llm":
            from .llm import LlmReranker

            return LlmReranker(
                provider=(settings.llm_provider or "openai").lower().replace("auto", "openai"),
                model=model or "gpt-5.4-mini",
                api_key=settings.llm_api_key,
                # Ranking degrades to the deployment's own reranker rather than
                # collapsing to fusion order if the model is unreachable
                # mid-query.
                fallback=fallback,
            )
        if choice == "cross-encoder":
            from .cross_encoder import CrossEncoderReranker

            return CrossEncoderReranker(model or "BAAI/bge-reranker-v2-m3")
        if choice == "embedding":
            if embedder is None:
                return None
            from .embedding import EmbeddingReranker

            return EmbeddingReranker(embedder)
        if choice == "lexical":
            from .lexical import LexicalReranker

            return LexicalReranker()
    except Exception as exc:  # noqa: BLE001
        # A connector pointing at a reranker this deployment cannot build
        # should degrade to ranking rather than to failing. Logged, because
        # silently answering with a worse ranking is the kind of thing that
        # goes unnoticed for a quarter.
        logger.warning("reranker %s unavailable (%s); using the deployment default", choice, exc)
    return None


def forget() -> None:
    with _lock:
        _cache.clear()
