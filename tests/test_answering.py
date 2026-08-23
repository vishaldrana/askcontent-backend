"""Answer composition: what may be said, and what must not be."""

import asyncio
import types

from askcontent.adapters.answerers.extractive import ExtractiveAnswerer
from askcontent.ports.answerer import AnswerChunk, Passage
from askcontent.services.answering import AnsweringService


def _citation(title, span, url="https://example.test/doc"):
    return types.SimpleNamespace(
        title=title, span=span, url=url, heading_path=(), updated_at=None,
        authority=None,
    )


def _drain(service, question, citations, history=()):
    async def run():
        text, outcome = "", None
        async for chunk, result in service.stream(question, citations, history):
            text += chunk
            if result is not None:
                outcome = result
        return text, outcome

    return asyncio.run(run())


CITATIONS = [
    _citation("Terminate", "Terminate ends a survey for respondents who do not "
                           "meet your targeting criteria."),
    _citation("Hyperlink", "Select the text, click Insert Link, type the URL."),
]


def test_an_uncovered_question_is_refused_without_calling_the_answerer():
    """The gate must not fail open: if it delegated to the answerer, an
    answerer outage would restore the behaviour it exists to prevent."""

    class Exploding:
        name = "exploding"
        model_id = "v0"

        def stream(self, **_):
            raise AssertionError("the answerer must not be called")

    service = AnsweringService(Exploding())
    text, outcome = _drain(
        service, "How many weeks of paid parental leave do I get?", CITATIONS
    )
    assert not outcome.supported
    assert "could not find" in text.lower()
    assert "parental" in outcome.reason


def test_a_covered_question_is_answered_with_citations():
    service = AnsweringService(ExtractiveAnswerer())
    text, outcome = _drain(service, "What does Terminate do to respondents?", CITATIONS)
    assert outcome.supported
    assert outcome.cited
    assert "[1]" in text


def test_citing_a_passage_that_was_never_offered_is_treated_as_unsupported():
    """A grounded answerer can still mislead in exactly one way: by attributing
    a claim to evidence nobody supplied."""

    class Fabricating:
        name = "fabricating"
        model_id = "v0"

        async def stream(self, **_):
            yield AnswerChunk(text="Respondents are terminated after 14 days [7].")
            yield AnswerChunk(done=True, supported=True, cited=(7,))

    service = AnsweringService(Fabricating())
    _text, outcome = _drain(service, "What does Terminate do to respondents?", CITATIONS)
    assert not outcome.supported
    assert outcome.invented == (7,)


def test_the_extractive_answerer_quotes_rather_than_paraphrases():
    """An extractive answerer that rewrites is an extractive answerer that lies."""
    service = AnsweringService(ExtractiveAnswerer())
    text, _ = _drain(service, "What does Terminate do to respondents?", CITATIONS)
    assert "targeting criteria" in text


def test_no_passages_means_no_answer():
    service = AnsweringService(ExtractiveAnswerer())
    text, outcome = _drain(service, "What does Terminate do?", [])
    assert not outcome.supported
    assert "could not find" in text.lower()


def test_passage_numbers_are_the_contract_with_the_evidence_panel():
    """The `[2]` a reader sees must be the second card below it."""
    from askcontent.services.answering import to_passages

    passages = to_passages(CITATIONS)
    assert [p.number for p in passages] == [1, 2]
    assert passages[0].title == "Terminate"
