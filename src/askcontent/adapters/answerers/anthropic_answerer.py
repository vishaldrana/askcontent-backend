"""Claude adapter for `Answerer`.

One of only two files permitted to import the Anthropic SDK; everything
vendor-shaped is converted here, and callers see `ports.answerer` types only.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence

from ...ports.answerer import AnswerChunk, Answerer, Passage
from .prompt import SYSTEM, render

#: The model's contract for "the passages do not answer this". Recognised here
#: rather than shown to the user: it is a protocol token, not prose.
_NOT_IN_CORPUS = re.compile(r"^\s*NOT_IN_CORPUS\s*:\s*", re.I)
_CITATION = re.compile(r"\[(\d+)\]")

#: Long enough to contain the marker, short enough that the reader does not
#: perceive the delay. "NOT_IN_CORPUS:" is 14 characters.
_MARKER_WINDOW = 16


class AnthropicAnswerer(Answerer):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-sonnet-5",
        max_tokens: int = 1_500,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "anthropic SDK not installed — pip install 'askcontent-backend[llm]'"
            ) from exc

        # A bare constructor still resolves credentials from the environment,
        # so an unset key here is not by itself an error.
        self._client = (
            anthropic.AsyncAnthropic(api_key=api_key)
            if api_key
            else anthropic.AsyncAnthropic()
        )
        self.model_id = model
        self._max_tokens = max_tokens

    async def stream(
        self,
        *,
        question: str,
        passages: Sequence[Passage],
        history: Sequence[tuple[str, str]] = (),
    ) -> AsyncIterator[AnswerChunk]:
        if not passages:
            # Nothing was retrieved. Asking the model anyway invites it to
            # answer from its own knowledge, which is the one thing it must
            # never do here.
            yield AnswerChunk(
                text="I could not find anything in this knowledgebase that "
                     "answers that.",
                done=True, supported=False,
            )
            return

        buffered = ""
        emitted = False
        usage: dict = {}

        async with self._client.messages.stream(
            model=self.model_id,
            max_tokens=self._max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": render(question, passages, history)}],
        ) as stream:
            async for text in stream.text_stream:
                buffered += text
                if emitted:
                    yield AnswerChunk(text=text)
                    continue
                # The refusal marker is only recognisable at the very start,
                # and only once enough has arrived to tell. Hold back the first
                # few characters rather than streaming "NOT_IN" to a reader and
                # then retracting it.
                if len(buffered) < _MARKER_WINDOW:
                    continue
                if _NOT_IN_CORPUS.match(buffered):
                    continue  # a refusal; assembled after the stream ends
                emitted = True
                yield AnswerChunk(text=buffered)

            final = await stream.get_final_message()
            usage = {
                "input_tokens": final.usage.input_tokens,
                "output_tokens": final.usage.output_tokens,
            }

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
