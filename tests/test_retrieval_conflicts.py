"""Conflict detection must not cry wolf."""

from askcontent.services.retrieval import _RELATIVE_AGE


def test_relative_age_footers_are_not_claims():
    """A help centre footers every page with "Last updated N years ago". Two
    such pages were being reported as sources disagreeing about a quantity —
    and a conflict panel that fires on furniture is one nobody reads on the day
    two documents really do disagree."""
    for line in (
        "Last updated 3 years ago",
        "last updated 2 years ago",
        "Updated 6 months ago",
        "Last reviewed 14 days ago",
    ):
        assert _RELATIVE_AGE.sub(" ", line).strip() == "", line


def test_a_real_quantity_survives():
    text = "An agent must be given 10 days notice before the account is closed."
    assert "10 days" in _RELATIVE_AGE.sub(" ", text)


# --------------------------------------------------------------- measures ---

from askcontent.services.retrieval import _measure  # noqa: E402


def test_the_unit_alone_does_not_say_what_is_measured():
    # The two that produced "Sources disagree — days your (days)" over a page
    # about due-date criteria and a page about autopay timing. Same unit,
    # different measure, no contradiction.
    assert _measure("days", " past due, your account is current") != _measure(
        "days", " before the automatic payment is scheduled"
    )


def test_the_same_measure_is_recognised_through_the_filler():
    assert _measure("days", " past due") == _measure("days", " past due and the")


def test_a_measure_ignores_words_that_qualify_nothing():
    # "your", "the", "will" say nothing about what is being counted.
    assert _measure("days", " your notice period") == "days notice period"


def test_currency_takes_its_measure_from_what_follows():
    assert _measure("currency", " monthly maintenance fee") == "currency monthly maintenance"


# ------------------------------------------------------------- end to end ---

def _Cite(doc_id, span, space=None):
    """A real Citation — the detector builds pydantic Conflicts from these."""
    from askcontent.services.retrieval import Citation

    return Citation(
        chunk_id=f"c-{doc_id}", doc_id=doc_id, title=doc_id, url="",
        space=space, owner=None, authority="supporting", updated_at=None,
        staleness="fresh", heading_path=(), span=span,
        rerank_score=0.5, fusion_rank=1,
    )


def _conflicts(cites):
    from askcontent.services.retrieval import _detect_conflicts

    return _detect_conflicts(cites)


def test_two_pages_about_different_things_do_not_disagree():
    found = _conflicts([
        _Cite("a", "Your account must be current or no more than 10 days past due."),
        _Cite("b", "Allow at least 3 business days before the automatic payment is scheduled."),
    ])
    assert found == []


def test_two_pages_about_the_same_thing_do_disagree():
    found = _conflicts([
        _Cite("a", "Your account must be current or no more than 10 days past due."),
        _Cite("b", "Your account must be current or no more than 30 days past due."),
    ])
    assert len(found) == 1
    assert found[0].subject == "days past due"
