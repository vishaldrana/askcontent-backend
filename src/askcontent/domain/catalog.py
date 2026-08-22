"""Classification, authority and freshness (CNT-CAT-*).

Every function here is a pure function of metadata and parsed structure. No
model call, ever — the same rule as askdb's column classification, for the same
reason: it converts a guarantee into a probability.
"""

from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel

from .documents import (
    AuthorityTier,
    BlockKind,
    DocMetadata,
    DocType,
    ParsedDocument,
    Staleness,
)


class Classification(BaseModel):
    doc_type: DocType
    confidence: float
    evidence: tuple[str, ...]
    source: str  # "mapped" | "ladder"


_TITLE_RULES: list[tuple[re.Pattern[str], DocType, str]] = [
    (re.compile(r"\b(policy|policies|standard|code of conduct)\b", re.I), DocType.POLICY, "title names a policy"),
    (re.compile(r"\b(runbook|playbook|how to|guide|procedure|sop)\b", re.I), DocType.PROCEDURE, "title names a procedure"),
    (re.compile(r"\b(adr|decision record|rfc)\b", re.I), DocType.DECISION, "title names a decision record"),
    (re.compile(r"\b(spec|specification|requirements|prd)\b", re.I), DocType.SPECIFICATION, "title names a specification"),
    (re.compile(r"\b(faq|frequently asked)\b", re.I), DocType.FAQ, "title names an FAQ"),
    (re.compile(r"\b(notes|minutes|retro|standup|sync)\b", re.I), DocType.NOTES, "title names notes"),
    (re.compile(r"\b(report|review|q[1-4]\s*20\d\d|annual)\b", re.I), DocType.REPORT, "title names a report"),
]

_BODY_MARKERS: list[tuple[re.Pattern[str], DocType, str]] = [
    (re.compile(r"^\s*(status|context|decision|consequences)\s*:", re.I | re.M), DocType.DECISION, "decision-record block markers"),
    (re.compile(r"\b(effective date|approved by|review date|policy owner)\b", re.I), DocType.POLICY, "policy approval block"),
    (re.compile(r"\b(must|must not|should|shall)\b", re.I), DocType.SPECIFICATION, "normative keyword density"),
]


def classify(meta: DocMetadata, parsed: ParsedDocument | None) -> Classification:
    """The ladder (CNT-CAT-01). A mapped doc_type wins (CNT-CAT-02)."""
    if meta.doc_type is not None and meta.doc_type_source == "mapped":
        return Classification(
            doc_type=meta.doc_type,
            confidence=1.0,
            evidence=("doc_type supplied by the source field map",),
            source="mapped",
        )

    evidence: list[str] = []

    for pattern, doc_type, why in _TITLE_RULES:
        if pattern.search(meta.title):
            evidence.append(why)
            return Classification(
                doc_type=doc_type, confidence=0.85, evidence=tuple(evidence), source="ladder"
            )

    if parsed is not None and not parsed.refused:
        text = parsed.full_text()

        headings = [b for b in parsed.blocks if b.kind is BlockKind.HEADING]
        questions = sum(1 for h in headings if h.text.strip().endswith("?"))
        if headings and questions / len(headings) > 0.5:
            return Classification(
                doc_type=DocType.FAQ,
                confidence=0.8,
                evidence=(f"{questions} of {len(headings)} headings are questions",),
                source="ladder",
            )

        tables = sum(1 for b in parsed.blocks if b.kind is BlockKind.TABLE)
        if parsed.blocks and tables / len(parsed.blocks) > 0.3:
            return Classification(
                doc_type=DocType.REFERENCE,
                confidence=0.7,
                evidence=(f"{tables} tables in {len(parsed.blocks)} blocks",),
                source="ladder",
            )

        numbered = sum(
            1 for h in headings if re.match(r"^\s*(step\s+)?\d+[.)]", h.text, re.I)
        )
        if numbered >= 3:
            return Classification(
                doc_type=DocType.PROCEDURE,
                confidence=0.75,
                evidence=(f"{numbered} numbered step headings",),
                source="ladder",
            )

        for pattern, doc_type, why in _BODY_MARKERS:
            hits = len(pattern.findall(text))
            if hits >= 3:
                return Classification(
                    doc_type=doc_type,
                    confidence=0.65,
                    evidence=(f"{why} ({hits} occurrences)",),
                    source="ladder",
                )

    return Classification(
        doc_type=DocType.PAGE,
        confidence=0.3,
        evidence=("no rule matched; default type",),
        source="ladder",
    )


class FreshnessPolicy(BaseModel):
    ageing_days: int = 180
    stale_days: int = 365
    expired_days: int = 1095


def staleness(
    meta: DocMetadata, policy: FreshnessPolicy, now: dt.datetime
) -> Staleness:
    """A missing date is never treated as fresh (CNT-CAT-10).

    The intuitive default — 'recent enough' — makes an entire badly-mapped
    knowledgebase permanently authoritative and permanently stale at once.
    """
    if meta.updated_at is None:
        return Staleness.UNKNOWN_AGE
    age = (now - meta.updated_at).days
    if age >= policy.expired_days:
        return Staleness.EXPIRED
    if age >= policy.stale_days:
        return Staleness.STALE
    if age >= policy.ageing_days:
        return Staleness.AGEING
    return Staleness.FRESH


class AuthorityRule(BaseModel):
    """Tier by rule, overridable by a human (CNT-CAT-05)."""

    space: str | None = None
    path_prefix: str | None = None
    label: str | None = None
    tier: AuthorityTier

    def matches(self, meta: DocMetadata) -> bool:
        if self.space is not None and meta.space != self.space:
            return False
        if self.path_prefix is not None and not (meta.path or "").startswith(self.path_prefix):
            return False
        if self.label is not None and self.label not in meta.labels:
            return False
        return any(x is not None for x in (self.space, self.path_prefix, self.label))


def assign_authority(
    meta: DocMetadata,
    rules: list[AuthorityRule],
    pins: dict[str, AuthorityTier],
    staleness_state: Staleness,
) -> tuple[AuthorityTier, str]:
    # Human pins survive every future ingest, re-map and re-classify
    # (CNT-CAT-11). They are checked first and nothing overrides them.
    if meta.doc_id in pins:
        return pins[meta.doc_id], "human pin"
    if staleness_state is Staleness.EXPIRED:
        return AuthorityTier.ARCHIVE, "expired by freshness policy"
    for rule in rules:
        if rule.matches(meta):
            return rule.tier, "authority rule"
    return AuthorityTier.SUPPORTING, "default"
