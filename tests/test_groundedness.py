"""The relevance gate — the thing standing between a corpus and confident nonsense."""

import pytest

from askcontent.domain.groundedness import assess, content_terms

SURVEY_CORPUS = [
    "Terminate allows you to end a survey for respondents who do not meet your "
    "targeting criteria or consent to your terms.",
    "Adding a hyperlink to survey text: select the text, click Insert Link, "
    "type the URL and click Insert.",
    "Qwary Survey Response objects sync into Salesforce with NPS, CSAT and CES "
    "scores for each contact.",
]


def test_a_question_the_corpus_does_not_cover_is_blocked():
    """The failure this whole module exists for: a survey product's help centre
    answering an HR question out of whatever ranked highest."""
    verdict = assess(
        "How many weeks of paid parental leave does a primary caregiver get?",
        SURVEY_CORPUS,
    )
    assert not verdict.covered
    assert "parental" in verdict.missing
    assert "caregiver" in verdict.missing


def test_the_refusal_names_what_was_missing():
    """A refusal nobody can act on is indistinguishable from a bug."""
    verdict = assess("What is our parental leave entitlement?", SURVEY_CORPUS)
    assert not verdict.covered
    assert "parental" in verdict.reason()


def test_a_question_the_corpus_covers_passes():
    verdict = assess("How do I add a hyperlink to survey text?", SURVEY_CORPUS)
    assert verdict.covered
    assert "hyperlink" in verdict.matched


def test_plural_and_singular_are_the_same_word():
    """Otherwise the gate is a spelling test: a corpus saying "surveys" plainly
    covers a question saying "survey"."""
    verdict = assess("survey respondent criteria", SURVEY_CORPUS)
    assert verdict.covered


def test_interrogative_and_stop_words_do_not_count_as_coverage():
    """"How do I get the details of..." is nearly all filler; if that filler
    counted, every question would look covered."""
    assert content_terms("How do I please explain what the details are") == set()


def test_a_question_with_no_subject_is_not_blocked_here():
    """"What is it about?" refers to the conversation, not to a topic. The gate
    has nothing to measure, so it defers rather than refusing."""
    assert assess("What is it about?", SURVEY_CORPUS).covered


@pytest.mark.parametrize("floor,expected", [(0.1, True), (0.9, False)])
def test_the_floor_is_the_only_knob(floor, expected):
    verdict = assess("hyperlink parental leave policy", SURVEY_CORPUS, floor=floor)
    assert verdict.covered is expected
