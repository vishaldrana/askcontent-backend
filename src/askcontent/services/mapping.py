"""Field mapping — the robustness core of the admin console (CNT-MAP-*).

Every knowledgebase in PGP spells the same concept differently and shapes it
differently: `lastModified` as ISO-8601 here, `updated` as epoch seconds there,
`revisedOn` as DD/MM/YYYY somewhere else. The platform never learns those
names. This map does, and it is **data** — versioned configuration, not code.

The prohibition that makes this work: a mapping entry is a *typed transform*,
never a scripting hook (CNT-MAP-02). A per-knowledgebase transform script is a
per-knowledgebase code branch wearing a costume — unreviewable, untestable in
aggregate, and a remote-execution surface in an admin console.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from ..domain.documents import AuthorityTier, DocMetadata, DocType, Sensitivity

REQUIRED_FIELDS = ("doc_id", "title", "url", "updated_at")
DEFAULT_COVERAGE_THRESHOLD = 0.95


class Coercion(StrEnum):
    STRING = "string"
    DATE_ISO = "date_iso"
    DATE_EPOCH = "date_epoch"
    DATE_DMY = "date_dmy"
    DATE_MDY = "date_mdy"
    STRING_LIST = "string_list"
    CSV_LIST = "csv_list"
    INT = "int"


class FieldRule(BaseModel):
    """One canonical field, one source field, one coercion, one optional value
    map, one optional default. That is the entire expressive surface."""

    target: str
    source: str | None = None
    coercion: Coercion = Coercion.STRING
    value_map: dict[str, str] = Field(default_factory=dict)
    default: str | None = None
    # Where PGP and the ECM both expose this field, which wins (CNT-MAP-05).
    # The default is the ECM, as the system of record.
    prefer: str = "ecm"


class FieldMap(BaseModel):
    kb_id: str
    rules: tuple[FieldRule, ...]
    # Declared when the source cannot answer "may this principal read this"
    # (CNT-ACL-03). There is no third option and no inference.
    access_class: str | None = None

    def rule_for(self, target: str) -> FieldRule | None:
        return next((r for r in self.rules if r.target == target), None)


class CoercionError(ValueError):
    pass


def coerce(value: object, coercion: Coercion) -> object:
    if value is None:
        return None
    match coercion:
        case Coercion.STRING:
            return str(value)
        case Coercion.INT:
            return int(value)
        case Coercion.DATE_ISO:
            return dt.datetime.fromisoformat(str(value))
        case Coercion.DATE_EPOCH:
            return dt.datetime.fromtimestamp(int(value))
        case Coercion.DATE_DMY:
            return dt.datetime.strptime(str(value), "%d/%m/%Y")
        case Coercion.DATE_MDY:
            return dt.datetime.strptime(str(value), "%m/%d/%Y")
        case Coercion.STRING_LIST:
            if isinstance(value, (list, tuple)):
                return tuple(str(v).strip() for v in value if str(v).strip())
            return tuple(str(value).split()) if str(value).strip() else ()
        case Coercion.CSV_LIST:
            if isinstance(value, (list, tuple)):
                return tuple(str(v).strip() for v in value)
            return tuple(p.strip() for p in str(value).split(",") if p.strip())
    raise CoercionError(f"unknown coercion {coercion}")


class MappingOutcome(BaseModel):
    metadata: DocMetadata | None = None
    errors: tuple[str, ...] = ()


def apply_map(field_map: FieldMap, raw: dict[str, object], kb_id: str) -> MappingOutcome:
    """Transform one source row into canonical metadata.

    Unmapped source fields are retained verbatim in `extras` (CNT-MAP-06): the
    field nobody mapped is routinely the one carrying the authority signal, and
    discarding it means re-ingesting to get it back.
    """
    values: dict[str, object] = {}
    errors: list[str] = []
    consumed: set[str] = set()

    for rule in field_map.rules:
        raw_value = raw.get(rule.source) if rule.source else None
        if rule.source:
            consumed.add(rule.source)
        if raw_value in (None, ""):
            if rule.default is not None:
                raw_value = rule.default
            else:
                continue
        try:
            coerced = coerce(raw_value, rule.coercion)
        except (ValueError, TypeError) as exc:
            errors.append(f"{rule.target}: cannot coerce {raw_value!r} via {rule.coercion} ({exc})")
            continue
        if rule.value_map and isinstance(coerced, str):
            coerced = rule.value_map.get(coerced, coerced)
        values[rule.target] = coerced

    missing = [f for f in REQUIRED_FIELDS if values.get(f) in (None, "", ())]
    # updated_at is required to be *mapped*, but a document legitimately
    # lacking a date becomes unknown_age rather than a mapping error
    # (CNT-CAT-10). The distinction is: no rule vs. no value.
    missing = [f for f in missing if f != "updated_at" or field_map.rule_for("updated_at") is None]
    if missing:
        errors.append(f"required fields absent: {', '.join(missing)}")
        return MappingOutcome(errors=tuple(errors))

    extras = {
        str(k): str(v)[:500] for k, v in raw.items() if k not in consumed
    }

    try:
        metadata = DocMetadata(
            doc_id=str(values["doc_id"]),
            kb_id=kb_id,
            title=str(values["title"]),
            url=str(values["url"]),
            updated_at=values.get("updated_at"),  # type: ignore[arg-type]
            space=_opt_str(values.get("space")),
            owner=_opt_str(values.get("owner")),
            labels=tuple(values.get("labels") or ()),  # type: ignore[arg-type]
            doc_type=_opt_enum(DocType, values.get("doc_type")),
            doc_type_source="mapped" if values.get("doc_type") else None,
            sensitivity=_opt_enum(Sensitivity, values.get("sensitivity")) or Sensitivity.INTERNAL,
            acl_principals=tuple(values.get("acl_principals") or ()),  # type: ignore[arg-type]
            authority=_opt_enum(AuthorityTier, values.get("authority")) or AuthorityTier.SUPPORTING,
            path=_opt_str(raw.get("path")) or _opt_str(values.get("path")),
            extras=extras,
        )
    except Exception as exc:  # noqa: BLE001
        return MappingOutcome(errors=(f"metadata construction failed: {exc}",))

    return MappingOutcome(metadata=metadata, errors=tuple(errors))


def _opt_str(value: object) -> str | None:
    return None if value in (None, "") else str(value)


def _opt_enum(enum_cls, value):
    if value in (None, ""):
        return None
    try:
        return enum_cls(str(value))
    except ValueError:
        return None


class FieldValidation(BaseModel):
    target: str
    source: str | None
    coverage: float
    parse_failures: int
    distinct: int
    examples: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


class MapValidation(BaseModel):
    kb_id: str
    sample_size: int
    fields: tuple[FieldValidation, ...]
    blocking: tuple[str, ...]

    @property
    def can_activate(self) -> bool:
        return not self.blocking


def validate_map(
    field_map: FieldMap,
    sample: list[dict[str, object]],
    *,
    coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
) -> MapValidation:
    """Validation over a real sample (CNT-MAP-04).

    Mapping metadata you cannot see is guesswork, and the failure is silent: a
    date field mapped from a string that does not parse yields every document
    dated 'unknown', which quietly disables freshness for the whole KB.
    """
    field_results: list[FieldValidation] = []
    blocking: list[str] = []

    for rule in field_map.rules:
        present = 0
        failures: list[str] = []
        values: list[str] = []

        for row in sample:
            raw_value = row.get(rule.source) if rule.source else None
            if raw_value in (None, ""):
                if rule.default is None:
                    continue
                raw_value = rule.default
            present += 1
            try:
                coerced = coerce(raw_value, rule.coercion)
                values.append(str(coerced)[:60])
            except (ValueError, TypeError) as exc:
                failures.append(f"{raw_value!r}: {exc}")

        coverage = present / len(sample) if sample else 0.0
        parse_rate = 1.0 - (len(failures) / present if present else 0.0)
        effective = coverage * parse_rate

        field_results.append(
            FieldValidation(
                target=rule.target,
                source=rule.source,
                coverage=round(effective, 4),
                parse_failures=len(failures),
                distinct=len(set(values)),
                examples=tuple(values[:3]),
                failures=tuple(failures[:3]),
            )
        )

        if rule.target in REQUIRED_FIELDS and effective < coverage_threshold:
            blocking.append(
                f"{rule.target} coverage {effective:.0%} is below the "
                f"{coverage_threshold:.0%} threshold required to activate"
            )

    mapped = {r.target for r in field_map.rules}
    for required in REQUIRED_FIELDS:
        if required not in mapped:
            blocking.append(f"{required} is required and is not mapped")

    if "acl_principals" not in mapped and not field_map.access_class:
        # The dangerous middle case: a source with unreadable ACLs where the
        # team assumes something reasonable. Force the declaration (CNT-ACL-03).
        blocking.append(
            "this knowledgebase exposes no ACL field, so an explicit access "
            "class must be declared before it can be activated"
        )

    return MapValidation(
        kb_id=field_map.kb_id,
        sample_size=len(sample),
        fields=tuple(field_results),
        blocking=tuple(blocking),
    )


def suggest_map(kb_id: str, field_names: list[str]) -> FieldMap:
    """A starting point for the mapping editor, never an applied default.

    Suggestions are shown for confirmation because a wrong guess here is silent
    and systemic — see the date example above.
    """
    lowered = {name.lower(): name for name in field_names}

    def find(*needles: str) -> str | None:
        for needle in needles:
            for low, original in lowered.items():
                if needle in low:
                    return original
        return None

    def guess_date_coercion(source: str | None) -> Coercion:
        # The editor shows live samples beside this so a human corrects it.
        if source and any(k in source.lower() for k in ("epoch", "ts", "time")):
            return Coercion.DATE_EPOCH
        return Coercion.DATE_ISO

    date_source = find("modified", "updated", "revised", "issued", "published", "date")
    labels_source = find("tag", "label", "topic", "keyword", "categor", "flag")

    rules = [
        FieldRule(target="doc_id", source=find("docid", "documentnumber", "pageid", "id")),
        FieldRule(target="title", source=find("title", "name", "heading", "subject")),
        FieldRule(target="url", source=find("url", "link", "href", "uri")),
        FieldRule(target="updated_at", source=date_source, coercion=guess_date_coercion(date_source)),
        FieldRule(target="space", source=find("space", "team", "domain", "site", "practice")),
        FieldRule(target="owner", source=find("owner", "maintainer", "custodian", "accountable")),
        FieldRule(target="labels", source=labels_source, coercion=Coercion.CSV_LIST),
        FieldRule(target="sensitivity", source=find("classification", "level", "infoclass", "handling")),
        FieldRule(target="acl_principals", source=find("acl", "readgroups", "entitlement", "permitted"), coercion=Coercion.STRING_LIST),
    ]
    return FieldMap(kb_id=kb_id, rules=tuple(r for r in rules if r.source))
