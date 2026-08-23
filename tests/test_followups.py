"""Suggested questions must be answerable, or they should not exist."""

import types

from askcontent.domain.followups import suggest


def _citation(title, heading_path=()):
    return types.SimpleNamespace(title=title, heading_path=heading_path)


def test_suggestions_come_from_documents_that_were_retrieved():
    """The whole point: a suggestion names something the corpus contains, so
    "is this answerable" is a property of construction rather than a hope."""
    out = suggest([_citation("Adding Hyperlink to Survey Text")])
    assert out
    assert "Adding Hyperlink to Survey Text" in out[0].question
    assert "Adding Hyperlink" in out[0].because


def test_a_heading_already_phrased_as_a_question_is_not_re_wrapped():
    """Otherwise: "What is How do I reset my password?"."""
    out = suggest([_citation("Account", heading_path=("Account", "How do I reset my password?"))])
    assert out[0].question == "How do I reset my password?"


def test_the_question_just_asked_is_never_suggested_back():
    out = suggest(
        [_citation("Survey Templates")],
        question="What does the documentation say about Survey Templates?",
    )
    assert all("Survey Templates" not in f.question for f in out)


def test_furniture_headings_are_skipped():
    """"Introduction" and "See also" are page chrome; a question built from one
    reads as a non-question."""
    out = suggest([_citation("Introduction", heading_path=("Introduction", "Overview"))])
    assert not out


def test_duplicates_across_citations_collapse():
    out = suggest([_citation("Terminate"), _citation("Terminate"), _citation("Branching")])
    assert len({f.question for f in out}) == len(out)


def test_the_limit_is_respected():
    citations = [_citation(f"Doc {i}") for i in range(12)]
    assert len(suggest(citations, limit=3)) == 3


def test_numbered_steps_are_not_subjects():
    """Help content uses "6. Click Insert" as a heading constantly, and a
    question built from one reads as a fragment of somebody's instructions."""
    out = suggest([_citation("Hyperlinks", heading_path=("Hyperlinks", "6. Click on Insert"))])
    assert all("Click on Insert" not in f.question for f in out)


def test_a_rephrasing_of_the_question_is_not_suggested():
    """Exact matching is not enough: "How do I add a hyperlink to survey text?"
    and "…about Adding Hyperlink to Survey Text" are one request in two
    outfits, and offering the second back is a dead end that looks like help."""
    out = suggest(
        [_citation("Adding Hyperlink to Survey Text")],
        question="How do I add a hyperlink to survey text?",
    )
    assert all("Hyperlink" not in f.question for f in out)
