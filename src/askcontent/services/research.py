"""The four phases of a research run.

    plan → investigate → verify → synthesise

Investigation uses the **ordinary retrieval path** — same scope, same access
rules, same reranker, same passage recovery. Deep research adds no new route
to the corpus, which is why it needs no separate access review: a sub-question
is asked exactly as a reader's question is asked, as the same principal, and a
document the asker cannot open is as invisible here as it is there.

Every phase yields as it goes, so the interface can show the plan before the
findings and the findings before the report. A research run takes minutes; one
that shows nothing until it finishes is indistinguishable from one that hung.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any

from ..domain.research import Finding, Limit, Report, SubQuestion, resolve_config

PLAN_SYSTEM = """\
You break one research question into the smaller questions that answer it.

Rules:

1. Each sub-question must be answerable from a document collection on its own,\
 without the others. "What does it cost, and is it worth it?" is two.
2. Cover the question, do not decorate it. Three good sub-questions beat six \
that overlap.
3. Ask about the subject, never about the collection: "What are the fees for \
an overdraft?", never "What does the documentation say about fees?".
4. Reply as JSON only: {"sub_questions": [{"question": "...", "because": "..."}]}\
 — `because` is one short clause saying why this is needed to answer the whole.
"""

SYNTHESIS_SYSTEM = """\
You write the report, from findings that have already been checked.

Rules:

1. Use only the findings supplied. Every one carries the passage numbers that \
support it; carry those numbers into your sentences as [3] or [1][4].
2. Open with a direct answer to the original question in two or three \
sentences, before any structure. Somebody who reads only the opening should \
have the answer.
3. Then the detail, under `##` headings that follow the shape of the question \
rather than the shape of the plan. A reader does not care that it was broken \
into six parts.
4. Where findings disagree, say so and attribute each side. Do not resolve it \
silently and do not manufacture a disagreement out of findings about \
different things.
5. Say plainly what the collection does not cover, if anything was asked and \
not found. That sentence is often the most useful one in the report.
6. Markdown. No preamble, no "based on the findings", no restating the \
question as a heading.
"""


def _json(raw: str) -> dict | None:
    """The first JSON object in a reply, however the model wrapped it."""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def run(
    platform,
    connector,
    question: str,
    *,
    principal: str,
    config: dict[str, Any] | None = None,
    role_rules=None,
    glossary=None,
    instructions: str = "",
    tone: str | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield `(event, payload)` as the run proceeds, then `("report", Report)`.

    Events: `plan`, `finding`, `limit`, `token`. The caller decides how to
    render them; nothing here knows about SSE.
    """
    from ..domain.retrieval_spec import Intent, RetrievalSpec

    cfg = resolve_config(config)
    started = time.monotonic()
    limits: list[Limit] = []
    passages_read = 0

    model = _chat(platform, cfg.get("model"))

    # ① plan -----------------------------------------------------------------
    plan = _plan(model, question, cfg["max_sub_questions"])
    if not plan:
        # Without a plan there is nothing to investigate, and inventing one
        # from the question verbatim would be a slow way to do what the
        # ordinary path already does.
        yield "limit", Limit(name="plan", detail="the question could not be broken down")
        plan = (SubQuestion(id="q1", question=question, because="asked as given"),)
    yield "plan", plan

    # ② investigate ----------------------------------------------------------
    findings: list[Finding] = []
    citations: list[Any] = []
    seen_chunks: set[str] = set()

    for sub in plan:
        if time.monotonic() - started > cfg["max_duration_s"]:
            limits.append(Limit(
                name="duration",
                detail=f"stopped after {cfg['max_duration_s']}s with "
                       f"{len(plan) - len(findings)} sub-questions unasked",
            ))
            break
        if passages_read >= cfg["max_passages"]:
            limits.append(Limit(
                name="passages",
                detail=f"stopped after {cfg['max_passages']} passages",
            ))
            break

        spec = RetrievalSpec(
            intent=Intent.LOOKUP,
            scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
            question=sub.question,
            channels=connector.retrieval.channels,
            k_per_channel=connector.retrieval.k_per_channel,
        )
        evidence = platform.retrieval.retrieve(
            connector, spec, principal, role_rules=role_rules, glossary=glossary
        )
        passages_read += len(evidence.citations)

        # Numbered once, across the whole run. A report that renumbered per
        # sub-question would carry four different [1]s, and the reader would
        # have no way to know which.
        numbers: list[int] = []
        for citation in evidence.citations:
            if citation.chunk_id in seen_chunks:
                numbers.append(
                    next(i + 1 for i, c in enumerate(citations)
                         if c.chunk_id == citation.chunk_id)
                )
                continue
            seen_chunks.add(citation.chunk_id)
            citations.append(citation)
            numbers.append(len(citations))

        statement = _answer_sub(model, sub.question, evidence.citations, numbers)
        finding = Finding(
            sub_question_id=sub.id,
            statement=statement,
            citations=tuple(str(n) for n in _cited_in(statement, numbers)),
        )
        findings.append(finding)
        yield "finding", finding

    # ③ verify ---------------------------------------------------------------
    if cfg["verify"]:
        for i, finding in enumerate(findings):
            if not finding.statement.strip():
                continue
            if not finding.citations:
                # Nothing to check it against. Dropped rather than softened —
                # this is the rule the whole design rests on.
                findings[i] = finding.model_copy(update={
                    "refuted": True,
                    "refuted_because": "no passage supported it",
                })
                yield "finding", findings[i]

    # ④ synthesise -----------------------------------------------------------
    usable = [f for f in findings if f.usable]
    if not usable:
        report = Report(
            question=question, depth=cfg["depth"], plan=tuple(plan),
            findings=tuple(findings), limits=tuple(limits),
            citations=tuple(citations),
            text="I could not find enough in this knowledgebase to research that.",
            elapsed_s=time.monotonic() - started, passages_read=passages_read,
        )
        for word in report.text.split(" "):
            yield "token", word + " "
        yield "report", report
        return

    text = ""
    for chunk in _synthesise(model, question, usable, instructions, tone):
        text += chunk
        yield "token", chunk

    for limit in limits:
        yield "limit", limit

    yield "report", Report(
        question=question, depth=cfg["depth"], plan=tuple(plan),
        findings=tuple(findings), limits=tuple(limits),
        # Only the passages the report actually cites. A run reads far more
        # than it uses, and a sources list padded with what was merely read is
        # a list nobody checks a second time.
        citations=tuple(
            c for i, c in enumerate(citations, start=1)
            if str(i) in {n for f in findings if f.usable for n in f.citations}
        ),
        text=text,
        elapsed_s=time.monotonic() - started, passages_read=passages_read,
    )


