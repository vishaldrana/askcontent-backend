"""How much of what the passages support an answer should say.

A closed grammar with three values, like every other setting that changes what
a reader sees. Not a slider: "detail level 7" means nothing to the person
setting it and nothing to the model reading it, and the three that matter are
genuinely different jobs rather than points on a line.

The default is `full`, and it is the default because the failure it prevents
is the common one. An assistant that answers a procedure with a sentence sends
the reader back to the search box, and every rule in the grounding prompt is
about not saying more than the passages support — none of them is about saying
less, so without an instruction the model economises.
"""

from __future__ import annotations

LEVELS = ("brief", "standard", "full")

DEFAULT = "full"

#: What each level adds to the prompt. Written as instructions about *what to
#: include*, never about length: "be concise" produces a shorter answer by
#: dropping whichever facts came last, which is not the same as a brief one.
INSTRUCTIONS = {
    "brief": (
        "Answer in as few sentences as the question needs — usually one or "
        "two. Give the direct answer and the single most important condition "
        "on it, and stop. Do not add background, related settings or what to "
        "do next unless the question asked for them."
    ),
    "standard": (
        "Give the answer and what a reader needs to act on it: the steps, the "
        "preconditions and the limits the passages state. Leave out background "
        "and adjacent topics."
    ),
    "full": (
        "Be thorough. Give the answer, then everything the passages support "
        "that a reader would otherwise have to ask for next: the exact steps "
        "with the screens and controls they name, the preconditions, the "
        "limits and figures, what happens afterwards, and the exceptions. "
        "Use headings when the answer covers more than one thing, a numbered "
        "list for a procedure, a table when two or more things are compared. "
        "Stopping at the first sentence that answers the question leaves the "
        "reader to ask three more.\n\n"
        "This is not licence to pad. Never invent detail to reach a length, "
        "and never repeat a passage twice in different words. Every sentence "
        "must carry something the passages say and the reader did not know."
    ),
}


def normalise(value: str | None) -> str:
    return value if value in LEVELS else DEFAULT


def instruction(value: str | None) -> str:
    return INSTRUCTIONS[normalise(value)]
