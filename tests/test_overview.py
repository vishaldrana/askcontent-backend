"""Corpus overview — the answer to "what can you tell me", from the corpus."""

import types

from askcontent.domain.overview import describe


def doc(title, path):
    return types.SimpleNamespace(title=title, path=path)


CORPUS = [
    doc("Terminate", "/product-guide/survey-branching/terminate"),
    doc("Branching", "/product-guide/survey-branching/branching"),
    doc("SMS Survey", "/product-guide/launch-survey/sms"),
    doc("Email Survey", "/product-guide/launch-survey/email"),
    doc("NPS", "/product-guide/analysis/nps"),
    doc("One-off", "/product-guide/orphan/page"),
]


def test_it_names_only_sections_that_exist():
    """A model asked to describe a knowledgebase writes something plausible
    about subjects it assumes are there. Every name here comes from a path."""
    out = describe("Qwary Help", "", CORPUS)
    assert "survey branching" in out.text
    assert "launch survey" in out.text
    assert "6 documents" in out.text


def test_a_section_of_one_is_a_page_not_a_section():
    """Presenting it as a section implies the corpus is organised around it."""
    assert "orphan" not in describe("Qwary Help", "", CORPUS).text


def test_the_repeated_root_segment_is_skipped():
    """"product-guide" is on every path and describes nothing."""
    assert "product guide" not in describe("Qwary Help", "", CORPUS).text


def test_an_empty_corpus_says_so_rather_than_describing_nothing():
    out = describe("Empty KB", "", [])
    assert "no indexed documents" in out.text


def test_titles_are_the_fallback_when_paths_say_nothing():
    flat = [doc("Refunds", ""), doc("Billing", "")]
    out = describe("Policies", "", flat)
    assert "Refunds" in out.text


def test_glossary_terms_are_offered_when_present():
    out = describe("Qwary Help", "", CORPUS, terms=["NPS", "CSAT"])
    assert "NPS" in out.text and "CSAT" in out.text


def test_the_description_is_included_when_set():
    out = describe("Qwary Help", "Public product documentation", CORPUS)
    assert "Public product documentation" in out.text
