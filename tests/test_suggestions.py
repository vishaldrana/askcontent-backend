"""What a suggested question has to survive before a reader is offered it."""

from askcontent.domain.suggestions import is_a_question, is_grounded, keep

SOURCE = (
    "Auto loan payments\n"
    "You can set up Autopay from your account, make a one-time payment, or "
    "schedule a payment up to 30 days in advance. Payments by mail should be "
    "sent to Wells Fargo Auto."
)


def test_a_heading_is_not_a_question():
    # Every one of these was offered to a reader by the constructed version.
    assert not is_a_question("Application")
    assert not is_a_question("Rates")
    assert not is_a_question("You have questions, we have answers?")


def test_the_sites_own_question_is_not_the_readers():
    # "How can we help?" is the site asking the reader. Handed back it reads
    # as though they had asked it, which is nonsense.
    assert not is_a_question("How can we help?")
    assert not is_a_question("How may I help you?")


def test_a_real_question_survives():
    assert is_a_question("How do I set up Autopay?")


def test_grounding_is_a_presence_test_not_a_similarity_score():
    assert is_grounded("How do I set up Autopay?", SOURCE)
    # Plausible, adjacent, and not in the text: the corpus cannot answer it.
    assert not is_grounded("What is the interest rate on a mortgage refinance?", SOURCE)


def test_a_restatement_of_the_question_is_dropped():
    kept = keep(
        ["How do I make a one-time payment?", "How do I set up Autopay?"],
        source=SOURCE,
        asked="How do I make a one-time payment on my auto loan?",
    )
    assert kept == ["How do I set up Autopay?"]


def test_duplicates_differing_only_in_wording_are_dropped():
    kept = keep(
        ["How do I set up Autopay?", "How do I Autopay set up?"],
        source=SOURCE,
    )
    assert len(kept) == 1


def test_a_missing_question_mark_is_added():
    assert keep(["How do I set up Autopay"], source=SOURCE) == ["How do I set up Autopay?"]


def test_nothing_survives_an_empty_source():
    assert keep(["How do I set up Autopay?"], source="") == []
