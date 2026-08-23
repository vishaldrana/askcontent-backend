"""Turning evidence into an answer.

The pipeline is short and every step is a gate:

    retrieve  ->  relevance  ->  ground  ->  answer  ->  verify citations

`retrieve` always returns something, so `relevance` is what stops a question the
corpus does not cover from being answered out of the least-bad matches.
`verify` is what stops the answerer from citing a passage number that was never
supplied, which is the one way a grounded answerer can still mislead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from ..domain.groundedness import assess
from ..ports.answerer import AnswerChunk, Passage


@dataclass
class AnswerOutcome:
    supported: bool
    reason: str | None = None
    cited: tuple[int, ...] = ()
    #: Passage numbers the answer cited that were never offered to it. Always
    #: empty in practice; non-empty is a defect worth alerting on, not hiding.
    invented: tuple[int, ...] = ()


def to_passages(citations) -> list[Passage]:
    """Number the citations for the answerer, in the order they were ranked.

    The number is the contract between the answer and the evidence panel: the
    `[2]` a reader sees in the prose has to be the second card below it.
    """
    return [
        Passage(
            number=i,
            title=c.title,
            url=c.url or "",
            text=c.span,
            heading_path=tuple(c.heading_path or ()),
            updated=c.updated_at.strftime("%d %b %Y") if c.updated_at else None,
            authority=str(c.authority) if c.authority else None,
        )
        for i, c in enumerate(citations, start=1)
    ]


class AnsweringService:
    def __init__(self, answerer, *, relevance_floor: float = 0.34) -> None:
        self.answerer = answerer
        self._floor = relevance_floor

    async def stream(
        self,
        question: str,
        citations,
        history: Sequence[tuple[str, str]] = (),
        instructions: str = "",
    ) -> AsyncIterator[tuple[str, AnswerOutcome | None]]:
        """Yield `(text, None)` while writing, then one final `("", outcome)`."""
        passages = to_passages(citations)

        verdict = assess(question, [p.text for p in passages], floor=self._floor)
        if not verdict.covered:
            # Refused before the answerer is even called. Calling it and hoping
            # it declines would be paying for a judgement already made, and
            # would fail open if the model were unavailable.
            message = (
                "I could not find anything in this knowledgebase that answers "
                "that question."
            )
            for word in message.split(" "):
                yield word + " ", None
            yield "", AnswerOutcome(supported=False, reason=verdict.reason())
            return

        offered = {p.number for p in passages}
        outcome: AnswerOutcome | None = None

        async for chunk in self.answerer.stream(
            question=question, passages=passages, history=history,
            instructions=instructions,
        ):
            if chunk.text:
                yield chunk.text, None
            if chunk.done:
                invented = tuple(sorted(set(chunk.cited) - offered))
                outcome = AnswerOutcome(
                    supported=chunk.supported and not invented,
                    cited=chunk.cited,
                    invented=invented,
                )

        yield "", outcome or AnswerOutcome(supported=False, reason="answerer produced nothing")