# -- the model calls, kept together so the phases above read as phases -------


def _chat(platform, model_id):
    """The model this run uses, through the answerer pool."""
    answerer = platform.answering._for(model_id) if model_id else platform.answering.answerer
    return getattr(answerer, "_model", None)


def _invoke(model, system: str, user: str) -> str:
    if model is None:
        return ""
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        reply = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    except Exception:  # noqa: BLE001
        return ""
    return reply.content if isinstance(reply.content, str) else str(reply.content)


def _plan(model, question: str, limit: int) -> tuple[SubQuestion, ...]:
    payload = _json(_invoke(model, PLAN_SYSTEM, question))
    if not payload:
        return ()
    out: list[SubQuestion] = []
    for i, item in enumerate(payload.get("sub_questions", [])[:limit], start=1):
        text = (item.get("question") or "").strip() if isinstance(item, dict) else str(item)
        if text:
            out.append(SubQuestion(
                id=f"q{i}", question=text,
                because=(item.get("because") or "").strip() if isinstance(item, dict) else "",
            ))
    return tuple(out)


def _answer_sub(model, question: str, citations, numbers: list[int]) -> str:
    """One sub-question, answered from its own passages and numbered globally."""
    if not citations:
        return ""
    blocks = "\n\n".join(
        f"[{n}] {c.title}\n{c.span}" for n, c in zip(numbers, citations)
    )
    return _invoke(
        model,
        "Answer the question from the numbered passages only, in two or three "
        "sentences. End every factual sentence with the numbers that support "
        "it, like [3] or [1][4]. If the passages do not answer it, reply with "
        "exactly: NOTHING_FOUND",
        f"PASSAGES\n\n{blocks}\n\nQUESTION\n{question}",
    ).strip()


def _cited_in(statement: str, offered: list[int]) -> list[int]:
    used = {int(n) for n in re.findall(r"\[(\d{1,3})\]", statement)}
    return sorted(used & set(offered))


def _synthesise(model, question, findings, instructions, tone) -> Iterator[str]:
    body = "\n\n".join(
        f"FINDING (supported by {', '.join('[' + c + ']' for c in f.citations)})\n"
        f"{f.statement}"
        for f in findings
    )
    system = SYNTHESIS_SYSTEM
    if instructions.strip():
        system = f"{instructions.strip()}\n\n---\n\n{system}"
    if tone and tone.strip():
        system = f"HOW TO WRITE\n{tone.strip()}\n\n---\n\n{system}"

    if model is None:
        yield "\n\n".join(f.statement for f in findings)
        return

    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        for piece in model.stream([
            SystemMessage(content=system),
            HumanMessage(content=f"QUESTION\n{question}\n\nFINDINGS\n\n{body}"),
        ]):
            text = piece.content if isinstance(piece.content, str) else ""
            if text:
                yield text
    except Exception:  # noqa: BLE001
        yield "\n\n".join(f.statement for f in findings)
