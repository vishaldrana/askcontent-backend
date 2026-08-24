"""Deep research: what a run is allowed to do, and what counts as a finding.

The same four phases as askdb — plan, investigate, verify, synthesise — over a
corpus instead of a database. The shape carries across because the problem
does: one question that no single retrieval answers, broken into ones that do,
each answered from evidence, and assembled into something a person can act on.

Two rules carry the whole thing, and they are the same two:

**Every finding carries its citations.** A finding with no evidence is dropped
at synthesis, not softened. That is what keeps the report honest, and it is
the same rule the ordinary answer path enforces — a research report is a long
answer, and a long answer that cannot be checked is worse than a short one
because there is more of it to believe.

**Every limit is enforced, not advisory.** A run that quietly stopped
investigating and wrote a confident report is the failure this design exists
to prevent, so reaching a limit is named in the output rather than logged.

This module is pure. The phases live in `services/research.py`, which drives
the ordinary retrieval and answering paths — deep research adds no new route
to the corpus, which is why it needs no separate access review.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: What each depth costs, in the units that actually bound a run. Named rather
#: than numbered because "standard" is a decision somebody can defend in a
#: meeting and "level 2" is not.
DEPTH_PRESETS: dict[str, dict[str, Any]] = {
    "quick": {
        "max_sub_questions": 3, "max_rounds": 1, "max_passages": 30,
        "max_duration_s": 90, "parallelism": 3,
    },
    "standard": {
        "max_sub_questions": 6, "max_rounds": 2, "max_passages": 80,
        "max_duration_s": 300, "parallelism": 4,
    },
    "thorough": {
        "max_sub_questions": 10, "max_rounds": 2, "max_passages": 160,
        "max_duration_s": 600, "parallelism": 5,
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "depth": "standard",
    "verify": True,
    "model": None,
    **DEPTH_PRESETS["standard"],
}


def resolve_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """A preset, plus whatever was overridden.

    An override makes the depth read `custom`, so nothing in the interface
    claims a depth the run did not actually use.
    """
    config = dict(DEFAULT_CONFIG)
    raw = raw or {}
    depth = raw.get("depth", config["depth"])
    config.update(DEPTH_PRESETS.get(depth, DEPTH_PRESETS["standard"]))
    config["depth"] = depth

    overridden = False
    for key, value in raw.items():
        if key == "depth" or value is None:
            continue
        if key in config and config[key] != value:
            overridden = key in DEPTH_PRESETS["standard"]
        config[key] = value

    if overridden:
        config["depth"] = "custom"
    return config


class SubQuestion(BaseModel):
    """One question the plan will actually put to the corpus."""

    id: str
    question: str
    #: Why the plan believes this is worth asking — shown, so a reader can
    #: disagree with the plan rather than only with the report.
    because: str = ""


class Finding(BaseModel):
    """One thing the corpus says, and where it says it.

    `citations` is not decoration and not optional. A finding that arrives
    without one is dropped before synthesis: there is no way to check it, and
    a report is exactly the format in which unchecked claims survive longest.
    """

    sub_question_id: str
    statement: str
    citations: tuple[str, ...] = ()
    #: Set when the verify pass could not stand the finding up.
    refuted: bool = False
    refuted_because: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.statement.strip() and self.citations and not self.refuted)


class Limit(BaseModel):
    """A bound that was reached. Reported, never silent."""

    name: str
    detail: str


class Report(BaseModel):
    question: str
    depth: str
    plan: tuple[SubQuestion, ...] = ()
    findings: tuple[Finding, ...] = ()
    #: Every passage the run read, numbered once across the whole report. Not
    #: optional and not a summary: the numbers in the prose point here, and a
    #: report whose numbers point nowhere is the exact failure a report is
    #: best at hiding — there is more of it to believe.
    citations: tuple[Any, ...] = ()
    limits: tuple[Limit, ...] = Field(default_factory=tuple)
    text: str = ""
    elapsed_s: float = 0.0
    passages_read: int = 0

    @property
    def grounded(self) -> bool:
        return any(f.usable for f in self.findings)


#: A question one retrieval answers is a waste of a research run: it costs
#: minutes and several model calls to produce what the ordinary path produces
#: in two seconds, and the reader waits for the difference.
_SINGLE = (
    "how do i", "how can i", "where is", "where do i", "what is the",
    "who is", "when is", "can i", "does the", "is there",
)
_RESEARCH = (
    "compare", "why", "explain", "analyse", "analyze", "overview of",
    "everything", "all the ways", "differences", "trade-off", "tradeoff",
    "implications", "summarise", "summarize", "across", "landscape",
    "what should i consider", "pros and cons",
)


def looks_single_shot(question: str) -> bool:
    """Whether the ordinary path would answer this just as well.

    A hint for the interface, never a refusal. Somebody who wants a research
    run on a simple question is allowed to have one — they may know something
    about the corpus that this does not.
    """
    text = " ".join(question.lower().split())
    if not text:
        return True
    if any(marker in text for marker in _RESEARCH):
        return False
    if len(text.split()) <= 7:
        return True
    return any(text.startswith(prefix) for prefix in _SINGLE)
