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

#: A figure as a model writes one: 42, 1,284, 3.5, 32%, -17, £12.
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PAGE_MARKER = re.compile(r"\[page\]", re.IGNORECASE)
_DATA_MARKER = re.compile(r"\[d\d{1,2}\]", re.IGNORECASE)
_PASSAGE_MARKER = re.compile(r"\[\d{1,2}\](?!\()")

#: Sentence-ish. Splitting on terminators followed by space is wrong in the
#: usual ways (abbreviations, decimals) and right enough here: a marker binds
#: to the clause it ends, and over-splitting only makes the check stricter
#: about which numbers sit with which marker.
_SENTENCE = re.compile(r"(?<=[.!?:])\s+|\n+")


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
    if not answer.strip():
        return ()

    allowed = _figures(sources) | _figures(question)
    found: list[str] = []

    for sentence in _SENTENCE.split(answer):
        marked = bool(_PAGE_MARKER.search(sentence) or _DATA_MARKER.search(sentence))
        if not marked or _PASSAGE_MARKER.search(sentence):
            continue
        # The markers themselves carry digits — [d1] is not a claim about 1.
        body = _DATA_MARKER.sub(" ", _PAGE_MARKER.sub(" ", sentence))
        for figure in _figures(body):
            if figure not in allowed and figure not in found:
                found.append(figure)

    return tuple(found)
