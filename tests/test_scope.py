"""CNT-SCP-* — the containment model."""

import datetime as dt

import pytest
from pydantic import ValidationError

from askcontent.domain.documents import DocMetadata, Sensitivity
from askcontent.domain.scope import (
    ExclusionRule,
    KnowledgeScope,
    SourceRoot,
    diff,
    evaluate,
)


def meta(**overrides) -> DocMetadata:
    base = dict(
        doc_id="D1", kb_id="kb", title="Doc", url="https://x/1",
        space="HR", path="/hr/policies/doc", labels=("policy",),
        updated_at=dt.datetime(2026, 1, 1),
    )
    return DocMetadata(**(base | overrides))


def test_scope_cannot_be_constructed_without_a_root():
    """CNT-SCP-02 — there is no representation of 'the whole source'."""
    with pytest.raises(ValidationError):
        KnowledgeScope(roots=())


def test_exclude_always_beats_include():
    """CNT-SCP-04."""
    scope = KnowledgeScope(
        roots=(SourceRoot(kind="space", value="HR"),),
        include=("/hr/policies/*",),
        exclude=("/hr/policies/doc",),
    )
    decision = evaluate(scope, meta())
    assert not decision.in_scope
    assert decision.rule is ExclusionRule.EXCLUDE


def test_every_exclusion_names_exactly_one_rule():
    """CNT-ADM-10 — 'excluded by exclude pattern /archive/*', never 'filtered'."""
    scope = KnowledgeScope(roots=(SourceRoot(kind="space", value="ENG"),))
    decision = evaluate(scope, meta())
    assert decision.rule is ExclusionRule.NO_ROOT
    assert "HR" in decision.reason()


def test_sensitivity_ceiling_is_part_of_the_scope_decision():
    """CNT-CON-03 / CNT-SCP-15 — one effective-corpus definition, not two passes."""
    scope = KnowledgeScope(
        roots=(SourceRoot(kind="space", value="HR"),),
        sensitivity_ceiling=Sensitivity.INTERNAL,
    )
    decision = evaluate(scope, meta(sensitivity=Sensitivity.RESTRICTED))
    assert decision.rule is ExclusionRule.SENSITIVITY


def test_evaluation_is_pure_and_repeatable():
    """CNT-SCP-05 — one implementation, called from three places."""
    scope = KnowledgeScope(roots=(SourceRoot(kind="space", value="HR"),))
    document = meta()
    assert evaluate(scope, document) == evaluate(scope, document)


def test_diff_reports_add_and_remove_counts():
    """CNT-SCP-09 — the sentence that stops a mistake."""
    old = KnowledgeScope(roots=(SourceRoot(kind="space", value="HR"),))
    new = KnowledgeScope(
        roots=(SourceRoot(kind="space", value="HR"),), exclude=("/hr/archive/*",)
    )
    population = [
        meta(doc_id="A", path="/hr/policies/a"),
        meta(doc_id="B", path="/hr/archive/b"),
        meta(doc_id="C", path="/hr/archive/c"),
    ]
    delta = diff(old, new, population)
    assert (delta.added, delta.removed, delta.unchanged) == (0, 2, 1)


def test_scope_canonical_json_is_stable_for_hashing():
    """CNT-SCP-01 — a closed structure can be canonicalised; a string cannot."""
    scope = KnowledgeScope(
        roots=(SourceRoot(kind="space", value="HR"),), exclude=("b", "a")
    )
    assert scope.canonical_json() == scope.model_copy().canonical_json()


def test_date_window_uses_the_documents_own_date():
    scope = KnowledgeScope(
        roots=(SourceRoot(kind="space", value="HR"),),
        updated_after=dt.date(2026, 6, 1),
    )
    assert evaluate(scope, meta()).rule is ExclusionRule.TOO_OLD
    assert evaluate(scope, meta(updated_at=dt.datetime(2026, 7, 1))).in_scope
