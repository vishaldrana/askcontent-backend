# 08 · Architecture, end to end

`CNT-ARC-*`

The whole system in one document: what runs, what talks to what, what happens
to a question between the keystroke and the answer, and which parts are real.

Read [00 · Overview](00-overview-and-non-goals.md) first for what the product
is for. Read [engineering/01 · Production readiness](../engineering/01-production-readiness.md)
for what has to change before this is exposed to anyone outside the team.

---

## 1. The shape of it

Four deployable pieces and one database.

```
  ┌───────────────┐        ┌───────────────┐        ┌──────────────────┐
  │   Console     │        │    Widget     │        │   Your CI        │
  │  (React SPA)  │        │ (script tag)  │        │  (eval gate)     │
  └───────┬───────┘        └───────┬───────┘        └────────┬─────────┘
          │ /api/*                 │ /api/widget/ask         │ CLI
          ▼                        ▼                         ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                        Platform API (FastAPI)                    │
  │  question routing · retrieval · answering · admin · evaluation   │
  └───────┬──────────────────────────────┬───────────────────────────┘
          │                              │
          │ ports                        │ jobs
          ▼                              ▼
  ┌────────────────────┐          ┌──────────────┐
  │ ContentIndex (PGP) │          │    Worker    │
  │ ContentRepo  (ECM) │          │  crawl/index │
  │ Embedder           │          └──────┬───────┘
  │ Reranker           │                 │
  │ Answerer           │                 │
  │ Crawler            │                 │
  └─────────┬──────────┘                 │
            └──────────────┬─────────────┘
                           ▼
                 ┌───────────────────┐
                 │ Postgres + pgvector│
                 │  askcontent schema │
                 │  ecm_stub schema   │  ← stands in for PGP + ECM
                 └───────────────────┘
```

### The one topological fact everything follows from

There are **two upstream systems, not one**, and they answer different
questions:

| | **ContentIndex** (PGP) | **ContentRepository** (ECM) |
|---|---|---|
| Answers | "which documents match this?" | "give me that document" |
| Returns | ids, scores, its own metadata copy | bytes, authoritative metadata, ACL |
| May be | stale, out of sync | the system of record |

**CNT-ARC-01 (MUST).** A hit is not a document. Every identifier the index
returns is re-resolved against the repository before anything is shown, and the
repository's answer wins on metadata, permission and content.

**Why.** The index holds a copy of metadata that lags by a sync interval. An
answer built from the index's copy cites a title that has changed, a date that
has moved, and — the one that matters — an ACL that no longer applies. The
re-resolution is also the only thing that detects a broken sync: a hit the
store cannot produce is a *signal*, and filtering it out by joining the two
would erase it.

---

## 2. Ports and adapters

Six ports. Everything vendor-shaped lives behind one, and a test asserts that
`services/` imports no adapter.

| Port | Real adapter | Offline adapter | Notes |
|---|---|---|---|
| `ContentIndex` | `pg_pgp` (ecm_stub) | `mock_pgp` | Replace with PGP's HTTP API |
| `ContentRepository` | `pg_ecm` (ecm_stub) | `mock_ecm` | Replace with the ECM's API |
| `Embedder` | `openai_embedder` | `hashing` | `text-embedding-3-small`, 1536 |
| `Reranker` | `cross_encoder` | `lexical` | also `llm` (temporary), `embedding` |
| `Answerer` | `langchain_answerer` | `extractive` | any LangChain provider |
| `Crawler` | `http_crawler` | injected fake | polite, robots-aware |

**CNT-ARC-02 (MUST).** The service layer imports no adapter. Selection happens
once, in `bootstrap.py`, from configuration.

**Why.** It is the property that makes "swap the mock for the real thing" a
one-file change rather than an archaeology exercise — and it is asserted by
`test_architecture.py` rather than by this sentence.

### Capability negotiation, not assumption

Adapters declare what they can do; callers never assume.

