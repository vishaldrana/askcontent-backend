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

from ..domain.decline import declines
from ..domain.figures import strip_unsupported
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
    #: Datapoint numbers the answer used.
    used_data: tuple[int, ...] = ()
    #: Figures attributed to the page or a live reading that are in neither.
    #: Non-empty means the answer did arithmetic and presented the result as
    #: something the reader could look up.
    derived: tuple[str, ...] = ()
    #: The sentences taken out because of it. Reported, not hidden: an answer
    #: silently different from what the model wrote is its own kind of
    #: unattributable.
    removed: tuple[str, ...] = ()
    #: The answer as the reader should see it, when that differs from what was
    #: streamed. `None` when nothing was taken out.
    revised: str | None = None
    #: The answer said the corpus does not hold this. Not supported -- there
    #: is nothing to support -- but not a violation either, and reported as
    #: neither: see `domain/decline.py`.
    declined: bool = False
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
    def __init__(self, answerer, *, relevance_floor: float = 0.34, pool=None) -> None:
        self.answerer = answerer
        self._floor = relevance_floor
        #: Resolves a model id to an answerer. Injected, because building one
        #: means naming a vendor SDK and this layer is not allowed to — the
        #: same rule that keeps every other adapter out of here, and a test
        #: asserts it. Absent, every connector answers with the one model this
        #: service was built with, which is what the product did before
        #: connectors could choose.
        self._pool = pool

    def _for(self, model_id: str | None):
        """The answerer a connector asked for, or the one we were built with."""
        if self._pool is None or not model_id:
            return self.answerer
        return self._pool(model_id, self.answerer)

    async def stream(
        self,
        question: str,
        citations,
        history: Sequence[tuple[str, str]] = (),
        instructions: str = "",
        synonyms: dict[str, tuple[str, ...]] | None = None,
        page=None,
        data=None,
        tone: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[tuple[str, AnswerOutcome | None]]:
        """Yield `(text, None)` while writing, then one final `("", outcome)`."""
        passages = to_passages(citations)
        has_page = page is not None and getattr(page, "usable", False)
        has_data = data is not None and getattr(data, "usable", False)
        offered_data = (
            {p.number for p in data.points} if has_data else set()
        )

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
            [p.text for p in passages]
            + ([page.render()] if has_page else [])
            + ([data.render()] if has_data else []),
            floor=self._floor, synonyms=synonyms,
        )
        if not verdict.covered:
            # Refused before the answerer is even called. Calling it and hoping
            # it declines would be paying for a judgement already made, and
            # would fail open if the model were unavailable.
            where = ["in this knowledgebase"]
            if has_page:
                where.append("on this page")
            if has_data:
                where.append("in the live figures")
            message = (
                "I could not find anything "
                + (", ".join(where[:-1]) + " or " + where[-1] if len(where) > 1 else where[0])
                + " that answers that question."
            )
            for word in message.split(" "):
                yield word + " ", None
            yield "", AnswerOutcome(supported=False, reason=verdict.reason())
            return

        offered = {p.number for p in passages}
        outcome: AnswerOutcome | None = None
        said = ""

        async for chunk in self._for(model).stream(
            question=question, passages=passages, history=history,
            instructions=instructions, page=page if has_page else None,
            data=data if has_data else None, tone=tone,
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
                # A datapoint marker naming a value that was never supplied is
                # the same defect as citing passage 9 of 4, and gets the same
                # answer. Checked against what was offered rather than against
                # a count, because a source returning three values where it
                # returned five yesterday is normal.
                fabricated_data = tuple(sorted(set(chunk.used_data) - offered_data))
                data_claimed = bool(set(chunk.used_data) & offered_data)
                # A figure marked [page] or [d1] has to be *in* the page or the
                # reading. A derived one is a guess at a definition presented
                # as a reading — the reader looks at the screen it names and
                # cannot find it.
                stripped = strip_unsupported(
                    said,
                    sources="\n".join(
                        ([page.render()] if has_page else [])
                        + ([data.render()] if has_data else [])
                    ),
                    question=question,
                )
                # Severity: take out the sentence, keep the answer. The failure
                # is almost always one trailing clause, and rejecting the whole
                # answer costs the reader everything to spare them a redundant
                # figure. The exception is an answer that does not survive the
                # edit — nothing left, or nothing left that says where it came
                # from — which is withheld exactly as before.
                derived = stripped.figures
                gutted = stripped.changed and not stripped.survives
                uncited = (
                    bool(said.strip())
                    and not chunk.cited
                    and not page_claimed
                    and not data_claimed
                )
                # An answer that says the corpus is silent has nothing to
                # cite, and flagging it for citing nothing tells the reader
                # the system doubts its own decline. Only ever reachable on an
                # answer that cited nothing at all, so a grounded answer with
                # one hedging sentence cannot land here.
                declined = uncited and declines(said)
                outcome = AnswerOutcome(
                    supported=(
                        chunk.supported and not invented and not uncited
                        and not fabricated_page and not fabricated_data
                        and not gutted
                    ),
                    cited=chunk.cited,
                    invented=invented,
                    used_page=page_claimed,
                    used_data=tuple(sorted(set(chunk.used_data) & offered_data)),
                    derived=derived,
                    removed=stripped.removed,
                    revised=stripped.kept if stripped.changed else None,
                    declined=declined,
                    reason=(
                        "the answer attributed something to a page that was "
                        "never supplied"
                        if fabricated_page else
                        f"the answer cited live values that were never supplied: "
                        f"{', '.join(f'd{n}' for n in fabricated_data)}"
                        if fabricated_data else
                        f"the answer worked out figures that are not on the page "
                        f"or in the readings it credited, and nothing was left "
                        f"once they were removed: {', '.join(derived)}"
                        if gutted else
                        "the answer cited nothing, so none of it can be checked"
                        if uncited and not declined else None
                    ),
                )

        yield "", outcome or AnswerOutcome(supported=False, reason="answerer produced nothing")
