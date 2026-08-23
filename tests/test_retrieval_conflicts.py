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
