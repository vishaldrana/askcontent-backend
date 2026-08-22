# 03 · Federation and the retrieval pipeline

`CNT-FED-*`, `CNT-RET-*`, `CNT-RNK-*`

Search and storage are two systems. This document is about the gap between
them.

---

## 1. Two ports, not one

**CNT-FED-01 (MUST).** PGP and the ECM sit behind **two separate ports**, and
neither is allowed to imply the other.

```
ContentIndex                        # PGP. Finds things. Holds no content.
  list_knowledgebases()            -> [KnowledgeBaseDescriptor]
  describe(kb_id)                  -> KnowledgeBaseDescriptor
  search(kb_id, query, filters, k) -> [IndexHit]

IndexHit { doc_id, kb_id, score, passage_hint?, metadata }

ContentRepository                   # ECM. Holds things. Ranks nothing.
  fetch(doc_ref)                   -> RawDocument(bytes, mime, metadata)
  fetch_metadata(doc_ref)          -> DocMetadata
  search(query, filters, k)        -> [DocRef]     # native keyword/metadata search
  authorize(principal, doc_ref)    -> Decision
```

**Why two.** They fail independently, they scale differently, they disagree
about metadata, and one of them will be replaced before the other. A single
`ContentSource` port that both searches and fetches forces every future adapter
to implement both halves, and hides the most important fact about the
architecture: **a hit is not a document.**

**CNT-FED-02 (MUST).** `IndexHit.passage_hint` is **advisory only**. Where PGP
returns a matched fragment it is used to seed passage selection; it is never
cited directly and never trusted as the span of record.

**Why.** We do not control PGP's chunker. Its fragment boundaries, whitespace
handling and truncation are not ours, may change without notice, and cannot be
mapped back to an offset in the document a user will open.

---

## 2. The pipeline, in six stages

**CNT-RET-01 (MUST).** Every answered question runs these stages in this order:

| # | Stage | Does |
|---|---|---|
| ① | **Plan** | Model fills a `RetrievalSpec`; `scope_ref` is an identifier |
| ② | **Compile** | Scope predicate ∩ principal permissions → concrete index filters |
| ③ | **Candidate generation** | Fan out: PGP vector search **and** ECM native search, in parallel, per-channel `k` |
| ④ | **Resolution** | Dedupe by document identity, re-apply both gates against fetched metadata, drop unresolvable |
| ⑤ | **Passage recovery** | Fetch from ECM → parse → chunk → select candidate passages |
| ⑥ | **Rerank and assemble** | Cross-encoder over (question, passage), diversity cap, context budget, citations |

**CNT-RET-02 (MUST).** Stages ③ and ⑤ are the expensive ones and are budgeted
separately, with separate timeouts and separate caches.

---

## 3. Candidate generation is multi-channel

**CNT-RET-03 (MUST).** At least two channels run for every question:

| Channel | Finds | Misses |
|---|---|---|
| PGP vector search | Semantic paraphrase, conceptual match | Exact identifiers, rare tokens, part numbers, ticket refs |
| ECM native search | Exact strings, metadata predicates, titles | Anything phrased differently from the document |

**Why both, always.** These have complementary blind spots, and the vector-only
failure is the one users notice: someone searches for an exact error code or
policy number and the system returns thematically similar documents that do not
contain it.

**CNT-RET-04 (MUST).** Channel results are fused by **rank** — reciprocal rank
fusion — **never by raw score**.

**Trap.** PGP scores are cosine similarities from a model we do not control;
ECM scores are BM25-family relevance from a different scale entirely. Merging
them numerically produces an ordering that looks principled and is arbitrary.
The reranker in `CNT-RNK-*` is the only stage entitled to compare across
channels, because it reads the text.

**CNT-RET-05 (MUST).** A failed or timed-out channel degrades the answer
visibly: the response states which channel was unavailable. See `CNT-CON-13`.

---

## 4. Resolution: a hit is not a document

