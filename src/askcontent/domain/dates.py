"""Recovering dates a source did not give us.

A great deal of content carries its dates in the *text* rather than in
metadata: "Effective date: 1 March 2026", "Last reviewed 14/02/2026", a version
block, a footer. When the field map has nothing to map, those are the only
dates there are.

Two rules govern this, and they follow from `CNT-CAT-10` — a missing date is
never treated as fresh:

  * **Provenance travels with the value.** A date read out of prose is weaker
    evidence than one the system of record supplied, and a reader deciding
    whether a policy is current needs to know which they are looking at.
  * **A guess is not a date.** Only labelled dates are taken. A bare date
    anywhere in the body is far more likely to be a deadline, an example or a
    statutory citation than the document's own currency.

Pure: no I/O, no model call.
"""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum

from pydantic import BaseModel


class DateSource(StrEnum):
    METADATA = "metadata"          # the field map supplied it
    CONTENT = "content"            # read from a labelled line in the text
    NONE = "none"                  # nothing found; never treated as fresh


class FoundDate(BaseModel):
    value: dt.datetime | None = None
    source: DateSource = DateSource.NONE
    #: The text the value was read from, so a reviewer can judge it.
    evidence: str | None = None
    label: str | None = None


_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})

#: Labels that mean "this document was last changed". Ordered: the earlier a
#: label appears here, the more it is trusted when several are present.
UPDATED_LABELS = [
    "last updated", "last modified", "last reviewed", "last revised",
    "date last reviewed", "revised on", "revised", "reviewed on", "reviewed",
    "updated on", "updated", "modified on", "modified", "version date",
    "effective date", "effective", "issued on", "issued", "published on",
    "published", "date of issue", "approval date", "approved on",
]

#: Labels that mean "this document came into existence".
CREATED_LABELS = [
    "created on", "created", "date created", "authored on", "authored",
    "first published", "first issued", "original issue", "date of origin",
    "drafted on", "drafted",
]

_DATE_PATTERNS = [
    # 1 March 2026 / 1st March 2026 / March 1, 2026
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s+(\d{4})\b"), "dmy"),
    (re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"), "mdy"),
    # 2026-03-01
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "iso"),
    # 01/03/2026 — ambiguous, and treated as day-first. Stated rather than
    # guessed: the deployments this is built for are not US-only, and a wrong
    # reading here silently shifts a date by up to eleven months.
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "dmy_slash"),
]


def _parse(match: re.Match, kind: str) -> dt.datetime | None:
    try:
        if kind == "iso":
            year, month, day = (int(g) for g in match.groups())
        elif kind == "dmy":
            day = int(match.group(1))
            month = _MONTHS.get(match.group(2).lower()[:3], 0)
            year = int(match.group(3))
        elif kind == "mdy":
            month = _MONTHS.get(match.group(1).lower()[:3], 0)
            day = int(match.group(2))
            year = int(match.group(3))
        else:
            day, month, year = (int(g) for g in match.groups())
        if not month or not 1 <= day <= 31 or not 1900 <= year <= 2100:
            return None
        return dt.datetime(year, month, day, tzinfo=dt.UTC)
    except (ValueError, TypeError):
        return None


def _find_labelled(text: str, labels: list[str]) -> FoundDate:
    lowered = text.lower()
    for label in labels:
        start = 0
        while True:
            position = lowered.find(label, start)
            if position < 0:
                break
            # Only look just after the label. A date three paragraphs later is
            # a different fact that happens to share a page.
            window = text[position : position + len(label) + 40]
            for pattern, kind in _DATE_PATTERNS:
                match = pattern.search(window)
                if match:
                    value = _parse(match, kind)
                    if value:
                        return FoundDate(
                            value=value, source=DateSource.CONTENT,
                            evidence=" ".join(window.split())[:120], label=label,
                        )
            start = position + len(label)
    return FoundDate()


def extract_dates(text: str) -> tuple[FoundDate, FoundDate]:
    """Return (created, updated) as found in the document's own text."""
    body = text or ""
    return _find_labelled(body, CREATED_LABELS), _find_labelled(body, UPDATED_LABELS)


def resolve_dates(
    metadata_updated: dt.datetime | None,
    text: str,
) -> tuple[FoundDate, FoundDate]:
    """Metadata first, content second, nothing third.

    The system of record wins where it has an answer. Where it does not, a
    labelled date in the text is better than no date at all — and the caller can
    tell the difference, which is the whole point of carrying the source.
    """
    created, updated = extract_dates(text)
    if metadata_updated is not None:
        updated = FoundDate(
            value=metadata_updated, source=DateSource.METADATA,
            label="source metadata",
        )
    return created, updated


def summarise(text: str, limit: int = 300) -> str:
    """A one-paragraph description, taken from the document rather than written.

    The first substantial paragraph that is not a heading, a label line or a
    table row. Generating a summary with a model would be better prose and
    worse evidence: this is what the document says about itself.
    """
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if len(line) < 60 or line.startswith(("#", "|", "-", "*")):
            continue
        if re.match(r"^[A-Za-z ]{3,30}:", line) and len(line) < 120:
            continue  # a label line, not prose
        return line[:limit]
    return ""
