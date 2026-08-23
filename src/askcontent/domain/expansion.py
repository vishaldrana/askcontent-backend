"""Rewriting a question in the corpus's own vocabulary.

The most common way retrieval fails on a help centre is not subtlety. It is
that the reader types the word they use and the documentation uses a different
one — "cancel my subscription" against a corpus that says "terminate plan",
"POA" against pages that spell out "power of attorney".

Embeddings absorb some of this and reliably fail on the rest: acronyms and
coined product names are *strings*, not meanings, and two unrelated
capitalised tokens sit no closer together in vector space for being synonyms
inside one company. The glossary is exactly the list of those, and until now
it was collected, displayed and never read at query time.

Three rules, and each is a way this goes wrong if ignored:

  * **Expansion adds; it never replaces.** The reader's own words are the
    strongest signal there is. Swapping them for the canonical term throws
    that away and, when the glossary is wrong, makes the question
    unanswerable in a way nobody can see.
  * **Whole words only.** A naive substring match turns "important" into a hit
    for the term "port", and the resulting query is worse than the original.
  * **Bounded.** A question that matches six terms must not become a paragraph:
    past a handful of additions the query stops being about anything, and
    vector search returns the centroid of the corpus.

Pure: no I/O, no model. The terms are handed in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Beyond this many added phrases the query is no longer a question. Chosen
#: low: two or three good synonyms help, ten dilute.
MAX_ADDITIONS = 4

#: Terms shorter than this are matched case-sensitively. "IT" and "it" are not
#: the same word, and expanding every "it" into "information technology" ruins
#: every question that contains a pronoun.
CASE_SENSITIVE_BELOW = 4


@dataclass(frozen=True)
class Term:
    term: str
    aliases: tuple[str, ...] = ()

    def surface_forms(self) -> tuple[str, ...]:
        return (self.term, *self.aliases)


@dataclass
class Expansion:
    """The rewritten question, and what was added to it."""

    question: str
    added: list[str] = field(default_factory=list)
    #: Which term matched which surface form, for the trace. An expansion
    #: nobody can attribute is one nobody can correct.
    matched: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added)


def expand(question: str, terms: list[Term], *, limit: int = MAX_ADDITIONS) -> Expansion:
    """Append the corpus's words for anything the question already names.

    The result is the original question followed by the additions. Order
    matters for the lexical channel, which weights earlier terms more heavily —
    the reader's phrasing should stay in front.
    """
    result = Expansion(question=question)
    if not question.strip() or not terms:
        return result

    already = _words(question)

    for entry in terms:
        if len(result.added) >= limit:
            break

        hit = _first_match(question, entry.surface_forms())
        if hit is None:
            continue

        # Add the forms the question does not already contain. A term matched
        # by its own name still contributes its aliases, which is the case that
        # matters most: somebody writing "POA" gets "power of attorney".
        additions = [
            form
            for form in entry.surface_forms()
            if form.lower() != hit.lower() and not _words(form) <= already
        ]
        if not additions:
            continue

        for form in additions[: limit - len(result.added)]:
            result.added.append(form)
            result.matched.append((hit, form))
            already |= _words(form)

    if result.added:
        result.question = f"{question} {' '.join(result.added)}"
    return result


def _first_match(question: str, forms: tuple[str, ...]) -> str | None:
    """The first surface form that appears as a whole word or phrase.

    Longest first, so a corpus containing both "plan" and "enterprise plan"
    matches the more specific one and does not expand on the generic.
    """
    for form in sorted(forms, key=len, reverse=True):
        if not form.strip():
            continue
        flags = 0 if len(form) < CASE_SENSITIVE_BELOW else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(form)}(?!\w)", question, flags):
            return form
    return None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))
