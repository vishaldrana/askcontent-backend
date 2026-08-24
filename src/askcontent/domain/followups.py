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
    furniture = _shared_suffix([getattr(c, "title", "") or "" for c in citations])

    def add(subject: str, because: str) -> None:
        """`subject` is the heading or title, before it is phrased as a
        question. The restatement test has to run on it rather than on the
        finished sentence: "What does the documentation say about X" carries
        six words of scaffolding that dilute the overlap with the question and
        let a pure restatement through."""
        terms = _terms(subject)
        if asked and terms and len(terms & asked) / len(terms) >= _RESTATEMENT:
            return

        text = _as_question(subject, furniture)
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


#: Trailing site furniture on a page title: "Auto loans FAQs | Wells Fargo".
#: Crawled corpora carry it on every page, and a suggestion that repeats the
#: company name back at the reader was written by a crawler rather than by
#: anyone.
#:
#: Only after a pipe. Dashes were tried and are not safe: "Credit Card
#: Questions - Increase Credit Limits" is a title with a dash in it, and
#: stripping the tail threw away the half that said what the page was about.
_SUFFIX = re.compile(r"\s*\|[^|]*$")

#: Words that mean the page is a list of questions rather than a subject.
_FAQ = re.compile(r"\bfaqs?\b", re.IGNORECASE)


def _shared_suffix(titles: list[str]) -> str:
    """The tail that every page carries, which is therefore not about any page.

    Crawled corpora brand every title — "… | Wells Fargo", "… - Wells Fargo".
    A pipe is safe to strip on sight; a dash is not, because "Credit Card
    Questions - Increase Credit Limits" is a title with a dash in it and the
    half after it is the half that says what the page is about.

    So the dash case is decided by evidence rather than by a rule: a tail that
    appears at the end of two or more of the titles in front of us is the site
    talking about itself. One that appears once is content.
    """
    from collections import Counter

    tails: Counter[str] = Counter()
    for title in titles:
        for match in re.finditer(r"[-\u2013\u2014]\s*([^-\u2013\u2014]{2,40})$", title):
            tails[match.group(1).strip()] += 1

    for tail, count in tails.most_common(1):
        if count >= 2:
            return tail
    return ""


def _as_question(subject: str, furniture: str = "") -> str:
    """Turn a heading into something a person would actually type.

    The frame matters more than it looks. This used to produce "What does the
    documentation say about Auto loans FAQs | Wells Fargo?" — which is the
    *system* talking about itself, in the voice of a librarian describing its
    holdings. Nobody asks a question that way. A reader asks about their own
    situation, and a suggestion that does not sound like them does not get
    clicked; worse, it teaches them that this is a search box over documents
    rather than something to ask.

    Headings are already phrased as questions surprisingly often in help
    content, so those pass through untouched — re-wrapping one produces "What
    is How do I reset my password?".
    """
    subject = _SUFFIX.sub("", subject.strip().rstrip(".")).strip()
    if furniture:
        subject = re.sub(
            r"\s*[-\u2013\u2014]\s*" + re.escape(furniture) + r"\s*$", "", subject
        ).strip()
    if not subject:
        return ""
    if subject.endswith("?"):
        return subject

    lowered = subject.lower()
    if lowered.startswith(
        ("how ", "what ", "when ", "where ", "why ", "who ", "can ", "does ", "do i ", "is ")
    ):
        return f"{subject}?"

    if _FAQ.search(subject):
        # "Auto loans FAQs" is a page of questions, so the useful thing to ask
        # is what they are — about the subject, with the label removed.
        stem = _FAQ.sub("", subject).strip(" -–—:").strip()
        if stem:
            return f"What do people usually ask about {stem}?"
        return "What do people usually ask about?"

    # A noun phrase, asked the way somebody with the problem would ask it.
    # Deliberately one frame rather than several chosen at random: a list of
    # suggestions in four different voices reads as four different products.
    return f"What should I know about {subject}?"


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 2}


def _normalise(text: str) -> str:
    """For de-duplication only.

    Deliberately not the topical `_terms` set: that drops tokens shorter than
    three characters, so "Step 1" and "Step 2" normalise identically and one of
    them silently disappears. Case and punctuation are all that is folded.
    """
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))
