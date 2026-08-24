"""One answerer per model, built once and reused.

The model used to come from the environment, so there was one answerer for the
whole process. A connector may now name its own, which means building them on
demand — and *not* building a new one per question: `init_chat_model` opens a
client, and doing that inside a reader's wait, for every question, is latency
nobody asked for.

Keyed by model id rather than by connector, because two connectors on the same
model should share one client. Bounded, because a catalogue somebody keeps
adding to should not become a leak.
"""

from __future__ import annotations

import threading

from ...config import settings

#: More than any deployment will genuinely run at once. The bound exists so a
#: mistake stays a mistake rather than becoming an outage.
LIMIT = 16

_lock = threading.Lock()
_cache: dict[str, object] = {}


def for_model(model_id: str | None, fallback):
    """The answerer for this model, or the deployment default.

    `fallback` is what the platform built at start-up. It is returned for an
    empty model id — which is what "use the deployment default" is stored as —
    and whenever a named model cannot be built, because a connector pointing at
    a model this deployment can no longer reach should degrade to answering
    rather than to failing.
    """
    if not model_id or model_id == getattr(fallback, "model_id", None):
        return fallback

    with _lock:
        existing = _cache.get(model_id)
    if existing is not None:
        return existing

    try:
        from .langchain_answerer import LangChainAnswerer

        built = LangChainAnswerer(
            provider=(settings.llm_provider or "openai").lower().replace("auto", "openai"),
            model=model_id,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    except Exception:  # noqa: BLE001
        return fallback

    with _lock:
        if len(_cache) >= LIMIT:
            _cache.pop(next(iter(_cache)), None)
        _cache[model_id] = built
    return built


def forget() -> None:
    """Drop the cache — used when the catalogue changes."""
    with _lock:
        _cache.clear()
