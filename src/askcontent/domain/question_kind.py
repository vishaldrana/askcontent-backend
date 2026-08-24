"""What kind of question is this?

Retrieval answers questions *from* the corpus. Some questions are *about* it —
"what can you tell me", "what do you know", "what's in here", "what topics do
you cover" — and running those through retrieval produces the worst possible
outcome: a refusal, on a question the system can answer perfectly well, which
reads as the assistant knowing nothing at all. It is the first thing many
people type, so it is the first impression the product makes.

The answer to such a question is not in any document. It is in the shape of the
corpus: how many documents, what the sections are called, what the glossary
holds. That is real data and it is answered from real data — the same
discipline as the follow-up suggestions, which are constructed from what exists
rather than generated and hoped over.

**Conservative by construction.** A question is only *about* the corpus when it
matches one of these phrasings **and has no subject of its own**. "What can you
tell me?" has no subject. "What can I tell my customers about NPS?" has two,
and is a content question that merely contains the word "tell". Requiring the
absence of a subject is what keeps this from hijacking real questions, and it
is why the check is a conjunction rather than a keyword list.

**The verb list is the part that keeps being too short.** "What can you answer
then?" is what somebody types straight after a refusal — the one moment they
most need an orientation and the one moment a second refusal is most
damaging — and it went to retrieval because the phrasing list knew *tell*,
*ask*, *do* and *help* but not *answer*. Each addition here is a real question
somebody asked and did not get an answer to.
"""

from __future__ import annotations

import re
from enum import StrEnum

from .groundedness import content_terms


class QuestionKind(StrEnum):
    #: Answer from the documents. Everything not caught below.
    CONTENT = "content"
    #: About the corpus itself — what is here, what can be asked.
    SCOPE = "scope"
    #: A greeting or a pleasantry. Answering it from documents is absurd, and
    #: refusing it is rude in a way people remember.
    SOCIAL = "social"


#: Phrasings that ask what the assistant holds. Deliberately about *capability
#: and contents*, never about a subject.
_SCOPE = re.compile(
    r"\b("
    r"what (?:all |else )?can (?:you|i) (?:tell|ask|answer|do|help|cover|find)"
    r"|what else (?:do|can) you"
    r"|what (?:questions|else) can i ask"
    r"|what (?:do|can) you know"
    r"|what (?:are you|is this) (?:for|about)"
    r"|what(?:'s| is) (?:in )?(?:here|this)"
    r"|what (?:kind of )?(?:topics|subjects|things|questions|documents|content)"
    r"|what should i ask"
    r"|how can you help"
    r"|who are you|what are you"
    r"|help me|^help$"
    r")\b",
    re.I,
)

_SOCIAL = re.compile(
    r"^\s*(hi|hey|hello|good (?:morning|afternoon|evening)|thanks|thank you"
    r"|cheers|ok|okay|got it|bye|goodbye)\b[\s!.,?]*$",
    re.I,
)

#: A scope question may name at most this many subjects of its own. One is
#: allowed because "what can you tell me about surveys" is a real request to
#: orient within a topic — and is still better served by an overview than by a
#: refusal.
MAX_SUBJECT_TERMS = 1


def classify(question: str) -> QuestionKind:
    text = (question or "").strip()
    if not text:
        return QuestionKind.CONTENT

    if _SOCIAL.match(text):
        return QuestionKind.SOCIAL

    match = _SCOPE.search(text)
    if match:
        # The words forming the scope phrase are not subjects. Counting them
        # would make "what topics do you cover" look like a question about
        # topics and covering, and it would never be recognised.
        remainder = text[: match.start()] + " " + text[match.end() :]
        if len(content_terms(remainder)) <= MAX_SUBJECT_TERMS:
            return QuestionKind.SCOPE

    return QuestionKind.CONTENT
