# 00 · Overview and non-goals

`CNT-PRD-*`

---

## 1. The product in one paragraph

A business group points the platform at knowledgebases that already exist in
PGP, bounds what the platform may see, and gets a chat surface where anyone in
the group asks questions in English and receives an answer in which **every
factual sentence is backed by a cited span in a document they can open**.

---

## 2. Personas

| Persona | Wants | Fails when |
|---|---|---|
| **Group administrator** | To make their group's knowledgebases answerable without an engineer | Configuration requires a ticket, or they cannot tell whether it worked |
| **Asker** | A trustworthy answer with a link | The answer is confident and wrong, or cites something they cannot open |
| **Content owner** | Their authoritative document to be the one that gets cited | A stale duplicate outranks it |
| **Compliance** | To prove what an account could and did see | Only logs of prose exist, not of evidence |
| **Platform engineer** | To add a knowledgebase without touching code | The pipeline has per-knowledgebase branches |

**CNT-PRD-01 (MUST).** The group administrator is the primary persona. Where a
tradeoff pits their ability to configure and diagnose against the asker's
convenience, the administrator wins.

**Why.** An assistant nobody can configure correctly is not an assistant with a
worse UI; it is an assistant that answers wrongly.

---

## 3. What this is not

**CNT-PRD-02 (MUST NOT).** Not a replacement for PGP or the ECM, and not a
second system of record. We index nothing we could ask for, and we cite into
the ECM rather than into our own copy.

**CNT-PRD-03 (MUST NOT).** Not enterprise search. A ranked list of links is a
different product; this one makes claims and must therefore stand behind each
one.

**CNT-PRD-04 (MUST NOT).** Not a summariser of everything. A question outside
the connector's scope is refused with the reason, never answered from general
knowledge.

**CNT-PRD-05 (MUST NOT).** Not a content quality tool. It surfaces staleness,
duplication and conflict as facts about the corpus, and does not fix them.

**CNT-PRD-06 (MUST NOT).** Not a writing assistant. It does not produce
documents, only answers with citations.

---

## 4. What success looks like

| Measure | Target shape |
|---|---|
| Time for an administrator to take a PGP knowledgebase from discovered to answering | Under an hour, unaided |
| Proportion of answers where every claim carries a resolvable citation | 100% — this is a gate, not a metric |
| Proportion of questions refused that should have been answered | Falls over time as scope and mapping improve |
| Proportion of answers citing a document the asker cannot open | **Zero**, asserted by test |
| Code changes required to onboard the n-th knowledgebase | Zero |

**CNT-PRD-07 (MUST).** The last row is the architectural acceptance criterion
for phase 1. A build that onboards knowledgebases by editing code has failed,
however well it answers.
