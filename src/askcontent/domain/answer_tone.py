"""How the assistant should sound.

Free text, deliberately, in a schema that closes almost every other grammar it
has. A scope, an expectation kind, a sensitivity — those are closed because the
*system* reasons about the value. Nothing reasons about this one: it is handed
to a model as English. Three fixed levels were therefore not a grammar, they
were three opinions about voice, held by whoever wrote the enum rather than by
the person whose product it is.

A help centre wants "answer like a colleague explaining it at a desk". A policy
library wants "quote the clause and state its date". Neither of those is brief,
standard or full.

What is *not* free text is where it goes. Tone is written into the prompt
before the grounding rules, never after, for the same reason the connector's
own instructions are: an instruction that arrives last is the one that is
followed, and nothing an administrator types should be able to switch off
attribution.
"""

from __future__ import annotations

#: Long enough for a paragraph of guidance, short enough that nobody pastes a
#: style guide in here and pushes the passages out of the model's attention.
MAX_CHARS = 1200

#: The default, and the reason it is a paragraph rather than a word: every rule
#: in the grounding prompt is about not saying *more* than the passages
#: support. None is about saying less — so with no instruction at all, a model
#: economises and a six-step procedure comes back as a sentence.
DEFAULT = (
    "Be thorough and write like a knowledgeable colleague. Give the answer, "
    "then everything the passages support that a reader would otherwise have "
    "to ask for next: the steps with the screens and controls they name, the "
    "preconditions, the limits and exact figures, and what happens afterwards. "
    "Use a numbered list for a procedure, a bulleted list for options, a table "
    "when two things are compared, and headings when the answer covers more "
    "than one subject."
)


class Preset:
    """A starting point, not a setting. Clicking one fills the box."""

    def __init__(self, name: str, blurb: str, text: str) -> None:
        self.name, self.blurb, self.text = name, blurb, text

    def as_dict(self) -> dict:
        return {"name": self.name, "blurb": self.blurb, "text": self.text}


PRESETS = [
    Preset(
        "Thorough",
        "Everything the passages support, structured.",
        DEFAULT,
    ),
    Preset(
        "Conversational",
        "Like a colleague explaining it at your desk.",
        "Write the way a helpful colleague would explain it out loud. Open with "
        "one sentence saying what the thing is or what the procedure achieves, "
        "then give the detail. Use 'you' and plain words, contractions where "
        "they read naturally, and no throat-clearing — no 'Great question', no "
        "restating the question back.",
    ),
    Preset(
        "Concise",
        "The direct answer and its one condition.",
        "Answer in as few sentences as the question needs — usually one or two. "
        "Give the direct answer and the single most important condition on it, "
        "then stop. No background, no adjacent topics, no summary of what you "
        "just said.",
    ),
    Preset(
        "Professional",
        "Plain and businesslike, no informality.",
        "Write plainly and impersonally, as internal documentation would. No "
        "contractions, no exclamation marks, no first person. State the "
        "position, the conditions on it, and the date or version the passages "
        "give, and attribute anything contested to the document that says it.",
    ),
    Preset(
        "Step by step",
        "Procedures first, one action per line.",
        "Lead with the procedure. Every instruction is a numbered step naming "
        "the exact screen, button or field the passages name, one action per "
        "step. Put preconditions above the steps and consequences below them, "
        "and keep prose to the sentence that says what the procedure achieves.",
    ),
    Preset(
        "Careful",
        "For regulated content: quote, date, attribute.",
        "Quote the passages closely rather than paraphrasing, and give the date "
        "and version of every document you rely on. Where two passages differ, "
        "state both positions and attribute each. Never smooth over a gap — if "
        "the passages do not cover part of the question, say which part.",
    ),
]


def normalise(value: str | None) -> str:
    """The tone as it will be used: trimmed, capped, or the default."""
    text = (value or "").strip()
    if not text:
        return DEFAULT
    return text[:MAX_CHARS]


def presets() -> list[dict]:
    return [p.as_dict() for p in PRESETS]
