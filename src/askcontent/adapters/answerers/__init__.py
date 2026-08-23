"""Answerer selection.

One decision, made once, from configuration: use the model if it is configured,
and the extractive stand-in if it is not. The important part is that the
fallback is *visible* — `Platform.answering.answerer.name` is reported in the
answer stream, so an offline deployment is never mistaken for the product.
"""

from __future__ import annotations

from ...config import settings
from .extractive import ExtractiveAnswerer


def build_answerer():
    """The configured answerer, or the offline one.

    A missing key is not an error: the test suite runs with no network by
    requirement, and a developer without a key should still get a working
    console. It *is* reported, because an unannounced downgrade to extractive
    answers is how a demo becomes a misunderstanding.
    """
    provider = (settings.answer_provider or "auto").lower()

    if provider in ("extractive", "none", "offline"):
        return ExtractiveAnswerer()

    if provider in ("auto", "anthropic"):
        key = settings.anthropic_api_key
        if not key and provider == "auto":
            return ExtractiveAnswerer()
        try:
            from .anthropic_answerer import AnthropicAnswerer

            return AnthropicAnswerer(api_key=key, model=settings.answer_model)
        except Exception:  # noqa: BLE001
            # An explicitly requested provider that cannot be built is a
            # configuration error worth seeing, but not one worth taking the
            # console down for.
            if provider == "anthropic":
                raise
            return ExtractiveAnswerer()

    raise ValueError(f"unknown answer provider: {provider}")
