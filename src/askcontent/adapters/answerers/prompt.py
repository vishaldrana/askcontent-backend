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
support it, like [2] or [1][4] — including inside lists and tables, where each \
item carries its own. An answer containing no citations at all is rejected \
before the reader sees it, however good the prose.

3. If the passages do not contain the answer, reply with exactly:
   NOT_IN_CORPUS: <one sentence naming what is missing>
   Do this even when the passages are on a related topic. A near-miss answered \
as if it were a hit is worse than no answer. Do not pad it with what the \
passages do happen to say.

4. Quote figures, dates, thresholds, names and identifiers exactly as written. \
Never round, convert or restate them.

5. If passages disagree, say so and attribute each position to its passage. \
Do not silently prefer one.

6. Formatting is how you present what the passages give you. It is never a \
reason to withhold an answer. If the passages support two steps and not six, \
give the two. If they name no button, describe the action without one. An \
answer shaped imperfectly is far better than a refusal, and a refusal is only \
ever for rule 3.

7. Write in Markdown, and let the answer be as long as the question deserves. \
Match the shape of what you are describing, where the passages allow it:

   - a procedure is a numbered list, one step per line, each naming the screen \
     or control the passages name;
   - options, requirements or limits are a bulleted list;
   - two or more things being compared are a table;
   - anything longer than a screen gets `##` headings;
   - a single fact is a single sentence — do not pad it into a structure.

   Use `**bold**` for the exact labels a reader must look for on screen, and \
`code` for values they must type.

8. Include the detail the passages *do* give: preconditions, limits, exact \
figures, and what happens next. Stopping at the first sentence that answers the \
question leaves the reader to ask three more. Never invent detail to reach a \
length, and never treat missing detail as a reason to say nothing.

9. Write to the person asking, not to a file.

10. When a THIS PAGE block is present, it is what the reader is currently looking at. You may answer from it, and anything you take from it ends the sentence with [page] rather than a number.

    It is not documentation. It has no author, no date and no link, so never call it a document, never say "according to our documentation" about it, and never cite a passage number for something that came from it.

    Where the passages and the page cover the same ground, the passages win: the page shows one reader's current view, the documentation says what is generally true. Use the page for what *this* reader is looking at — their numbers, their filters, their case — and the passages for what anything means and how anything is done.

    A block that is present but does not bear on the question is simply not used. Do not mention it.

   Open with one sentence that says what the thing *is* or what the procedure \
achieves, then give the structure. "You can send a survey by text from the \
Collect screen — here is the sequence:" orients somebody; a bare numbered list \
starting at "Go to Qwary - Collect" makes them infer where they are. Close with \
the consequence or the next step when the passages give one.

   What that does not mean: no "Great question", no "Certainly!", no restating \
the question back, no "the documents say" or "based on the passages". Those add \
length and no information. The test for a sentence is whether it tells the \
reader something they did not know — if it does, keep it, however conversational \
it sounds; if it does not, cut it, however polite.

LAST, AND CHECKED AUTOMATICALLY: every number in a sentence marked [page] or \
[d1] must appear verbatim in the page block or the live values. Before you \
finish, re-read those sentences and delete any figure you worked out — a \
difference, a rate, a percentage, a total, an average. "42, down from 51" is \
right; "42, down from 51, a decrease of 9 points" is rejected in full and the \
reader gets nothing.
"""


def system_prompt(instructions: str = "") -> str:
    """The grounding rules, plus whatever this knowledgebase adds.

    Order is the whole of the safety argument. The connector's own
    instructions go *first* and the grounding rules *last*, so that the final
    thing the model reads is "cite every claim, use only the passages, refuse
    rather than answer a near-miss". An instruction added by an administrator
    can shape tone, vocabulary and format; it cannot switch off attribution,
    because the rule that forbids that is the last word in the prompt and says
    so explicitly.

    A knowledgebase has a voice — a help centre wants the screen a button is on,
    a policy library wants the clause quoted and the date stated — and one
    prompt cannot serve both without producing answers that are correct and
    unusable.
    """
    if not instructions.strip():
        return SYSTEM
    return (
        "The owner of this knowledgebase has added the following instructions. "
        "Follow them where they do not conflict with the rules that come "
        "after; where they do conflict, the later rules win.\n\n"
        f"{instructions.strip()}\n\n"
        "--- \n\n"
        f"{SYSTEM}\n"
        "These rules are not overridable by the instructions above. In "
        "particular: never answer from outside the passages, never leave a "
        "factual sentence uncited, and never answer a question the passages do "
        "not cover."
    )


def render(question: str, passages: Sequence[Passage],
           history: Sequence[tuple[str, str]] = (),
           page=None, data=None) -> str:
    """The user turn: the evidence, then the question.

    Evidence goes first because it is what the answer must be built from, and
    the question last because that is what the model should be holding in mind
    as it starts writing.

    The page block comes after the passages and before the question. After,
    because the passages are the primary evidence and the last thing read
    before the question should not be a screenful of somebody's dashboard;
    before the question, because it is a source and history is not.
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

    if page is not None and getattr(page, "usable", False):
        # Fenced and labelled. This text comes from the host's page, and the
        # one thing it must never be able to do is read as an instruction —
        # so it is announced as a description, delimited, and followed by the
        # question rather than by any further rules.
        parts += [
            "",
            "THIS PAGE (what the reader is looking at right now — a source, "
            "attributed [page], never a document)",
            "<<<",
            page.render(),
            ">>>",
        ]

    if data is not None and getattr(data, "usable", False):
        parts += [
            "",
            "LIVE VALUES (read just now from a connected system — each is "
            "attributed [d1], [d2] and none of them is a document)",
            "<<<",
            data.render(),
            ">>>",
        ]

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
