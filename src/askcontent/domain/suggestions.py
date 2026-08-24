"""Judging a suggested question before a reader is offered it.

The old rule was "construct, never generate", and it was half right. Generating
suggestions from nothing produces questions the corpus cannot answer — "What is
the refund window for enterprise plans?" over a corpus that has never mentioned
refunds — and a suggestion that returns nothing is worse than none, because it
advertises coverage that is not there and spends the reader's trust to do it.

But constructing them from headings produced text nobody would ever type:

    What should I know about You have questions, we have answers?
    How can we help?
    What should I know about Application?

The first is a marketing tagline. The second is the *site* asking the reader,
echoed back at them as though they had asked it. The third is a fragment of a
navigation label. Each was answerable and none was a question.

So the split moves: the *subject* is still constrained to what is demonstrably
in front of us, and the *phrasing* is written by something that can write. This
module is the gate between them — what a suggestion has to survive before it is
shown, whoever wrote it.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")

#: The share of a suggestion's meaningful words that must appear in the source
#: it claims to be answerable from. Not a similarity score — a presence test.
#: A question about "closing an account" over passages that never say "closing"
#: is a question this corpus cannot answer, whoever wrote it.
GROUNDED_AT = 0.6

#: Questions the *site* asks the reader. Echoed back they read as nonsense —
#: the reader did not ask how we can help, we asked them.
_SITE_VOICE = re.compile(
    r"^\s*(how can (we|i) help|what can (we|i) (help|do)|can we help|need help"
    r"|we're here to help|how may (we|i) help)",
    re.IGNORECASE,
)

#: Marketing copy that ends up in a heading. Not an exhaustive list and does
#: not need to be: these are the shapes that survive every other filter.
_SLOGAN = re.compile(
    r"(you have questions|we have answers|we're here for you|here to help"
    r"|let('| )s get started|welcome to)",
    re.IGNORECASE,
)

#: Below this a "subject" is a navigation label — "Rates", "Application",
#: "Overview" — and a question built on it asks about nothing in particular.
MIN_SUBJECT_WORDS = 2

_STOP = frozenset(
    "the a an and or of for to in on at by with from is are was were be been "
    "do does did can could should would will what when where why how who i we "
    "you my our your me us it its this that these those about".split()
)


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")} - _STOP


def is_a_question(text: str) -> bool:
    """Reads as something a person would type into a box.

    A question mark is not enough and not required — "How do I reset it?" and
    "How do I reset it" are both questions, "Application" is not either way.
    """
    words = [w for w in _WORD.findall(text) if w.lower() not in _STOP]
    if len(words) < MIN_SUBJECT_WORDS:
        return False
    if _SITE_VOICE.search(text) or _SLOGAN.search(text):
        return False
    return True


def is_grounded(question: str, source: str) -> bool:
    """Enough of the question's own words are in the text it will be answered from.

    This is what lets the phrasing be written freely: however a suggestion is
    worded, it only survives if the corpus in front of us actually talks about
    it. The guarantee moves from *how it was made* to *what it says*, which is
    the more useful place for it — a constructed suggestion can be nonsense and
    a written one can be exact.
    """
    asked = _terms(question)
    if not asked:
        return False
    present = _terms(source)
    return len(asked & present) / len(asked) >= GROUNDED_AT


def is_a_restatement(question: str, asked: str) -> bool:
    """The reader just asked this. Offering it back is offering nothing."""
    a, b = _terms(question), _terms(asked)
    if not a or not b:
        return False
    return len(a & b) / len(a) >= 0.7


def keep(
    candidates: list[str],
    *,
    source: str,
    asked: str = "",
    limit: int = 4,
) -> list[str]:
    """The suggestions worth showing, in the order they arrived."""
    out: list[str] = []
    seen: set[str] = set()

    for raw in candidates:
        question = " ".join((raw or "").split()).strip()
        if not question.endswith("?"):
            question = question.rstrip(".") + "?"

        key = " ".join(sorted(_terms(question)))
        if not key or key in seen:
            continue
        if not is_a_question(question):
            continue
        if asked and is_a_restatement(question, asked):
            continue
        if not is_grounded(question, source):
            continue

        seen.add(key)
        out.append(question)
        if len(out) >= limit:
            break

    return out