**CNT-RET-06 (MUST).** Every candidate identifier is resolved against the ECM
before it can contribute to an answer. Four outcomes, all of which occur in
production and each of which is handled explicitly:

| Outcome | Meaning | Action |
|---|---|---|
| **Resolved** | Document exists, principal may read it | Continue |
| **Not found** | PGP holds an identifier the ECM no longer has | Drop, record `stale_index`, count it |
| **Forbidden** | Indexed but this principal may not read it | Drop **before** ranking; never mention its existence beyond `CNT-ACL-04` |
| **Unavailable** | ECM error or timeout | Drop, degrade visibly, do **not** substitute cached content silently |

**CNT-RET-07 (MUST).** `stale_index` counts are aggregated per knowledgebase
and surfaced in the admin console as an index-health metric.

**Why.** A rising stale rate is the earliest available signal that PGP's sync
is broken for a knowledgebase, and it is otherwise invisible — the product
keeps answering, from a shrinking corpus, with no error anywhere.

**CNT-RET-08 (MUST).** The scope gate and the permission gate are **both**
re-evaluated at this stage against ECM metadata, not against metadata cached
from the index.

**Why.** PGP's copy of a document's labels, space and sensitivity can lag the
ECM's by a sync interval. The ECM is the system of record for both. Trusting
the index's metadata means enforcing yesterday's permissions.

---

## 5. Passage recovery — where citations come from

**CNT-RET-09 (MUST).** Because the index returns documents, **passages are
produced locally**: fetch → sniff → parse (`CNT-PAR-*`) → chunk (`CNT-CHK-*`) →
select.

**CNT-RET-10 (MUST).** A **passage cache** keyed by
`(doc_id, doc_version, parser_version, chunker_version)` holds parsed and
chunked output. A cache hit skips fetch, parse and chunk entirely.

**Why.** Without the cache, every question re-parses every candidate document,
and a 300-page PDF appearing in the candidate set of a common question makes
that question permanently slow. With it, the second asker pays nothing. This
cache is the single largest latency lever in the system.

**CNT-RET-11 (MUST).** `doc_version` comes from the ECM. Where the ECM exposes
no version, the content hash of the fetched bytes is used, and the fetch cannot
be skipped — the cache then saves parsing but not retrieval.

**CNT-RET-12 (MUST).** Passage selection within a resolved document is
deterministic: embed the question once, score every chunk of the document,
take the top `n` per document with a per-document cap.

**Why the cap.** Without it one long, densely relevant document fills the entire
context budget and the answer silently rests on a single source — which reads
exactly like a well-grounded answer and is not one.

**CNT-RET-13 (MUST).** Passage recovery is skippable only when the document's
chunks are already in the local index (the custom path of `02`). The two paths
converge here, on the same chunk model and the same citation shape.

---

## 6. The reranker

**CNT-RNK-01 (MUST).** A **cross-encoder reranker** scores every surviving
(question, passage) pair before context assembly. This stage is not optional
and is not a tuning knob.

**Why load-bearing here specifically.** In a single-index system a reranker is
an improvement. Here it is the **only** component that can compare a PGP result
against an ECM result against a locally-indexed chunk, because it is the only
one that reads text rather than consuming a score from a scale it does not own.

**CNT-RNK-02 (MUST).** The phase-1 model is an open-source cross-encoder run
locally. Two acceptable choices, both permissive:

| Model | Licence | Notes |
|---|---|---|
| `BAAI/bge-reranker-v2-m3` | Apache-2.0 | Default. Multilingual, strong quality, larger |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Apache-2.0 | Fallback. Much faster, English-only, weaker on paraphrase |

**CNT-RNK-03 (MUST).** The reranker is behind a port with a **deterministic
offline implementation** used by the test suite, so the full suite runs with no
model download and no network — the root package's offline rule.

**CNT-RNK-04 (MUST).** Reranking is batched, bounded in both pair count and
wall-clock time, and degrades to fusion order on timeout rather than failing the
answer.

