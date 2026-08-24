"""Telling a decline apart from a violation.

An answer that cites nothing is normally a fault: something was asserted and
there is no way to check it. That rule is what keeps the assistant honest, and
it fires on the one case where citing nothing is exactly right —

    Q: Can I refinance auto loans?
    A: The passages do not mention whether you can refinance auto loans.

— which the reader then sees flagged, with a warning underneath explaining
that the answer cited nothing and so cannot be checked. Both statements are
true and together they read as the system doubting itself: the answer says the
corpus is silent, and the system responds by treating that as a defect in the
answer.

A decline has nothing to cite *because* there is nothing there. It is still
not a supported answer -- the citation list stays empty and the turn is not
counted as answered -- but it is not a violation, and it is not reported as
one. The distinction is only ever drawn on an answer that cited nothing at
all, so a long grounded answer carrying one hedging sentence cannot reach it.
"""

from __future__ import annotations

import re

#: What a decline is *about*. This is the load-bearing half: "does not cover"
#: is not a decline in "The policy does not cover flood damage", and "does not
#: include" is a perfectly ordinary answer in "Autopay does not include the
#: final payment". The same verbs mean opposite things depending on whether
#: the subject is the corpus or something in the world, so the subject is
#: required rather than the verb being made rarer.
_CORPUS = re.compile(
    r"\b(passages?|documents?|docs?|knowledge ?base|corpus|sources?|excerpts?"
    r"|content|context|material|records?|articles?|retrieved \w+"
    r"|provided (?:context|text|documents?|passages?)"
    r"|(?:pages?|text|information) (?:provided|supplied|given|here))\b",
    re.I,
)

#: "It is not there" -- only a decline when said about the corpus.
_ABSENT = re.compile(
    r"\b((?:do|does|did)(?:n't| not) (?:mention|say|state|specify|address"
    r"|cover|contain|include|provide|indicate|describe|discuss)"
    r"|(?:is|are|was|were) not (?:mentioned|stated|specified|covered"
    r"|addressed|described|included|discussed))\b",
    re.I,
)

#: Phrasings that name the corpus implicitly and need no subject beside them.
_ALONE = re.compile(
    r"\b((?:could|can) ?not find|couldn't find|cannot find|unable to find"
    r"|no (?:information|mention|details?|guidance) (?:about|on|regarding|for)"
    r"|nothing (?:in|about|on) (?:this|the) (?:knowledge ?base|corpus"
    r"|passages?|documents?|page))\b",
    re.I,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def declines(answer: str) -> bool:
    """Whether this answer is saying the corpus does not hold the answer.

    Judged per sentence, because the subject and the verb have to belong to
    each other: "The policy does not cover that, and the documents describe
    the rest" is two sentences and neither of them is a decline.
    """
    for sentence in _SENTENCE.split(answer or ""):
        if _ALONE.search(sentence):
            return True
        if _ABSENT.search(sentence) and _CORPUS.search(sentence):
            return True
    return False
