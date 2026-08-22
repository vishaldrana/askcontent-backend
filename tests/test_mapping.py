"""CNT-MAP-* — the robustness core."""

import datetime as dt

from askcontent.services.mapping import (
    Coercion,
    FieldMap,
    FieldRule,
    apply_map,
    suggest_map,
    validate_map,
)


def test_the_same_concept_maps_from_three_different_shapes():
    """The reason the field map exists: every knowledgebase spells and shapes
    the same concept differently, and none of that reaches the pipeline."""
    cases = [
        ("lastModified", "2026-07-08T00:00:00", Coercion.DATE_ISO),
        ("updated", 1783555200, Coercion.DATE_EPOCH),
        ("revisedOn", "08/07/2026", Coercion.DATE_DMY),
    ]
    for source, value, coercion in cases:
        field_map = FieldMap(kb_id="kb", rules=(
            FieldRule(target="doc_id", source="id"),
            FieldRule(target="title", source="t"),
            FieldRule(target="url", source="u"),
            FieldRule(target="updated_at", source=source, coercion=coercion),
            FieldRule(target="acl_principals", source="acl", coercion=Coercion.STRING_LIST),
        ))
        outcome = apply_map(
            field_map, {"id": "D", "t": "T", "u": "U", source: value, "acl": ["g"]}, "kb"
        )
        assert outcome.metadata is not None, outcome.errors
        assert isinstance(outcome.metadata.updated_at, dt.datetime)


def test_unmapped_fields_are_retained_verbatim():
    """CNT-MAP-06 — the field nobody mapped is routinely the authority signal."""
    field_map = FieldMap(kb_id="kb", rules=(
        FieldRule(target="doc_id", source="id"),
        FieldRule(target="title", source="t"),
        FieldRule(target="url", source="u"),
        FieldRule(target="updated_at", source="d", coercion=Coercion.DATE_ISO),
        FieldRule(target="acl_principals", source="acl", coercion=Coercion.STRING_LIST),
    ))
    outcome = apply_map(field_map, {
        "id": "D", "t": "T", "u": "U", "d": "2026-01-01T00:00:00",
        "acl": ["g"], "reviewCycle": "annual",
    }, "kb")
    assert outcome.metadata.extras["reviewCycle"] == "annual"


def test_a_knowledgebase_without_acl_fields_cannot_activate_silently():
    """CNT-ACL-03 — the dangerous middle case is a source with unreadable ACLs
    where the team assumes something reasonable. Force the declaration."""
    field_map = FieldMap(kb_id="kb", rules=(
        FieldRule(target="doc_id", source="id"),
        FieldRule(target="title", source="t"),
        FieldRule(target="url", source="u"),
        FieldRule(target="updated_at", source="d", coercion=Coercion.DATE_ISO),
    ))
    sample = [{"id": "D", "t": "T", "u": "U", "d": "2026-01-01T00:00:00"}]
    assert not validate_map(field_map, sample).can_activate

    declared = field_map.model_copy(update={"access_class": "group:all-staff"})
    assert validate_map(declared, sample).can_activate


def test_a_date_that_does_not_parse_is_reported_not_swallowed():
    """The silent failure this prevents: every document dated 'unknown', which
    quietly disables freshness for the whole knowledgebase."""
    field_map = FieldMap(kb_id="kb", access_class="g", rules=(
        FieldRule(target="doc_id", source="id"),
        FieldRule(target="title", source="t"),
        FieldRule(target="url", source="u"),
        FieldRule(target="updated_at", source="d", coercion=Coercion.DATE_ISO),
    ))
    sample = [{"id": "D", "t": "T", "u": "U", "d": "08/07/2026"}] * 4
    validation = validate_map(field_map, sample)
    assert not validation.can_activate
    updated = next(f for f in validation.fields if f.target == "updated_at")
    assert updated.parse_failures == 4
    assert updated.failures  # examples are shown, not just a count


def test_suggestions_are_never_silently_applied():
    """CNT-MAP-03 — a wrong guess here is silent and systemic, so the editor
    shows suggestions for confirmation against live samples."""
    suggested = suggest_map("kb", ["documentNumber", "docTitle", "portalUrl", "lastModified"])
    assert suggested.rule_for("doc_id").source == "documentNumber"
    assert suggested.rule_for("updated_at").source == "lastModified"
    # No ACL field present, so the map alone cannot activate.
    assert not validate_map(suggested, [{}]).can_activate
