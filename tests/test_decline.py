"""A decline is not a violation."""

from askcontent.domain.decline import declines


def test_the_answer_that_says_the_corpus_is_silent():
    assert declines("The passages do not mention whether you can refinance auto loans.")
    assert declines("The retrieved documents do not mention refinancing.")
    assert declines("I could not find anything in this knowledgebase that answers that.")
    assert declines("There is no information about refinancing on these pages.")
    assert declines("Overdraft limits are not stated in the documents provided.")


def test_a_real_answer_is_not_a_decline():
    assert not declines("You can pay by phone, by mail, or online.")
    # A hedge about the subject is not a statement about the corpus.
    assert not declines("Rates may vary and are not guaranteed.")
    assert not declines("Autopay does not include the final payment.")
    # The same verbs, about the world rather than about the corpus. This is
    # the pair the detector exists to keep apart.
    assert not declines("The policy does not cover flood damage.")
    assert not declines("A standard account does not provide overdraft protection.")


def test_a_decline_needs_its_subject_in_the_same_sentence():
    assert not declines(
        "The policy does not cover that. The documents describe the rest in full."
    )