- `KnowledgeBaseDescriptor.exposes_acl` — whether the index can answer "may
  this principal read this". Absence forces an explicit access-class
  declaration.
- `KnowledgeBaseDescriptor.supports_rerank` — whether the search service has a
  cross-encoder inside it.
- `IndexPage.reranked` — whether it **actually** reranked, reported by the
  response rather than inferred from the request.

**CNT-ARC-03 (MUST).** Where the index reranks, the platform does not rerank
again.

**Why.** Two rankers produce two scales. The second pass reorders on a scale it
does not own, over passages the first ranker never saw, and the result still
looks like a reasonable answer — which is what makes it dangerous. And the
capability must be read from the *response*: a service under load may ignore
the flag, and a caller that assumed otherwise would skip its own ranking and
serve vector order as though it had been ranked.

---

## 3. A question, end to end

Nine stages. Each is a gate, and each exists because of a specific failure.

```
question
   │
   ├─① route ─────────── scope/social? → answer from the corpus's shape, stop
   │
   ├─② expand ────────── glossary: add the corpus's words to the reader's
   │
   ├─③ search ────────── PGP (vector) ∥ ECM (lexical), both always
   │
   ├─④ fuse ─────────── reciprocal rank fusion, never score fusion
   │
   ├─⑤ resolve ──────── re-fetch from the store; drop what it will not give
   │        ├── role rules (this role's narrowing)
   │        ├── scope predicate (the connector's bounds)
   │        └── authority/staleness (archive tier)
   │
   ├─⑥ passages ─────── stored chunks + vectors; select per document
   │
   ├─⑦ rerank ───────── shortlist, then the cross-encoder (or the index's)
   │
   ├─⑧ relevance ────── does the evidence cover the question at all?
   │                     no → refuse, before the model is called
   │
   └─⑨ answer ───────── grounded prompt → verify citations → stream
```

### Why each gate is there

**① Question routing.** Not every question is a content question. "What can you
tell me?" is about the corpus and is answered from its shape — document count,
section names, glossary terms — all constructed from real data, never
generated. It is the first thing many people type, and a refusal there is the
product's first impression.

**② Glossary expansion.** The commonest help-centre failure is vocabulary: the
reader types "cancel", the documentation says "terminate". Embeddings absorb
some of this and reliably fail on acronyms and coined product names, which are
strings rather than meanings. Expansion **adds** the corpus's words and never
replaces the reader's.

**③ Two channels, always.** They have complementary blind spots. The
vector-only failure is the one users notice: an exact error code returns
thematically similar documents that do not contain it.

**④ Rank fusion, not score fusion.** The two channels' scores are not
comparable and never will be. RRF uses positions.

**⑤ Resolution and the access gates.** Everything that can refuse a document
happens here, before ranking — a document excluded afterwards has already
influenced the order and occupied the k budget.

**⑥ Passages.** Chunks and their vectors come from our own index. Nothing is
re-embedded at query time; doing so cost 6–12 seconds per question.

**⑦ Rerank.** The shortlist is chosen on the cheap similarity already
computed; the expensive ranker sees ~16 candidates rather than 80.

**⑧ Relevance.** Retrieval always returns *something* — that is what retrieval
is. Something has to judge whether it is on the subject. Deliberately not a
model call: it runs on every question, and a model here fails **open**, which
restores exactly the behaviour it exists to prevent.

**⑨ Answering and verification.** The prompt requires a citation on every
factual sentence and `NOT_IN_CORPUS` rather than a near-miss. Then the code
checks: a cited number that was never offered, or an answer with no citations
at all, is reported as unsupported and its evidence panel is emptied.

**CNT-ARC-04 (MUST).** An unsupported answer shows no passages.

**Why.** Leaving evidence on screen under "I could not find that" makes an
honest refusal look like a bug.

---

## 4. Ingestion

Content arrives three ways. All three converge on the same store-then-index
path.

