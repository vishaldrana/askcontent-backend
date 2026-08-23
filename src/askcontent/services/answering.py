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
    #: True when the answer rests, in part or whole, on what the host's page
    #: is showing rather than on a document.
    used_page: bool = False
    #: Set when the answerer itself failed — a timeout, a rate limit, an
    #: outage.
    #:
    #: Distinct from `supported=False`, and the distinction is the whole point.
    #: Both produce no answer, but one means "the corpus does not cover this"
    #: and the other means "we could not ask". An eval suite that reports the
    #: second as the first sends somebody to write content for a question that
    #: was never actually put.
    error: str | None = None


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
        synonyms: dict[str, tuple[str, ...]] | None = None,
        page=None,
    ) -> AsyncIterator[tuple[str, AnswerOutcome | None]]:
        """Yield `(text, None)` while writing, then one final `("", outcome)`."""
        passages = to_passages(citations)
        has_page = page is not None and getattr(page, "usable", False)

        # The page counts towards coverage, and it has to.
        #
        # The relevance gate exists to refuse questions the corpus does not
        # cover. A question about the chart on the screen is exactly that kind
        # of question, and refusing it while holding the answer in the request
        # is the behaviour this whole feature exists to end. So the page's text
        # is assessed alongside the passages: if the question is about neither,
        # it is still refused.
        verdict = assess(
            question,
            [p.text for p in passages] + ([page.render()] if has_page else []),
            floor=self._floor, synonyms=synonyms,
        )
        if not verdict.covered:
            # Refused before the answerer is even called. Calling it and hoping
            # it declines would be paying for a judgement already made, and
            # would fail open if the model were unavailable.
            message = (
                "I could not find anything in this knowledgebase"
                + (" or on this page" if has_page else "")
                + " that answers that question."
            )
            for word in message.split(" "):
                yield word + " ", None
            yield "", AnswerOutcome(supported=False, reason=verdict.reason())
            return

        offered = {p.number for p in passages}
        outcome: AnswerOutcome | None = None
        said = ""

        async for chunk in self.answerer.stream(
            question=question, passages=passages, history=history,
            instructions=instructions, page=page if has_page else None,
        ):
            if chunk.text:
                said += chunk.text
                yield chunk.text, None
            if chunk.done:
                invented = tuple(sorted(set(chunk.cited) - offered))
                # An answer with prose and no citations at all is not a
                # grounded answer, whatever it claims. It may even be correct —
                # the one that prompted this check was — but nothing in it can
                # be followed back to a document, which is the entire promise.
                # Checked here rather than asked for in the prompt, because a
                # rule the model can quietly stop following is not a rule.
                # `[page]` counts as attribution, but only when a page was
                # actually supplied. An answer claiming to quote a page that
                # was never sent is inventing a source, which is the same
                # defect as citing passage 9 of 4 and is treated the same way.
                page_claimed = chunk.used_page and has_page
                fabricated_page = chunk.used_page and not has_page
                uncited = bool(said.strip()) and not chunk.cited and not page_claimed
                outcome = AnswerOutcome(
                    supported=(
                        chunk.supported and not invented and not uncited
                        and not fabricated_page
                    ),
                    cited=chunk.cited,
                    invented=invented,
                    used_page=page_claimed,
                    reason=(
                        "the answer attributed something to a page that was "
                        "never supplied"
                        if fabricated_page else
                        "the answer cited nothing, so none of it can be checked"
                        if uncited else None
                    ),
                )

        yield "", outcome or AnswerOutcome(supported=False, reason="answerer produced nothing")
