"""Question routing — the class of question decides which machinery answers it."""

import pytest

from askcontent.domain.question_kind import QuestionKind, classify


@pytest.mark.parametrize("question", [
    "What all can you tell me?",
    "what can you tell me",
    "What do you know?",
    "What is this for?",
    "What's in here?",
    "What topics do you cover?",
    "What should I ask?",
    "How can you help?",
    "Who are you?",
    "help",
])
def test_questions_about_the_corpus_are_recognised(question):
    """Running these through retrieval produces a refusal on a question the
    system can answer perfectly well — and it is the first thing many people
    type, so it is the first impression the product makes."""
    assert classify(question) is QuestionKind.SCOPE


@pytest.mark.parametrize("question", [
    "What can I tell my customers about NPS scores?",
    "What do you know about survey branching and terminate rules?",
    "How do I add a hyperlink to survey text?",
    "What is the refund policy for enterprise contracts?",
])
def test_a_real_question_that_merely_contains_the_words_is_not_hijacked(question):
    """"What can I tell my customers about NPS" has subjects of its own. The
    check is a conjunction — matching a phrasing is not enough — precisely so
    that a keyword cannot capture a genuine question."""
    assert classify(question) is QuestionKind.CONTENT


@pytest.mark.parametrize("question", ["hi", "Hello!", "thanks", "Good morning", "ok"])
def test_greetings_are_their_own_kind(question):
    """Answering a greeting from documents is absurd; refusing it is rude in a
    way people remember."""
    assert classify(question) is QuestionKind.SOCIAL


def test_a_greeting_with_a_question_attached_is_a_question():
    assert classify("hi, how do I add a hyperlink?") is QuestionKind.CONTENT


def test_one_subject_still_counts_as_orientation():
    """"What can you tell me about surveys" is a request to orient within a
    topic, and is better served by an overview than by a refusal."""
    assert classify("What can you tell me about surveys?") is QuestionKind.SCOPE


def test_an_empty_question_is_not_special_cased():
    assert classify("") is QuestionKind.CONTENT


def test_the_question_asked_right_after_a_refusal_is_a_scope_question():
    """"What can you answer then?"

    The worst possible moment to refuse twice. Somebody has just been told the
    corpus does not cover what they asked, and they are asking what it *does*
    cover — which is the one question the system can always answer.
    """
    assert classify("what can you answer then?") is QuestionKind.SCOPE
    assert classify("what else can you answer") is QuestionKind.SCOPE
    assert classify("what else do you know") is QuestionKind.SCOPE
    assert classify("what questions can i ask") is QuestionKind.SCOPE


def test_widening_the_verbs_did_not_swallow_real_questions():
    # Each of these contains a scope verb and a subject of its own.
    assert classify("what can I tell my customers about NPS?") is QuestionKind.CONTENT
    assert (
        classify("what can you find about overdraft fees on a checking account")
        is QuestionKind.CONTENT
    )
    assert classify("Can I refinance auto loans?") is QuestionKind.CONTENT
