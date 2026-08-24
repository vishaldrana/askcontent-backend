"""The offline answerer.

The requirements say the test suite runs with no network and no API key, so
something has to answer when no model is configured. The temptation is to make
that something *look* like an answer — glue the top passage onto a "Based on
X:" prefix and stream it. That is what this system did before, and it produced
confident paragraphs about GDPR in reply to "Who are you?", because a template
cannot tell the difference between evidence that answers a question and
evidence that merely ranked highest.

So this adapter does the opposite of impersonating a model. It:

  * checks whether the retrieved passages actually overlap the question, and
    says plainly that they do not when they do not;
  * quotes rather than paraphrases, because an extractive answerer that
    rewrites is an extractive answerer that lies;
  * labels itself, so nobody mistakes offline mode for the product.

A quoted sentence with a citation is a genuinely useful answer. A fluent
paragraph nobody can check is not, and the gap between them is the whole
argument for this file being written this way.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence

from ...ports.answerer import AnswerChunk, Answerer, Passage

_WORD = re.compile(r"[a-z0-9]+")
#: Words that carry no topical signal, so overlap on them is not evidence that
#: a passage is about the question.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "was", "were", "be", "been", "it", "its", "this", "that", "these",
    "those", "as", "at", "by", "from", "how", "what", "when", "where", "which",
    "who", "why", "do", "does", "did", "can", "i", "you", "we", "they", "my",
    "our", "your", "if", "not", "no", "yes", "there", "here", "about", "into",
    "have", "has", "had", "will", "would", "should", "could", "may", "might",
}

#: Below this share of the question's meaningful words appearing in the
#: passages, the passages are treated as not answering the question. Set by
#: what it has to catch: "Who are you?" against a product help centre.
_COVERAGE_FLOOR = 0.34


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 2]


class ExtractiveAnswerer(Answerer):
    name = "extractive-offline"
    model_id = "extractive-v1"

    async def stream(
        self,
        *,
        question: str,
        passages: Sequence[Passage],
        history: Sequence[tuple[str, str]] = (),
        instructions: str = "",
        # Accepted and ignored. This answerer quotes passages verbatim, and a
        # page summary is not a passage — there is nothing here it could quote
        # without inventing the attribution. The offline fallback answering
        # from documents only is the right degradation.
        page=None,
        data=None,
    ) -> AsyncIterator[AnswerChunk]:
        asked = _terms(question)
        if not passages or not asked:
            yield AnswerChunk(
                text="I could not find anything in this knowledgebase that answers that.",
                done=True, supported=False,
            )
            return

        available = set().union(*(_terms(p.title + " " + p.text) for p in passages))
        covered = asked & available
        if len(covered) / len(asked) < _COVERAGE_FLOOR:
            missing = ", ".join(sorted(asked - available)[:4])
            yield AnswerChunk(
                text=(
                    "Nothing in this knowledgebase addresses that question. "
                    f"The retrieved documents do not mention {missing}."
                ),
                done=True, supported=False,
            )
            return

        # Rank sentences by how much of the question they carry, keeping the
        # best few and quoting them verbatim with their passage numbers.
        scored: list[tuple[float, str, int]] = []
        for passage in passages:
            for sentence in _sentences(passage.text):
                terms = _terms(sentence)
                if not terms:
                    continue
                overlap = len(asked & terms)
                if not overlap:
                    continue
                # Normalised by length so a long paragraph does not win purely
                # by containing more words.
                scored.append((overlap / (len(terms) ** 0.5), sentence, passage.number))

        scored.sort(key=lambda x: -x[0])
        chosen: list[tuple[str, int]] = []
        seen: set[str] = set()
        for _score, sentence, number in scored:
            key = sentence.lower()[:60]
            if key in seen:
                continue
            seen.add(key)
            chosen.append((sentence, number))
            if len(chosen) == 3:
                break

        if not chosen:
            yield AnswerChunk(
                text="The retrieved documents do not contain a statement that "
                     "answers that question.",
                done=True, supported=False,
            )
            return

        for sentence, number in chosen:
            for word in f"{sentence.rstrip('.')} [{number}]. ".split(" "):
                yield AnswerChunk(text=word + " ")

        yield AnswerChunk(
            done=True, supported=True,
            cited=tuple(sorted({n for _, n in chosen})),
        )
