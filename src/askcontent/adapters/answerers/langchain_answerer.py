"""LangChain adapter for `Answerer` — one adapter, every provider.

Same reasoning as askdb's `langchain_llm.py`, and deliberately the same shape:
`init_chat_model` resolves a provider name to a chat model at runtime, so
moving from OpenAI to Anthropic, Bedrock, Vertex or an OpenAI-compatible
gateway is a configuration change and a `pip install`, not a new file here.

This is one of the few files permitted to import a vendor package. Everything
LangChain-shaped is converted at this boundary; callers see `ports.answerer`
types only, which is what keeps the internal-SDK swap a one-file job.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ...ports.answerer import AnswerChunk, Answerer, Passage
from .prompt import SYSTEM, render

#: The model's contract for "the passages do not answer this". Recognised here
#: rather than shown to the reader: it is a protocol token, not prose.
_NOT_IN_CORPUS = re.compile(r"^\s*NOT_IN_CORPUS\s*:\s*", re.I)
_CITATION = re.compile(r"\[(\d+)\]")

#: Long enough to contain the marker, short enough that the reader does not
#: perceive the delay. "NOT_IN_CORPUS:" is fourteen characters.
_MARKER_WINDOW = 16


class LangChainAnswerer(Answerer):
    name = "langchain"

    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str = "gpt-4.1-2025-04-14",
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 1_200,
        temperature: float | None = 0.0,
    ) -> None:
        try:
            from langchain.chat_models import init_chat_model
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "langchain is not installed — pip install 'askcontent-backend[langchain]', "
                "plus the provider package (langchain-openai, langchain-anthropic, …)"
            ) from exc

        self.model_id = model
        self.provider = provider

        kwargs: dict[str, Any] = {"model": model, "model_provider": provider}
        # Only pass what was configured. `init_chat_model` forwards unknown
        # kwargs to the provider class, where a stray `None` is an error rather
        # than a default on several of them.
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            # An answer that must be checkable should not vary run to run.
            # Determinism is a product property here, not a preference.
            kwargs["temperature"] = temperature

        try:
            self._model = init_chat_model(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surface config errors as ours
            raise RuntimeError(
                f"could not initialise LangChain model '{provider}:{model}': {exc}"
            ) from exc

    async def stream(
        self,
        *,
        question: str,
        passages: Sequence[Passage],
        history: Sequence[tuple[str, str]] = (),
    ) -> AsyncIterator[AnswerChunk]:
        from langchain_core.messages import HumanMessage, SystemMessage

        if not passages:
            # Nothing was retrieved. Calling the model anyway invites it to
            # answer from its own knowledge, which is the single thing it must
            # never do here.
            yield AnswerChunk(
                text="I could not find anything in this knowledgebase that answers that.",
                done=True, supported=False,
            )
            return

        messages = [
            SystemMessage(content=SYSTEM),
            HumanMessage(content=render(question, passages, history)),
        ]

        buffered = ""
        emitted = False
        usage: dict = {}

        async for piece in self._model.astream(messages):
            text = _text_of(piece)
            if hasattr(piece, "usage_metadata") and piece.usage_metadata:
                usage = dict(piece.usage_metadata)
            if not text:
                continue

            buffered += text
            if emitted:
                yield AnswerChunk(text=text)
                continue
            # Hold the opening back until the refusal marker can be recognised,
            # rather than streaming "NOT_IN" to a reader and retracting it.
            if len(buffered) < _MARKER_WINDOW:
                continue
            if _NOT_IN_CORPUS.match(buffered):
                continue  # a refusal; assembled once the stream ends
            emitted = True
            yield AnswerChunk(text=buffered)

        if _NOT_IN_CORPUS.match(buffered):
            reason = _NOT_IN_CORPUS.sub("", buffered).strip()
            yield AnswerChunk(
                text=reason or "The documents in scope do not answer that.",
                done=True, supported=False, usage=usage,
            )
            return

        if not emitted and buffered:
            yield AnswerChunk(text=buffered)

        cited = tuple(sorted({int(n) for n in _CITATION.findall(buffered)}))
        yield AnswerChunk(done=True, supported=True, cited=cited, usage=usage)


def _text_of(piece: Any) -> str:
    """LangChain chunks carry either a string or a list of content blocks.

    Both shapes are normal and provider-dependent; handling only the string one
    works until the day the provider changes and the answer silently arrives
    empty.
    """
    content = getattr(piece, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return ""
