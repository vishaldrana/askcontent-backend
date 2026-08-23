"""The grounding prompt, kept apart from any vendor SDK.

This file is the actual product. Retrieval quality sets the ceiling on what can
be answered; this prompt decides whether what comes back can be trusted.

Every rule here exists because of a specific failure:

  * **Cite every claim.** Without it the model writes a fluent paragraph and
    the reader has no way to check any sentence in it.
  * **Say when the passages do not answer.** Without it "Who are you?" returns
    a confident paragraph assembled from whatever ranked highest, which is the
    single most damaging thing a content assistant can do.
  * **Never use outside knowledge.** The model knows a great deal about most
    subjects. All of it is wrong here, because the question being asked is
    always "what does *our documentation* say", and a correct-sounding answer
    that is not in the corpus is indistinguishable from one that is until
    somebody acts on it.
  * **Quote dates and numbers exactly.** Paraphrased numbers are the errors
    nobody catches in review.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...ports.answerer import Passage

SYSTEM = """\
You answer questions strictly from the numbered passages supplied with each \
question. The passages are extracts from an organisation's own documents.

Rules, in order of importance:

1. Use ONLY the passages. You have broad knowledge of the world; none of it is \
admissible here. The question is always "what do these documents say", never \
"what is true".

2. Cite. Every factual sentence ends with the numbers of the passages that \
support it, like [2] or [1][4]. A sentence with no citation is a defect.

3. If the passages do not contain the answer, reply with exactly:
   NOT_IN_CORPUS: <one sentence naming what is missing>
   Do this even when the passages are on a related topic. A near-miss answered \
as if it were a hit is worse than no answer. Do not pad it with what the \
passages do happen to say.

4. Quote figures, dates, thresholds, names and identifiers exactly as written. \
Never round, convert or restate them.

5. If passages disagree, say so and attribute each position to its passage. \
Do not silently prefer one.

6. Answer in plain prose. Be brief — usually two to five sentences. Use a short \
list only when the passages are themselves a list of steps. Do not open with a \
preamble, do not restate the question, and do not describe the passages ("the \
documents say...") — just answer, with citations.
"""


def render(question: str, passages: Sequence[Passage],
           history: Sequence[tuple[str, str]] = ()) -> str:
    """The user turn: the evidence, then the question.

    Evidence goes first because it is what the answer must be built from, and
    the question last because that is what the model should be holding in mind
    as it starts writing.
    """
    blocks: list[str] = []
    for passage in passages:
        where = " > ".join(passage.heading_path) if passage.heading_path else ""
        header = f"[{passage.number}] {passage.title}"
        if where:
            header += f" — {where}"
        if passage.updated:
            header += f" (updated {passage.updated})"
        blocks.append(f"{header}\n{passage.text.strip()}")

    parts = ["PASSAGES", "", "\n\n".join(blocks) if blocks else "(none)"]

    if history:
        # Prior turns disambiguate the question ("and in Texas?"). They are
        # explicitly not evidence: a fact whose passage has scrolled out of
        # this turn's evidence cannot be cited, so it cannot be asserted.
        parts += ["", "EARLIER IN THIS CONVERSATION (for resolving what the "
                  "question refers to — not a source of facts)"]
        for asked, answered in history[-4:]:
            parts.append(f"Q: {asked}\nA: {answered}")

    parts += ["", "QUESTION", question]
    return "\n".join(parts)