**CNT-RNK-05 (MUST).** Both the pre-rerank fusion rank and the post-rerank
score are retained per passage and are visible in the diagnostics view.

**Why.** "The right document was retrieved and the reranker buried it" and "the
right document was never retrieved" are different bugs with different fixes,
and they are indistinguishable without both numbers.

**CNT-RNK-06 (MUST).** The reranker model identifier and version participate in
the plan hash. Changing it invalidates cached plans.

**CNT-RNK-07 (SHOULD).** A score floor exists below which a passage is dropped
even if it is in the top `k`. An answer with no passage above the floor is a
**refusal**, not a low-confidence answer.

---

## 7. The `RetrievalSpec`

**CNT-RET-14 (MUST).** The model fills this structure and nothing else. It is a
closed union; there is no raw-query variant and adding one is a rejected change.

```
RetrievalSpec {
  intent:        lookup | procedure | compare | timeline | who_owns | summarize
  scope_ref:     ScopeId                # identifier of a reviewed scope
  question:      str                    # verbatim user text, for the reranker
  terms:         [ResolvedTerm]         # glossary ids or verbatim phrases
  filters:       [{field: FilterField, op: FilterOp, value: Literal}]
  doc_types:     [DocType]
  freshness:     {as_of: Date?, max_age_days: int?}
  authority:     authoritative_only | include_supporting
  channels:      [pgp | ecm | native]   # resolved from scope, not model-chosen
  k_per_channel: int
  diversity_by:  document | space | source
}
```

**CNT-RET-15 (MUST).** `channels` is populated by the server from the scope's
configuration. A model-supplied value is discarded.

**CNT-RET-16 (MUST).** The spec is canonicalised and hashed. The hash keys the
plan cache and appears in the audit row.

**CNT-RET-17 (MUST).** **Cache the plan and the resolved evidence set; never
the prose.** Two people asking the same question of the same corpus get the
same citations.

---

## 8. Answering

**CNT-RET-18 (MUST).** Every sentence that makes a factual claim carries a
citation to a chunk id and the exact supporting span. A claim with no
supporting span is **not emitted**.

**CNT-RET-19 (MUST).** A citation renders as: document title, space or
knowledgebase, authority tier, last-modified date, and a link that opens the
document **in the ECM**, not in our copy.

**Why the ECM link.** Our parsed copy can be stale, and sending the user to it
makes us the system of record for content we do not own.

**CNT-RET-20 (MUST).** Where two documents at the same authority tier support
contradictory claims, **both are shown**, with dates and owners, and the answer
says they disagree. It does not pick.

**CNT-RET-21 (MUST).** Where the best supporting evidence is older than the
scope's freshness policy, the answer carries a staleness notice naming the
document's age.

---

## 9. Mocks

**CNT-FED-03 (MUST).** The phase-1 `ContentIndex` and `ContentRepository` mocks
reproduce, deterministically and under configuration:

| Behaviour | Why it must be in the mock |
|---|---|
| Latency distribution with a long tail | Timeout and budget logic is untestable without it |
| Pagination with cursors | Real indexes do not return 500 hits in one response |
| `not_found` for a fraction of hits | The `stale_index` path is the most common production surprise |
| `403` on documents the index returned | Proves resolution gates before ranking |
| Metadata disagreement between index and repository | Forces `CNT-RET-08` to be honoured rather than assumed |
| Intermittent 5xx and hard timeouts | Proves visible degradation |
| Documents with no version field | Proves the `CNT-RET-11` fallback |

**CNT-FED-04 (MUST).** Every mock module states at the top: the real system it
stands in for, the specific call that replaces each method, the assumed request
and response shapes, and the open questions to settle with that system's
owners.

**CNT-FED-05 (MUST).** Swapping a mock for a real adapter must require **no
change outside the adapter directory**. A test asserts the services layer
imports no adapter module directly.
