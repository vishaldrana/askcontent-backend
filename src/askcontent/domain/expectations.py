"""What an eval case asserts.

A **closed set**, for the same reason the query grammar is closed: a free-text
expectation is one nobody can evaluate mechanically, and a suite whose results
need a human to interpret is a suite that stops being run by the third week.

Five kinds, and each exists because a real failure needed it:

    answers          the corpus covers this; refusing is the bug
    refuses          the corpus does not; answering is the bug — and this is
                     the more important direction, because a miss is visible
                     to the reader and an invention is not
    cites            the answer must rest on a particular document; this is
                     what catches a ranking change that quietly swapped the
                     source out from under a still-plausible answer
    cites_first      and it must be the *best* source. `cites` alone cannot
                     see a ranking regression that keeps the right document in
                     the evidence and pushes it down — which is precisely what
                     a reranker change does when it goes wrong
    cites_something  at least one citation, without saying which. For questions
                     where several documents would be a fair answer and the
                     thing worth asserting is that the answer rests on
                     *anything* — an answer that cites nothing may be correct
                     and is still unverifiable
    says             an exact string must appear — for figures, thresholds and
                     dates, where a paraphrase is a wrong answer
    does_not_say     for the specific wrong answer somebody has already given

`says` is deliberately literal. The temptation is to check meaning with a
model, and it is a trap twice over: the check becomes non-deterministic, so a
red suite may be red because the judge changed its mind; and it costs a model
call per assertion on a suite meant to run on every change.

This module is pure. It is handed a finished answer and returns what failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KINDS = ("answers", "refuses", "cites", "cites_first", "cites_something",
         "says", "does_not_say")


@dataclass(frozen=True)
class Expectation:
    kind: str
    #: The document, string or phrase this expectation is about. Unused by
    #: `answers` and `refuses`, which assert about the outcome itself.
    value: str = ""

    def describe(self) -> str:
        return {
            "answers": "answers the question",
            "refuses": "refuses to answer",
            "cites": f"cites “{self.value}”",
            "cites_first": f"cites “{self.value}” first",
            "cites_something": "cites at least one source",
            "says": f"says “{self.value}”",
            "does_not_say": f"does not say “{self.value}”",
        }.get(self.kind, f"{self.kind} {self.value}")


@dataclass
class Outcome:
    """The finished answer, as the assertions see it."""

    answer: str
    grounded: bool
    #: Titles and ids of the passages the answer actually cited — not
    #: everything retrieved. An expectation that a document was *cited* must
    #: not be satisfied by it merely having been considered.
    cited: tuple[str, ...] = ()
    failures: list[str] = field(default_factory=list)


def check(expectations: list[Expectation], outcome: Outcome) -> list[str]:
    """Every failure, not the first.

    Stopping at the first turns a case that is wrong in three ways into three
    consecutive re-runs.
    """
    failures: list[str] = []

    for expectation in expectations:
        kind, value = expectation.kind, expectation.value

        if kind == "answers":
            if not outcome.grounded:
                failures.append("refused, but this question should be answerable")

        elif kind == "refuses":
            if outcome.grounded:
                failures.append(
                    "answered, but nothing in the corpus covers this — "
                    f"cited {', '.join(outcome.cited[:3]) or 'nothing'}"
                )

        elif kind == "cites":
            if not any(_loose(value) in _loose(c) for c in outcome.cited):
                failures.append(
                    f"did not cite “{value}” — cited "
                    f"{', '.join(outcome.cited[:3]) or 'nothing'}"
                )

        elif kind == "cites_first":
            if not outcome.cited:
                failures.append(f"cited nothing, so “{value}” cannot be first")
            elif _loose(value) not in _loose(outcome.cited[0]):
                failures.append(
                    f"cited “{outcome.cited[0]}” first, not “{value}”"
                )

        elif kind == "cites_something":
            if not outcome.cited:
                failures.append(
                    "answered but cited nothing, so none of it can be checked"
                )

        elif kind == "says":
            if _loose(value) not in _loose(outcome.answer):
                failures.append(f"did not say “{value}”")

        elif kind == "does_not_say":
            if _loose(value) in _loose(outcome.answer):
                failures.append(f"said “{value}”, which it must not")

        else:
            failures.append(f"unknown expectation “{kind}”")

    return failures


def _loose(text: str) -> str:
    """Case and whitespace folded, punctuation kept.

    Punctuation survives because the assertions that matter most are about
    figures — "£1,500" and "£1500" are different claims, and a comparison that
    erased the difference would pass the day the number changed.
    """
    return re.sub(r"\s+", " ", (text or "").strip().lower())
