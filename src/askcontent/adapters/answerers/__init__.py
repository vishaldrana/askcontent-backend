"""Answerer selection.

One decision, made once, from configuration — the same `LLM_PROVIDER` /
`LLM_MODEL` / `LLM_API_KEY` triple askdb uses, so a deployment configures both
products the same way.

The important part is that the fallback is *visible*: the answerer's name is
reported in the answer stream and rendered under every answer, because an
unannounced downgrade to extractive answers is how a demo becomes a
misunderstanding.
"""

from __future__ import annotations

from ...config import settings
from .extractive import ExtractiveAnswerer


def build_answerer():
    """The configured answerer, or the offline one.

    A missing key is not an error: the test suite runs with no network by
    requirement, and a developer without a key should still get a working
    console.
    """
    provider = (settings.llm_provider or "auto").lower()

    if provider in ("extractive", "none", "offline", "null"):
        return ExtractiveAnswerer()

    if not settings.llm_api_key and provider == "auto":
        return ExtractiveAnswerer()

    from .langchain_answerer import LangChainAnswerer

    resolved = "openai" if provider == "auto" else provider
    try:
        return LangChainAnswerer(
            provider=resolved,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    except Exception:
        # An explicitly requested provider that cannot be built is a
        # configuration error worth seeing. On "auto" it is a reason to fall
        # back, not to take the console down.
        if provider != "auto":
            raise
        return ExtractiveAnswerer()
