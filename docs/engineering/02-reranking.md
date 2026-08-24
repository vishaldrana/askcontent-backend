# 02 · Reranking — choosing one, and changing it

**Status**: built. Configured per connector; see migration 0022.

---

## Why this is a per-connector decision and not a deployment one

Reranking is the only stage whose right implementation depends on **where the
content came from**, and a real deployment has both kinds at once.

**Content the enterprise platform already indexes.** The fragment search takes
a rerank parameter and returns ranked fragments. It reranks with a
cross-encoder that reads *the fragments it indexed*. Doing it again locally is
paying twice to be worse: our reranker reads a passage recovered afterwards
from the store, which is a different piece of text, usually longer, sometimes
assembled from chunks the index never saw. Where the platform can rank, it
should, and the local stage should be skipped.

**Content we crawled and indexed ourselves.** Nobody else holds those
fragments. There is no index-side reranker to ask, because we *are* the index.
Here a local reranker is the only reranker there is.

One process serves both, so this cannot be an environment variable. It is a
column on the connector.

---

## The five choices

Set on a connector's Settings screen, or through
`PUT /api/connectors/{slug}/answering`.

| Choice | What it does | When it is right |
| --- | --- | --- |
| `index` | Skips the local stage. The search already ranked. | Any connector over the enterprise index that advertises the capability. |
| `llm` | A model reads question-and-passage pairs and scores them. | Content we indexed ourselves, on a deployment with no GPU. |
| `cross-encoder` | A local cross-encoder model scores the pairs. | Content we indexed ourselves, where a GPU or a tolerant latency budget exists. |
| `embedding` | Bi-encoder cosine against the question. | A fallback. It cannot attend across the pair, so it cannot tell "mentions the subject" from "answers the question". |
| `lexical` | Word overlap. | The offline default. It loses whenever the question is worded differently from the document, which is most of the time. |

Unset means the deployment default, resolved at boot by `build_reranker` in
the usual preference order — so an untouched connector behaves exactly as it
did before this existed.

### `index` is not "no reranking"

It is the assertion that somebody better already did it. The trace records
which: `reranked_by` reads `pgp:<id>` where the index ranked and
`local:<id>` where we did, and the console's Diagnose screen shows both
columns — fusion rank beside reranker score — because *"the right document was
retrieved and the reranker buried it"* and *"it was never retrieved"* are
different bugs and are indistinguishable without both numbers.

The capability is **reported, never inferred**: `IndexPage.reranked` says
whether the index actually ranked this page. An index that ignores the
parameter and returns insertion order does not get to be believed about it.

---

## The LLM reranker, and choosing its model

This is the path that matters for self-indexed content, and it is the one with
a model to choose.

It sends the question and a batch of passages to a small model and asks for a
score per passage. That is a real model call inside the reader's wait, so:

- **The model is chosen from the same catalogue as the answering model**, and
  it should not be the same model. Ranking twenty passages does not need the
  model that writes the answer; it needs one that is fast and cheap. The
  default is `gpt-5.4-mini`.
- **It fails soft.** If the model is unreachable mid-query the ranking degrades
  to the deployment's own reranker rather than collapsing to fusion order, and
  the degradation is named in the trace.
- **It is pooled by model id.** Two connectors on one model share a client; a
  connector that switches models gets a new one.

Changing it:

```
Settings → Answering → Reranker → LLM
Settings → Answering → Reranker model → GPT-5.4 mini
```

or

```bash
curl -X PUT .../api/connectors/<slug>/answering \
  -d '{"reranker": "llm", "rerank_model": "gpt-5.4-mini"}'
```

---

## Moving this into the company network

The expected sequence, in order:

1. **Point the index adapter at the real fragment search.** `ports/index.py` is
   the contract; `adapters/index/pg_pgp.py` is the stand-in. Nothing above the
   port changes — an architecture test asserts the service layer imports no
   adapter, and it is there precisely so this swap stays a swap.
2. **Set `index_side_rerank` on the connectors that live in the platform**, and
   set their `reranker` to `index`. The search now ranks, and the local stage
   is skipped rather than duplicated.
3. **Leave `llm` on the connectors over content we crawled.** They have no
   index-side option and will not acquire one.
4. **Watch the trace, not the settings screen.** `reranked_by` is the only
   place that says what actually happened. A connector configured for `index`
   against an index that silently ignores the parameter reads as `pgp:` and
   ranks like insertion order, and the eval suite is what catches it — a
   `cites_first` expectation fails long before anybody notices the answers got
   vaguer.

### What not to do

**Do not rerank twice.** Asking the index to rank and then re-ranking locally
is not belt and braces: the two rankers disagree, the second one wins, and the
one that read the actual indexed fragment is the one that got overruled.

**Do not use `embedding` as the everyday choice.** A bi-encoder scores the
question and the passage separately and compares the two vectors. It cannot
attend across the pair, so it cannot distinguish a passage that *mentions* the
subject from one that *answers* the question — which is the entire job.

**Do not leave `lexical` in production.** It is the offline default so the test
suite and a developer without a key get a working console. It scores word
overlap, so a question worded differently from the document ranks badly, which
is the failure the whole retrieval stack exists to prevent.

---

## Where the numbers are

- `rerank_floor` — passages scoring below it are dropped. Per connector, on the
  Retrieval screen. Raising it is how you make an assistant quieter; lowering
  it is how you make it confident about near-misses.
- `rerank_shortlist` — how many passages reach the reranker. Everything past it
  keeps fusion order. This is the latency dial for the LLM reranker.
- The trace carries a rerank score and a fusion rank for every candidate, and
  Diagnose shows them side by side. That pair is the diagnostic; either alone
  is not.
