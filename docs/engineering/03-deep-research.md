# 03 · Deep research — one question, several retrievals

**Status**: built. Off per connector by default; see migration 0023.

---

## What it is for

The ordinary path answers a question with one retrieval. That is the right
shape for most questions and the wrong shape for a few:

> *Compare the ways I can pay an auto loan and what each one requires.*

No single search returns that. The passages about online payment, about
mail-in payment and about the autopay enrolment requirement live in different
documents, and a retrieval tuned to return the twenty best passages for the
whole question returns the twenty best passages for *part* of it — usually
whichever part is worded most like the corpus.

Deep research breaks the question into parts that one retrieval *can* answer,
asks each one separately, and writes a report from what came back. Four phases:

    plan → investigate → verify → synthesise

The shape is carried over from askdb's deep agent, because the problem is the
same one over a different store: a question that needs several lookups, and a
reader who needs to see what was and was not found.

---

## The two rules that carry it

**Every finding carries its citations.** A finding that arrives without one is
dropped at synthesis, not softened into "some sources suggest". A report is
exactly the format in which unchecked claims survive longest — there is more of
it to believe, and nobody re-reads the middle of a report. `Finding.usable` is
the whole gate: a statement, at least one citation, and not refuted.

**Every limit is enforced and named.** A run that quietly stopped
investigating and wrote a confident report is the failure this exists to
prevent. Reaching a bound emits a `Limit` that reaches the reader, so
"three of six sub-questions unasked" appears in the output rather than in a log
nobody opens.

---

## No new route to the corpus

Investigation goes through the **ordinary retrieval path** — same scope, same
role rules, same reranker, same passage recovery, as the same principal. A
sub-question is asked exactly as a reader's question is asked.

This is why deep research needs no separate access review, and it is worth
being explicit about because the alternative is tempting and wrong: a research
mode that queried the store directly "because it is only reading" would let a
report quote a document its reader cannot open. Here a document invisible to
the asker on the ordinary path is invisible here too, and the report is
missing that fact rather than leaking it.

---

## Depth

| Depth | Sub-questions | Passages | Wall clock |
| --- | --- | --- | --- |
| Quick | 3 | 30 | 90 s |
| Standard | 6 | 80 | 300 s |
| Thorough | 10 | 160 | 600 s |

Named rather than numbered, because "standard" is a decision somebody can
defend and "level 2" is not. Overriding any preset field makes the depth read
**custom** — nothing in the interface gets to claim a depth the run did not
actually use.

---

## Numbering

Passages are numbered **once across the whole run**, not per sub-question. Six
sub-questions each renumbering from `[1]` would put six different `[1]`s in one
report and give the reader no way to tell which. A passage that comes back for
a second sub-question keeps the number it already had.

The sources list holds only what the report **cites**, not everything read. A
thorough run reads a hundred and sixty passages; a sources list padded with the
ones that went nowhere is a list nobody checks twice.

---

## What the reader sees while it runs

Every phase yields as it goes, over the same SSE channel as an ordinary answer:

| Event | When | Shown as |
| --- | --- | --- |
| `plan` | after planning | the sub-questions, each with why it was asked |
| `finding` | per sub-question | a step, as it completes |
| `token` | during synthesis | the report, streaming |
| `limit` | at the end | a named bound that was reached |

A run takes minutes. One that shows nothing until it finishes is
indistinguishable from one that hung, and the plan is the most useful thing to
show first — a reader who disagrees with the plan can stop the run instead of
disagreeing with the report five minutes later.

---

## Turning it on

Off by default. A run costs minutes and several model calls, which is not a
thing to enable for a whole connector because one person wanted it once.

```
Settings → Answering → Deep research → Depth
```

or

```bash
curl -X PUT .../api/connectors/<slug>/answering \
  -d '{"research": {"enabled": true, "depth": "standard"}}'
```

Enabled, the composer gains a toggle. The toggle is the trigger — a question is
never silently promoted to a research run, because the reader is the one paying
the wait.

`looks_single_shot()` marks questions the ordinary path would answer just as
well ("how do I reset my password"). It is a **hint for the interface, never a
refusal**: somebody who wants a research run on a simple question may know
something about the corpus that a prefix test does not.

---

## Where it can go wrong

**The plan is the run.** A bad plan cannot be recovered downstream — every
later phase is faithful to it. If reports come back oddly shaped, read the
plan first; it is emitted precisely so this is checkable.

**A model that will not answer JSON.** `_plan` returns nothing, and the run
falls back to asking the question verbatim, which is the ordinary path with
extra steps. The `plan` limit says so.

**Tone applies to the report, grounding does not bend.** The connector's tone
is prepended to the synthesis prompt; the citation rules are written after it,
because the last instruction is the one followed.
