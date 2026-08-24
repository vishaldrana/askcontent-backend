"""Figures credited to the page or a reading have to be in one of them."""

from askcontent.domain.figures import unsupported_figures

PAGE = "Responses: 1,284 of 4,010 invited\nNPS: 42\nNPS last quarter: 51"


def test_a_quoted_figure_passes():
    assert unsupported_figures(
        "You have 1,284 responses of 4,010 invited [page].", sources=PAGE
    ) == ()


def test_separators_do_not_make_a_figure_new():
    # 1284 and 1,284 are the same number written two ways, and rejecting an
    # answer over a comma would make the gate useless.
    assert unsupported_figures("1284 responses so far [page].", sources=PAGE) == ()


def test_a_percent_sign_does_not_make_a_figure_new():
    assert unsupported_figures("NPS is 42% [page].", sources=PAGE) == ()


def test_a_derived_rate_is_caught():
    # The case from the first real run. The arithmetic is not the problem; the
    # definition is — nothing said "invited" is the denominator this product
    # means by "response rate".
    assert unsupported_figures(
        "Your response rate is approximately 32% [page].", sources=PAGE
    ) == ("32",)


def test_a_derived_difference_is_caught():
    assert unsupported_figures(
        "NPS fell by 9 points [page].", sources=PAGE
    ) == ("9",)


def test_the_same_rule_covers_live_readings():
    readings = "Survey analytics\n[d1] NPS: 42\n[d2] NPS last quarter: 51"
    assert unsupported_figures("A drop of 9 points [d1][d2].", sources=readings) == ("9",)


def test_the_markers_own_digits_are_not_claims():
    readings = "[d1] NPS: 42"
    # [d1] is a marker, not an assertion about the number one.
    assert unsupported_figures("NPS is 42 [d1].", sources=readings) == ()


def test_a_sentence_that_also_cites_a_passage_is_left_alone():
    # The number may legitimately come from the passage, and this check cannot
    # see passages. Conservative on purpose: a false positive rejects a good
    # answer.
    assert unsupported_figures(
        "The threshold is 9 points [2][page].", sources=PAGE
    ) == ()


def test_a_sentence_with_no_marker_is_left_alone():
    assert unsupported_figures("Nine is a number. 77 too.", sources=PAGE) == ()


def test_the_readers_own_number_is_theirs():
    assert unsupported_figures(
        "There were 300 in that segment [page].", sources=PAGE,
        question="how many of the 300 mobile users responded",
    ) == ()


def test_spelled_out_numbers_are_not_examined():
    # A floor, not a proof. One that never fires on honest prose is worth more
    # than one that catches everything.
    assert unsupported_figures("It fell by nine points [page].", sources=PAGE) == ()


def test_nothing_to_check_when_there_is_no_answer():
    assert unsupported_figures("", sources=PAGE) == ()


def test_the_answer_survives_one_bad_sentence():
    from askcontent.domain.figures import strip_unsupported

    result = strip_unsupported(
        "Your NPS is 42, against 51 last quarter [page]. That is a fall of 9 points [page].",
        sources=PAGE,
    )
    assert result.kept == "Your NPS is 42, against 51 last quarter [page]."
    assert result.removed == ("That is a fall of 9 points [page].",)
    assert result.figures == ("9",)
    assert result.survives


def test_an_answer_that_is_only_a_bad_sentence_does_not_survive():
    from askcontent.domain.figures import strip_unsupported

    result = strip_unsupported("Your response rate is 32% [page].", sources=PAGE)
    assert result.kept == ""
    assert not result.survives


def test_what_is_left_must_still_say_where_it_came_from():
    from askcontent.domain.figures import strip_unsupported

    # The surviving sentence carries no marker, so there is no answer left to
    # keep — only prose that cannot be checked.
    result = strip_unsupported(
        "Here is what I found. Your rate is 32% [page].", sources=PAGE
    )
    assert result.kept == "Here is what I found."
    assert not result.survives
