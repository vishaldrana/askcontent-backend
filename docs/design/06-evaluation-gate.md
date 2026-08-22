# 06 · The evaluation gate

`CNT-EVL-*`

`askdb` has a determinism gate because its answers are verifiable. Ours are
not, so the gate measures **evidence**, not prose.

---

## 1. Build the gate first

**CNT-EVL-01 (MUST).** The gate exists and runs in CI before the first
knowledgebase is activated for real users.

**Why.** Retrieval quality regressions are invisible without it. The system
keeps answering fluently from a worse evidence set, and the first signal is a
user losing trust — which is not recoverable on the same timescale as a bug.

---

## 2. The question set

**CNT-EVL-02 (MUST).** Per knowledgebase, 100–200 questions drawn from **real**
sources — support tickets, chat history, the group's own FAQ — each labelled
with the document(s) that actually answer it.

**CNT-EVL-03 (MUST).** The set includes questions that **should be refused**:
out of scope, no supporting document, and answerable only from material the
asker cannot open.

**Why.** A system tuned only on answerable questions learns to always answer.
Refusal correctness is half the product.

**CNT-EVL-04 (MUST).** Question sets are versioned with the configuration and
owned by the group administrator, not by engineering.

---

## 3. Metrics

**CNT-EVL-05 (MUST).** Measured per run, per knowledgebase:

| Metric | Definition | Gates |
|---|---|---|
| Recall@k | Labelled document present in candidates | Candidate generation |
| Resolution rate | Candidates that resolved in the ECM | Index/store agreement |
| Citation precision | Cited span actually supports the sentence | Answer honesty |
| Groundedness | Claims with a citation ÷ all claims | Must be 1.0 |
| Refusal correctness | Correct refusals ÷ should-refuse questions | Over-answering |
| Leakage | Answers citing an inaccessible document | **Must be zero** |
| Conflict detection | Known contradictions surfaced | Conflict handling |
| Rerank lift | Labelled document's rank before vs after rerank | Reranker value |

**CNT-EVL-06 (MUST).** Leakage and groundedness are **hard gates**: any
failure fails the build. The rest have thresholds set per knowledgebase at
activation and may only be raised.

**CNT-EVL-07 (MUST NOT).** Prose similarity to a reference answer is never a
metric. It rewards fluency and is indifferent to whether the answer is true.

---

## 4. Offline

**CNT-EVL-08 (MUST).** The gate runs with the deterministic reranker and
embedder implementations, against the mock index and repository seeded from a
committed fixture corpus. No network, no model download, no live systems.

**CNT-EVL-09 (MUST).** Citation-precision judging that requires a model runs as
a separate, clearly-labelled **online** suite, and its result is advisory until
its own agreement with human judgement has been measured.
