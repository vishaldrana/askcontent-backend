"""Eval expectations — the assertions the suite is made of."""

from askcontent.domain.expectations import Expectation, Outcome, check


def answered(text="Go to Builder and click Insert Link.", cited=("Adding Hyperlink",)):
    return Outcome(answer=text, grounded=True, cited=cited)


def refused(text="I could not find anything that answers that."):
    return Outcome(answer=text, grounded=False, cited=())


def test_answers_fails_when_the_system_refused():
    assert check([Expectation("answers")], refused())


def test_refuses_fails_when_the_system_answered():
    """The direction that matters: a miss is visible to the reader, an
    invention is not."""
    failures = check([Expectation("refuses")], answered())
    assert failures and "answered" in failures[0]


def test_cites_is_not_satisfied_by_merely_retrieving():
    """`cited` holds what the answer rested on, not everything considered —
    otherwise this assertion passes for a ranking change that swapped the
    source out from under a still-plausible answer."""
    assert check([Expectation("cites", "Terminate")], answered(cited=("Branching",)))
    assert not check([Expectation("cites", "Branching")], answered(cited=("Branching",)))


def test_says_is_literal_and_case_insensitive():
    assert not check([Expectation("says", "insert link")], answered())
    assert check([Expectation("says", "Insert Hyperlink")], answered())


def test_says_keeps_punctuation_because_figures_are_the_point():
    """"£1,500" and "£1500" are different claims. A comparison that folded
    punctuation would keep passing the day the number changed."""
    outcome = answered(text="The fee is £1,500 per quarter.")
    assert not check([Expectation("says", "£1,500")], outcome)
    assert check([Expectation("says", "£1500")], outcome)


def test_does_not_say_catches_a_known_wrong_answer():
    outcome = answered(text="Parental leave is 12 weeks.")
    assert check([Expectation("does_not_say", "12 weeks")], outcome)


def test_every_failure_is_reported_not_just_the_first():
    """A case wrong in three ways should not need three re-runs."""
    failures = check(
        [Expectation("answers"), Expectation("says", "Builder"), Expectation("cites", "X")],
        refused(),
    )
    assert len(failures) == 3


def test_an_unknown_expectation_fails_loudly():
    """Silently passing an assertion nobody implemented is how a suite becomes
    decorative."""
    assert check([Expectation("vibes", "good")], answered())
