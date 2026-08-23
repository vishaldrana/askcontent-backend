"""What the host's page is allowed to tell the answerer."""

import asyncio
from collections.abc import AsyncIterator

from askcontent.domain.page_context import (
    MAX_SUMMARY_CHARS,
    PageContext,
    from_payload,
)
from askcontent.ports.answerer import AnswerChunk, Passage
from askcontent.services.answering import AnsweringService


# --------------------------------------------------------------- bounds ----

def test_nothing_in_gives_nothing_back():
    assert from_payload(None) is None
    assert from_payload({}) is None
    assert from_payload({"summary": "   "}) is None
    assert from_payload("a string, not an object") is None


def test_a_key_alone_is_enough_to_carry():
    # Step 1 does not use the key. It is accepted now so a host integrating
    # today does not have to change their snippet when the REST source lands.
    context = from_payload({"key": "srv_8f2a11c4"})
    assert context is not None and context.key == "srv_8f2a11c4"
    assert not context.usable


def test_a_long_summary_is_cut_and_says_so():
    context = from_payload({"summary": "word " * 5000})
    assert context is not None
    assert len(context.summary) <= MAX_SUMMARY_CHARS
    assert context.truncated
    assert "truncated" in context.render()


def test_control_characters_do_not_survive():
    # A page emitting control characters is not formatting; it is trying to
    # break out of the block it was put in.
    context = from_payload({"summary": "NPS is 42\x00\x1b[31m rising"})
    assert context is not None
    assert "\x00" not in context.summary
    assert "\x1b" not in context.summary


def test_render_falls_back_to_a_neutral_heading():
    context = PageContext(summary="NPS is 42")
    assert context.render().startswith("The page the reader is on")


# ---------------------------------------------------------------- gates ----

class _Fake:
    """An answerer that says exactly what the test wants said."""

    name = "fake"
    model_id = "fake"

    def __init__(self, text: str, cited=(), used_page=False) -> None:
        self._text, self._cited, self._used_page = text, cited, used_page
        self.saw_page = None

    async def stream(self, *, question, passages, history=(), instructions="",
                     page=None) -> AsyncIterator[AnswerChunk]:
        self.saw_page = page
        yield AnswerChunk(text=self._text)
        yield AnswerChunk(done=True, supported=True, cited=self._cited,
                          used_page=self._used_page)


class _Citation:
    title = "Survey design"
    url = "https://help.example.com/survey-design"
    span = "Net Promoter Score is calculated from the 0-10 recommendation question."
    heading_path = ()
    updated_at = None
    authority = None


def _run(service, question, citations, page=None):
    async def go():
        said, outcome = "", None
        async for text, result in service.stream(question, citations, page=page):
            said += text
            outcome = result or outcome
        return said, outcome

    return asyncio.run(go())


def test_a_page_marker_counts_as_attribution():
    # Without this the uncited gate rejects a perfectly attributed answer whose
    # only source was the screen in front of the reader.
    answerer = _Fake("Your NPS for this survey is 42 [page].", used_page=True)
    _, outcome = _run(
        AnsweringService(answerer), "what is my NPS",
        [_Citation()], page=PageContext(summary="NPS is 42 for survey srv_1"),
    )
    assert outcome.supported
    assert outcome.used_page


def test_claiming_a_page_that_was_never_supplied_is_not_supported():
    # The same defect as citing passage 9 of 4: a source the answer invented.
    # Asked about something the passages *do* cover, so the relevance gate lets
    # it through and the fabricated attribution is what fails it.
    answerer = _Fake("Net Promoter Score is 42 [page].", used_page=True)
    _, outcome = _run(
        AnsweringService(answerer), "how is Net Promoter Score calculated",
        [_Citation()],
    )
    assert not outcome.supported
    assert "never supplied" in outcome.reason


def test_the_page_counts_towards_coverage():
    # The relevance gate refuses what the corpus does not cover. A question
    # about the chart on the screen is exactly that — and refusing it while
    # holding the answer in the request is the behaviour this feature ends.
    answerer = _Fake("The chart shows 42 responses [page].", used_page=True)
    said, outcome = _run(
        AnsweringService(answerer), "how many responses does this chart show",
        [], page=PageContext(summary="This chart shows 42 responses this month."),
    )
    assert outcome.supported, said


def test_a_page_about_something_else_still_refuses():
    answerer = _Fake("...", used_page=False)
    said, outcome = _run(
        AnsweringService(answerer), "what is our parental leave policy",
        [], page=PageContext(summary="This chart shows 42 responses this month."),
    )
    assert not outcome.supported
    assert "on this page" in said


def test_the_page_is_not_handed_over_when_it_is_empty():
    answerer = _Fake("NPS is defined in [1].", cited=(1,))
    _run(AnsweringService(answerer), "how is NPS calculated", [_Citation()],
         page=PageContext(key="srv_1"))
    # Carried a key, showed nothing: there is no block to render, and rendering
    # an empty one tells the model a page exists and says nothing about it.
    assert answerer.saw_page is None
