"""What to ask next, constructed rather than generated.

A model asked for follow-up questions writes plausible ones. Plausible is the
problem: it will happily suggest "What is the refund window for enterprise
plans?" about a corpus that has never mentioned refunds, and the reader clicks
it and gets nothing. A suggestion that cannot be answered is worse than no
suggestion, because it advertises coverage the corpus does not have and spends
the reader's trust to do it.

So suggestions here are *derived from documents that were actually retrieved*.
Every one names a heading, a sibling page or a term that exists in the corpus,
which makes "is this answerable" a property of how the suggestion was built
rather than a hope about what a model wrote.

This module is pure: no I/O, no model call. It is given the citations an answer
used and returns questions built from them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Headings that describe the page's furniture rather than a subject. A
#: suggestion built from one of these reads as a non-question.
_EMPTY_HEADINGS = frozenset(
    "introduction overview contents summary about index home page notes "
    "see also related links references appendix faq faqs".split()
)

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")

#: "6. We are done linking the URL" is a step in a procedure, not a subject.
#: Help content uses numbered steps as headings constantly, and a question
#: built from one reads as a fragment of somebody else's instructions.
_STEP = re.compile(r"^\s*(?:step\s*)?\d+[.):]\s")

#: Above this share of a candidate's words already present in the question,
#: the suggestion is a restatement. Exact-string matching is not enough:
#: "How do I add a hyperlink to survey text?" and "…about Adding Hyperlink to
#: Survey Text" are the same request in different clothes.
_RESTATEMENT = 0.7


@dataclass(frozen=True)
class Followup:
    question: str
    #: Why this is answerable — the document or heading it was built from.
    because: str


def suggest(citations, *, question: str = "", limit: int = 4) -> list[Followup]:
    """Questions the corpus can answer, given what this answer cited.

    Ordered by how directly the source supported the answer: the first
    suggestion comes from the best-ranked citation, because that is the thread
    a reader is most likely to want to pull.
    """
    asked = _terms(question)
    out: list[Followup] = []
    seen: set[str] = set()

    def add(subject: str, because: str) -> None:
        """`subject` is the heading or title, before it is phrased as a
        question. The restatement test has to run on it rather than on the
        finished sentence: "What does the documentation say about X" carries
        six words of scaffolding that dilute the overlap with the question and
        let a pure restatement through."""
        terms = _terms(subject)
        if asked and terms and len(terms & asked) / len(terms) >= _RESTATEMENT:
            return

        text = _as_question(subject)
        key = _normalise(text)
        if not key or key in seen or key == _normalise(question):
            return
        seen.add(key)
        out.append(Followup(text, because))

    for citation in citations:
        if len(out) >= limit:
            break

        path = tuple(getattr(citation, "heading_path", ()) or ())
        title = (getattr(citation, "title", "") or "").strip()

        # A sub-heading under the cited section is the most reliable kind of
        # suggestion there is: the passage that answers it has already been
        # parsed and is one chunk away.
        for heading in reversed(path):
            heading = heading.strip()
            if not heading or heading.lower() in _EMPTY_HEADINGS:
                continue
            if _STEP.match(heading):
                continue
            if _terms(heading) <= asked:
                continue  # nothing new to ask about
            add(heading, f"a section of “{title}”")
            break

        if title and title.lower() not in _EMPTY_HEADINGS and not _terms(title) <= asked:
            add(title, f"the document “{title}”")

    return out[:limit]


def _as_question(subject: str) -> str:
    """Turn a heading into something a person would type.

    Headings are already phrased as questions surprisingly often in help
    content, and re-wrapping one produces "What is How do I reset my password?".
    """
    subject = subject.strip().rstrip(".")
    if subject.endswith("?"):
        return subject
    lowered = subject.lower()
    if lowered.startswith(("how ", "what ", "when ", "where ", "why ", "who ", "can ", "does ")):
        return f"{subject}?"
    # "Survey Templates" -> "Tell me about Survey Templates" reads as filler.
    # Naming the corpus's own words back is what makes it feel answerable.
    return f"What does the documentation say about {subject}?"


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 2}


def _normalise(text: str) -> str:
    """For de-duplication only.

    Deliberately not the topical `_terms` set: that drops tokens shorter than
    three characters, so "Step 1" and "Step 2" normalise identically and one of
    them silently disappears. Case and punctuation are all that is folded.
    """
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))
