"""Knowledge scope: the containment model (CNT-SCP-*).

This module is *pure*. It performs no I/O, calls no model, and is the single
implementation used by the ingest gate, the retrieval gate and the console
preview (CNT-SCP-05). Three implementations of a predicate is three predicates,
and the divergence shows up as a document the console says is excluded and
retrieval cites anyway.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .documents import DocMetadata, DocType, Sensitivity


class RootKind(StrEnum):
    SPACE = "space"
    PATH = "path"
    LABEL = "label"


class SourceRoot(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: RootKind
    value: str

    def matches(self, meta: DocMetadata) -> bool:
        match self.kind:
            case RootKind.SPACE:
                return meta.space == self.value
            case RootKind.PATH:
                return _path_matches(meta.path, self.value)
            case RootKind.LABEL:
                return self.value in meta.labels
        return False


def _path_matches(path: str | None, pattern: str) -> bool:
    if path is None:
        return False
    # A root given as a prefix implies everything beneath it.
    if not any(ch in pattern for ch in "*?["):
        return path == pattern or path.startswith(pattern.rstrip("/") + "/")
    return fnmatch.fnmatchcase(path, pattern)


class ExclusionRule(StrEnum):
    """Every drop is attributed to exactly one named rule (CNT-ADM-10)."""

    NO_ROOT = "no_matching_root"
    INCLUDE = "include_pattern_did_not_match"
    EXCLUDE = "exclude_pattern_matched"
    LABELS_ANY = "required_label_absent"
    LABELS_NONE = "forbidden_label_present"
    DOC_TYPE = "doc_type_not_in_scope"
    TOO_OLD = "updated_before_window"
    TOO_NEW = "updated_after_window"
    SENSITIVITY = "above_sensitivity_ceiling"


class ScopeDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    in_scope: bool
    rule: ExclusionRule | None = None
    detail: str | None = None

    def reason(self) -> str:
        if self.in_scope:
            return "in scope"
        return f"{self.rule.value}: {self.detail}" if self.detail else str(self.rule)


class KnowledgeScope(BaseModel):
    """Closed grammar. No free-text query field, no raw filter expression, and
    no variant that accepts a source-native query string (CNT-SCP-01).

    There is deliberately no representation of "the whole source" — `roots` is
    non-empty and is the only positive grant (CNT-SCP-02).
    """

    model_config = ConfigDict(frozen=True)

    roots: tuple[SourceRoot, ...]
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    labels_any: tuple[str, ...] = ()
    labels_none: tuple[str, ...] = ()
    doc_types: tuple[DocType, ...] = ()
    updated_after: dt.date | None = None
    updated_before: dt.date | None = None
    max_documents: int = 50_000
    max_bytes: int = 20 * 1024 * 1024 * 1024
    sensitivity_ceiling: Sensitivity = Sensitivity.INTERNAL

    @field_validator("roots")
    @classmethod
    def _roots_non_empty(cls, v: tuple[SourceRoot, ...]) -> tuple[SourceRoot, ...]:
        if not v:
            raise ValueError(
                "a scope must name at least one root; there is no 'everything' scope "
                "(CNT-SCP-02)"
            )
        return v

    def canonical_json(self) -> str:
        """Stable serialisation for hashing, diffing and the audit row."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )


def evaluate(scope: KnowledgeScope, meta: DocMetadata) -> ScopeDecision:
    """Fixed, deterministic evaluation order (CNT-SCP-03).

    Exclude always beats include (CNT-SCP-04).
    """
    if not any(root.matches(meta) for root in scope.roots):
        return ScopeDecision(
            in_scope=False,
            rule=ExclusionRule.NO_ROOT,
            detail=f"space={meta.space!r} path={meta.path!r}",
        )

    if scope.include and not _any_pattern(scope.include, meta):
        return ScopeDecision(
            in_scope=False,
            rule=ExclusionRule.INCLUDE,
            detail=f"none of {list(scope.include)} matched {meta.path!r}",
        )

    for pattern in scope.exclude:
        if _pattern_matches(pattern, meta):
            return ScopeDecision(
                in_scope=False, rule=ExclusionRule.EXCLUDE, detail=pattern
            )

    if scope.labels_any and not set(scope.labels_any) & set(meta.labels):
        return ScopeDecision(
            in_scope=False,
            rule=ExclusionRule.LABELS_ANY,
            detail=f"needs one of {list(scope.labels_any)}",
        )

    forbidden = set(scope.labels_none) & set(meta.labels)
    if forbidden:
        return ScopeDecision(
            in_scope=False,
            rule=ExclusionRule.LABELS_NONE,
            detail=", ".join(sorted(forbidden)),
        )

    if scope.doc_types and meta.doc_type is not None and meta.doc_type not in scope.doc_types:
        return ScopeDecision(
            in_scope=False, rule=ExclusionRule.DOC_TYPE, detail=str(meta.doc_type)
        )

    if meta.updated_at is not None:
        updated = meta.updated_at.date()
        if scope.updated_after and updated < scope.updated_after:
            return ScopeDecision(
                in_scope=False, rule=ExclusionRule.TOO_OLD, detail=updated.isoformat()
            )
        if scope.updated_before and updated > scope.updated_before:
            return ScopeDecision(
                in_scope=False, rule=ExclusionRule.TOO_NEW, detail=updated.isoformat()
            )

    # The sensitivity ceiling is part of the effective corpus (CNT-SCP-15), so
    # it is evaluated here rather than in a second pass somebody can forget.
    if meta.sensitivity.rank > scope.sensitivity_ceiling.rank:
        return ScopeDecision(
            in_scope=False,
            rule=ExclusionRule.SENSITIVITY,
            detail=f"{meta.sensitivity} > ceiling {scope.sensitivity_ceiling}",
        )

    return ScopeDecision(in_scope=True)


def _any_pattern(patterns: tuple[str, ...], meta: DocMetadata) -> bool:
    return any(_pattern_matches(p, meta) for p in patterns)


def _pattern_matches(pattern: str, meta: DocMetadata) -> bool:
    if _path_matches(meta.path, pattern):
        return True
    # Patterns may also address the title, which is how a curator excludes
    # "*DRAFT*" without knowing the source's path convention.
    return fnmatch.fnmatchcase(meta.title, pattern)


class ScopeDiff(BaseModel):
    """What a scope edit would do, before it is saved (CNT-SCP-09)."""

    added: int
    removed: int
    unchanged: int
    added_sample: tuple[str, ...] = Field(default_factory=tuple)
    removed_sample: tuple[str, ...] = Field(default_factory=tuple)

    def summary(self) -> str:
        return f"add {self.added}, remove {self.removed}, {self.unchanged} unchanged"


def diff(
    old: KnowledgeScope | None,
    new: KnowledgeScope,
    population: list[DocMetadata],
    sample_size: int = 5,
) -> ScopeDiff:
    """'Add three hundred, remove eleven thousand' is a sentence that stops a
    mistake. A save button that just says Save does not."""
    added: list[str] = []
    removed: list[str] = []
    unchanged = 0
    for meta in population:
        was = evaluate(old, meta).in_scope if old is not None else False
        now = evaluate(new, meta).in_scope
        if was and not now:
            removed.append(meta.title)
        elif now and not was:
            added.append(meta.title)
        elif now and was:
            unchanged += 1
    return ScopeDiff(
        added=len(added),
        removed=len(removed),
        unchanged=unchanged,
        added_sample=tuple(added[:sample_size]),
        removed_sample=tuple(removed[:sample_size]),
    )
