"""Is this question answerable from what we retrieved?

WHY THIS IS A SEPARATE DECISION
===============================
Retrieval always returns something. That is what retrieval *is*: rank the
corpus against the question and hand back the top of the list. Ask a survey
product's help centre how much parental leave a primary caregiver gets and it
will dutifully return twelve passages about NPS, CSAT and Zendesk workflows,
each with a plausible-looking score, because they were the least-bad match
available. Nothing in the ranking says "none of these are about the question" —
a rank is an ordering, not a judgement.

So the judgement has to be made explicitly, and this is where. It runs *before*
the answer is composed, so that a question the corpus does not cover produces
one honest sentence and an empty evidence panel, rather than an answer stitched
from the least-bad matches and twelve passages implying they support it.

WHY NOT A MODEL
===============
It could be a model call, and it would be a good one. It is deliberately not:

  * it runs on every question, so it is the hottest path in the product;
  * a model here fails *open* — an outage would restore exactly the behaviour
    this exists to prevent;
  * "did the corpus actually contain these words" is a factual question about
    two strings, and asking a language model to judge lexical coverage is
    paying a lot for a worse answer.

The signal it uses is coverage: how much of what the question is *about*
appears in the retrieved text at all. That is deliberately a low bar. It is not
trying to decide whether the passages answer the question well — the answerer
does that, with the passages in front of it. It is only catching the case where
they are not on the subject, which is the case that produces confident nonsense.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")

#: Words whose presence says nothing about what a question is about. Overlap on
#: "how" and "the" is not evidence of anything.
STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does doing for from
had has have having he her hers him his how i if in into is it its me might
much my no nor not of on or our ours out should so some such than that the
their theirs them then there these they this those to us was we were what when
where which while who whom why will with would you your yours about after
again against all also any because before below between both during each few
more most other over same then through under until up very
""".split())

#: Verbs and nouns that are about *asking*, not about the subject. They appear
#: in nearly every question and would otherwise inflate coverage.
INTERROGATIVE = frozenset("""
tell explain describe give show list get find need want know understand say
question answer information detail details please help
""".split())


@dataclass(frozen=True)
class Groundedness:
    """The verdict, with the evidence for it.

    Every field is here so the console can explain a refusal. "Not covered" with
    no reason is indistinguishable from a bug, and support cannot act on it.
    """

    covered: bool
    coverage: float
    matched: tuple[str, ...]
    missing: tuple[str, ...]

    def reason(self) -> str:
        if self.covered:
            return f"{self.coverage:.0%} of the question's terms appear in the evidence"
        # Always name what was missing, including — especially — when nothing
        # matched at all. That is the most common refusal, and a message that
        # says only "not covered" leaves support with nothing to act on.
        absent = ", ".join(self.missing[:4])
        if not self.matched:
            return f"nothing retrieved mentions {absent}"
        return (
            f"only {self.coverage:.0%} of the question's terms appear in the "
            f"evidence; nothing retrieved mentions {absent}"
        )


def content_terms(text: str) -> set[str]:
    """The words that carry what a piece of text is *about*."""
    words = {w for w in _WORD.findall(text.lower()) if len(w) > 2}
    return words - STOPWORDS - INTERROGATIVE


def assess(question: str, passages: list[str], *, floor: float = 0.34) -> Groundedness:
    """Does the retrieved text cover what the question is about?

    `floor` is the share of the question's content words that must appear
    somewhere in the retrieved passages. It is set low on purpose. Raising it
    starts rejecting real questions whose vocabulary differs from the
    documentation's ("terminate a respondent" vs "disqualify"); the answerer,
    which can see the passages, is the right place for that finer judgement.

    Singular and plural forms are treated as the same word, because a corpus
    that says "surveys" plainly covers a question that says "survey", and
    failing on that would be a spelling test rather than a relevance test.
    """
    asked = content_terms(question)
    if not asked:
        # A question made entirely of stopwords ("what is it about?") has no
        # subject of its own. It is not un-answerable — it usually refers back
        # to the conversation — so it is not blocked here.
        return Groundedness(True, 1.0, (), ())

    available = content_terms(" ".join(passages))
    stems = {_stem(w) for w in available}

    matched = {w for w in asked if w in available or _stem(w) in stems}
    coverage = len(matched) / len(asked)
    return Groundedness(
        covered=coverage >= floor,
        coverage=round(coverage, 3),
        matched=tuple(sorted(matched)),
        missing=tuple(sorted(asked - matched)),
    )


def _stem(word: str) -> str:
    """Crude, and deliberately so — enough to join singular to plural without
    pulling in a stemmer whose behaviour nobody on the team can predict."""
    for suffix in ("ies", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word