```
  crawl a site ─┐
  paste links  ─┼─→ collection_member ─→ publish ─→ ECM (store)  ─→ index
  upload files ─┘                                 └→ PGP (index)
```

**Crawling** is two phases, deliberately. `crawl_plan` reads the sitemap and
writes one row per page *before* anything is fetched; `crawl_load` works
through them. That split is what gives the progress bar a denominator,
resumability, and a cancel that keeps what it already has.

**Publishing** writes the store first, then the index. An index entry pointing
at a document the store does not have is the one failure this design exists to
prevent; the reverse is invisible for a few seconds and then correct.

**Change detection** uses three hashes: `file_hash` (same bytes?),
`content_hash` (same words, order-independent, normalised) and
`structure_hash` (same layout?). A re-save that changes every byte and no
meaning is classified *cosmetic* and costs nothing. A live refresh of 114 pages
reported 55 of 59 changes as cosmetic-only.

---

## 5. Data model

`askcontent` schema, 17 migrations. RLS on every tenant table, enforced with a
transaction-local `set_config` — a session-scoped tenant id behind a
transaction-mode pooler is a cross-tenant read waiting to happen.

| Group | Tables |
|---|---|
| Tenancy | `org`, `workspace` |
| Connectors | `connector`, `knowledgebase`, `rbac_role`, `rbac_role_member`, `rbac_label_rule` |
| Collections | `collection`, `collection_rule`, `collection_member`, `upload` |
| Corpus | `document`, `document_chunk`, `embedding`, `quarantine_item` |
| Language | `glossary_term` |
| Conversation | `chat_thread`, `chat_turn` |
| Quality | `answer_feedback`, `eval_case`, `eval_run`, `eval_result` |
| Operations | `job`, `audit_event`, `embed` |

`ecm_stub` holds `ecm_document` and `pgp_index_entry`, and stands in for two
systems that are not ours.

---

## 6. Quality machinery

Two features that are really one.

**Feedback.** A thumbs-down carries the question, the answer and the citations —
copied, not referenced, because a thread can be deleted and the lesson should
survive it.

**Evaluation.** A closed set of expectations: `answers`, `refuses`, `cites`,
`cites_first`, `cites_something`, `says`, `does_not_say`. A free-text
expectation is one nobody can check mechanically, and a suite whose results
need interpreting stops being run.

The join: **a thumbs-down is a test case nobody has written yet**, and the
console promotes one into the other in a click.

Every run records the configuration it ran under — reranker, embedding model,
floors, freshness. "20/20" beside another "20/20" taken under different
settings is not a comparison.

`python -m askcontent.cli evals <connector>` exits non-zero on failure. That is
the point: a suite reporting into a dashboard goes red on a Friday and is
noticed the following quarter.

---

## 7. Configuration

Same names askdb uses, so one deployment configures both.

| Variable | Default | Notes |
|---|---|---|
| `ASKCONTENT_DATABASE_URL` | — | Session pooler, port 5432 |
| `ASKCONTENT_LLM_PROVIDER` | `auto` | `auto` falls back to extractive with no key |
| `ASKCONTENT_LLM_MODEL` | `gpt-4.1-2025-04-14` | |
| `ASKCONTENT_LLM_API_KEY` | — | |
| `ASKCONTENT_EMBEDDING_PROVIDER` | `auto` | |
| `ASKCONTENT_EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `ASKCONTENT_EMBEDDING_DIM` | `1536` | Must match the vector columns |
| `ASKCONTENT_RERANKER` | `auto` | `cross-encoder` → `llm` → `embedding` → `lexical` |
| `ASKCONTENT_RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | **Must be in the image** |
| `ASKCONTENT_RELEVANCE_FLOOR` | `0.34` | Share of question terms that must appear |

**Changing the embedding model invalidates the index.** Vectors from two models
are not comparable; cosine between them is a number with no meaning and the
failure is silent. Re-index rather than migrate.
