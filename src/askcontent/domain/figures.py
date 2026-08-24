"""Numbers an answer attributes to the page or to a live reading.

A prompt rule the model can quietly stop following is not a rule. `[page]` and
`[d1]` say "this came from something in front of you" — so the figures in those
sentences have to *be* in the thing in front of you, and that is checkable
rather than merely instructable.

The failure it catches, which happened on the first real run:

    the page shows "Responses: 1,284 of 4,010 invited"
    the answer says "your response rate is approximately 32% [page]"

Thirty-two percent is not on the page. It is arithmetic, and the arithmetic
is not the problem — the definition is. Nothing told the model whether "invited"
is the denominator this product means by "response rate", whether partial
responses count, or whether 4,010 already excludes bounced invitations. It
guessed a definition, presented the result as a reading, and attributed it to a
screen the reader can look at and not find it on.

Deliberately conservative, because a false positive rejects a good answer:

  * only sentences whose markers are *exclusively* `[page]` or `[dN]`. A
    sentence that also cites a passage may legitimately take its number from
    the passage;
  * numbers echoed from the reader's own question are theirs, not ours;
  * spelled-out numbers are not examined at all — "nine points" passes. The
    check is a floor, not a proof, and a floor that never fires on honest
    prose is worth more than one that catches everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A figure as a model writes one: 42, 1,284, 3.5, 32%, -17, £12.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PAGE_MARKER = re.compile(r"\[page\]", re.IGNORECASE)
_DATA_MARKER = re.compile(r"\[d\d{1,2}\]", re.IGNORECASE)
_PASSAGE_MARKER = re.compile(r"\[\d{1,2}\](?!\()")

#: Sentence-ish, with the separator kept so the remainder can be rejoined and
#: still read as prose. Splitting on terminators is wrong in the usual ways
#: (abbreviations, decimals) and right enough here: a marker binds to the
#: clause it ends, and over-splitting only makes the check stricter about which
#: numbers sit with which marker.
_SENTENCE = re.compile(r"((?<=[.!?:])\s+|\n+)")


def _split(text: str) -> list[tuple[str, str]]:
    """Sentences paired with the whitespace that followed each."""
    parts = _SENTENCE.split(text)
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        body = parts[i]
        gap = parts[i + 1] if i + 1 < len(parts) else ""
        if body or gap:
            out.append((body, gap))
    return out


def _normalise(value: str) -> str:
    """`1,284` and `1284` are the same figure; `42%` and `42` are the same one."""
    return value.replace(",", "").rstrip("%").rstrip(".")


def _figures(text: str) -> set[str]:
    return {_normalise(m) for m in _NUMBER.findall(text)}


def unsupported_figures(
    answer: str,
    *,
    sources: str,
    question: str = "",
) -> tuple[str, ...]:
    """Figures attributed to the page or a live reading that are not in either.

    `sources` is the page block and the live readings, rendered exactly as the
    answerer was given them — so "in the sources" means "in what it was shown",
    not "true somewhere".
    """
    return strip_unsupported(answer, sources=sources, question=question).figures


@dataclass(frozen=True)
class Stripped:
    """An answer with its unsupported sentences taken out.

    Removing a sentence rather than the whole answer is a deliberate choice
    about severity. The failure is almost always one trailing clause — "42,
    down from 51, *a decrease of 9 points*" — and rejecting the answer costs
    the reader everything to spare them a redundant figure they could have
    worked out themselves.

    Two rules keep the edit honest. What is removed is reported, because an
    answer silently different from what the model wrote is its own kind of
    unattributable. And if removing leaves nothing that carries a marker,
    there is no answer left to keep — the caller withholds it, exactly as
    before.
    """

    kept: str
    removed: tuple[str, ...] = ()
    figures: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.removed)

    @property
    def survives(self) -> bool:
        """Something is left, and it still says where it came from."""
        if not self.kept.strip():
            return False
        return bool(
            _PASSAGE_MARKER.search(self.kept)
            or _PAGE_MARKER.search(self.kept)
            or _DATA_MARKER.search(self.kept)
        )


def strip_unsupported(answer: str, *, sources: str, question: str = "") -> Stripped:
    """Take out the sentences that credit a figure to something it is not in."""
    if not answer.strip():
        return Stripped(kept=answer)

    allowed = _figures(sources) | _figures(question)
    kept: list[str] = []
    removed: list[str] = []
    figures: list[str] = []

    for sentence, gap in _split(answer):
        marked = bool(_PAGE_MARKER.search(sentence) or _DATA_MARKER.search(sentence))
        if not marked or _PASSAGE_MARKER.search(sentence):
            kept.append(sentence + gap)
            continue

        # The markers themselves carry digits — [d1] is not a claim about 1.
        body = _DATA_MARKER.sub(" ", _PAGE_MARKER.sub(" ", sentence))
        offending = [f for f in _figures(body) if f not in allowed]
        if not offending:
            kept.append(sentence + gap)
            continue

        removed.append(sentence.strip())
        for figure in offending:
            if figure not in figures:
                figures.append(figure)

    return Stripped(
        kept="".join(kept).strip(),
        removed=tuple(removed),
        figures=tuple(figures),
    )
