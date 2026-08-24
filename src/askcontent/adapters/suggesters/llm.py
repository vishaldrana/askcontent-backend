"""Writing the follow-ups, from text we already hold.

The model is given passages and asked for questions about them. It is not
asked to be truthful, careful or grounded — it is asked to write like the
person reading, and everything it returns goes through
`domain.suggestions.keep`, which throws away anything whose words are not in
the passages.

That division is the whole design. Constraining a model to be accurate is a
losing game played every request; constraining it to be *fluent* and checking
the output against text we hold is a game we win every time.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("askcontent.suggest")

SYSTEM = """\
You write the questions a person would ask next, having just read the text \
supplied to you.

Rules:

1. Write as the *reader*, in the first person where it is natural: "How do I \
change my address?", not "What does the documentation say about address \
changes?" and not "How can we help?".

2. Ask only about things the text actually covers. Every important word in \
your question must appear in the text; a question about something adjacent is \
thrown away before anyone sees it.

3. One line per question, no numbering, no punctuation before the question, \
nothing else in your reply. Between three and six questions.

4. Never repeat the question that was just asked, and never ask two questions \
that differ only in wording.

5. Never use a heading, a navigation label or a marketing line as a question. \
"You have questions, we have answers" is not a question. "Application" is not \
a question. If a piece of the text is not a subject somebody could ask about, \
skip it and use another.
"""


class LlmSuggester:
    """A small model, called once per answer."""

    def __init__(self, *, provider: str = "openai", model: str = "gpt-5.4-mini",
                 api_key: str | None = None, base_url: str | None = None) -> None:
        from langchain.chat_models import init_chat_model

        kwargs: dict[str, Any] = {"model": model, "model_provider": provider}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.model_id = model
        self._model = init_chat_model(**kwargs)

    def suggest(self, *, source: str, asked: str = "", limit: int = 4) -> list[str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = f"TEXT\n\n{source[:12000]}"
        if asked:
            prompt += f"\n\nTHE READER JUST ASKED\n{asked}"

        try:
            reply = self._model.invoke(
                [SystemMessage(content=SYSTEM), HumanMessage(content=prompt)]
            )
        except Exception as exc:  # noqa: BLE001
            # Suggestions are an invitation, never part of the answer. A model
            # that is slow, rate-limited or unreachable costs the reader a row
            # of chips and nothing else.
            logger.warning("suggester unavailable: %s", exc)
            return []

        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        lines = [re.sub(r"^\s*[-*\d.)\s]+", "", line).strip() for line in text.splitlines()]
        return [line for line in lines if line][: limit * 3]


class NoSuggester:
    """The offline path. Returns nothing, and the caller falls back."""

    model_id = "none"

    def suggest(self, *, source: str, asked: str = "", limit: int = 4) -> list[str]:
        return []
