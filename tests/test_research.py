"""What a research run is allowed to do, and what counts as a finding."""

from askcontent.domain.research import (
    DEPTH_PRESETS,
    Finding,
    Report,
    looks_single_shot,
    resolve_config,
)


def test_a_preset_is_a_whole_configuration():
    config = resolve_config({"depth": "thorough"})
    assert config["max_sub_questions"] == DEPTH_PRESETS["thorough"]["max_sub_questions"]
    assert config["depth"] == "thorough"


def test_overriding_a_preset_stops_it_claiming_to_be_one():
    # Nothing in the interface should say "standard" about a run that was not.
    assert resolve_config({"depth": "standard", "max_sub_questions": 99})["depth"] == "custom"
    assert resolve_config({"depth": "standard", "verify": False})["depth"] == "standard"


def test_a_finding_without_evidence_is_not_usable():
    # The rule the whole design rests on: dropped at synthesis, not softened.
    assert not Finding(sub_question_id="q1", statement="Fees are waived.").usable
    assert Finding(sub_question_id="q1", statement="Fees are waived [2].",
                   citations=("2",)).usable


def test_a_refuted_finding_is_not_usable_even_with_citations():
    assert not Finding(
        sub_question_id="q1", statement="Fees are waived [2].",
        citations=("2",), refuted=True, refuted_because="no passage supported it",
    ).usable


def test_a_report_with_no_usable_finding_is_not_grounded():
    report = Report(
        question="q", depth="quick",
        findings=(Finding(sub_question_id="q1", statement="Something."),),
    )
    assert not report.grounded


def test_a_simple_question_is_flagged_as_a_waste():
    # A hint for the interface, never a refusal.
    assert looks_single_shot("how do I reset my password")
    assert looks_single_shot("where is the settings page")


def test_a_question_that_needs_investigation_is_not():
    assert not looks_single_shot(
        "compare the ways I can pay an auto loan and what each one requires"
    )
    assert not looks_single_shot("why did my NPS drop and what should I do about it")


def test_a_research_marker_beats_the_length_test():
    # Short, but it is a comparison — one retrieval will not do it.
    assert not looks_single_shot("compare overdraft fees")


def _trace(**kw):
    from askcontent.services.retrieval import RetrievalTrace

    return RetrievalTrace(spec_json="{}", plan_hash="h", filters={}, **kw)


def test_a_research_turn_carries_every_field_an_ordinary_turn_does():
    """The white-screen regression.

    A research turn goes out over the same channel as an ordinary answer and
    is read by the same screen, which reaches into `trace` on every completed
    turn. A payload that leaves it out does not degrade — it unmounts.
    """
    from askcontent.api.extra import _merge_traces
    from askcontent.services.retrieval import Evidence

    evidence = Evidence(citations=(), trace=_merge_traces(()))
    assert set(Evidence.model_fields) <= set(evidence.model_dump(mode="json"))
    assert evidence.trace.degraded == ()


def test_merging_adds_the_counts_and_unions_the_degradations():
    from askcontent.api.extra import _merge_traces

    merged = _merge_traces([
        _trace(forbidden_count=2, degraded=("vector channel down",), total_ms=10.0),
        _trace(forbidden_count=3, degraded=("vector channel down",), total_ms=5.0),
    ])
    assert merged.forbidden_count == 5
    assert merged.total_ms == 15.0
    # The same channel failing on every sub-question is one thing wrong, and
    # saying it three times reads as three.
    assert merged.degraded == ("vector channel down",)
