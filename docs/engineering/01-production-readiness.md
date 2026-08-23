# 01 · Assumptions, stand-ins, and what must change

What is real, what is standing in for something real, and what has to happen
before this is exposed to anyone outside the team.

Nothing here is a surprise discovered late. Every stand-in carries a comment in
its own file saying what replaces it; this is the index to those comments.

---

## 1. Assumptions we are making about your estate

These are the load-bearing ones. If any is wrong, tell us before the
integration starts rather than after.

| # | Assumption | If it is wrong |
|---|---|---|
| A1 | PGP can filter at query time (space, label, date, principal) | The scope predicate cannot be pushed down and must be enforced against a local metadata store — a significant redesign of `compile_filters` |
| A2 | PGP returns document identifiers the ECM recognises | The resolution stage needs an id mapping table |
| A3 | PGP can rerank on request, and says whether it did | We fall back to our own cross-encoder; already supported |
| A4 | The ECM can answer "may this principal read this document" | Every connector must declare an access class instead, and the guarantee weakens from *enforced* to *asserted* |
| A5 | The ECM exposes a stable per-document version or content hash | The passage cache cannot skip fetches; retrieval gets slower, not wrong |
| A6 | The host application can mint a short-lived signed token per visitor | The widget cannot be used — there is no anonymous mode by design |
| A7 | Content is HTML or PDF | Other formats need a parser; the registry is the extension point |
| A8 | One Postgres with pgvector is acceptable for our own index | The chunk/vector store needs a different backend behind `StoredPassages` |

**A4 deserves emphasis.** The product promise is "no answer cites a document
the asker cannot open". Where the source cannot answer permission questions,
that promise degrades to "no answer cites a document outside the declared
access class" — which is weaker, and must be stated to whoever signs off.

---

## 2. Stand-ins, and what replaces each

### 2.1 PGP and the ECM — `ecm_stub` schema

**What it is.** Two Postgres tables (`pgp_index_entry`, `ecm_document`) behind
the real ports, holding real content: 114 pages crawled from a live help centre
plus seeded finance corpora.

**Why it exists.** Neither system is reachable from here. The stub proves the
*shape* — that a hit is not a document, that the index's metadata can go stale,
that resolution catches it — which a in-memory fake could not.

**What replaces it.**

```
list_knowledgebases()  →  GET  {PGP_BASE}/v1/knowledgebases
describe(kb)           →  GET  {PGP_BASE}/v1/knowledgebases/{kb}/schema
search(...)            →  POST {PGP_BASE}/v1/knowledgebases/{kb}/search
                          { query, filters, top_k, rerank }
list_documents(kb)     →  GET  {PGP_BASE}/v1/knowledgebases/{kb}/documents

fetch(ref, principal)  →  GET  {ECM_BASE}/v1/documents/{id}/content
fetch_metadata_batch   →  POST {ECM_BASE}/v1/documents:batchGet
authorize(...)         →  POST {ECM_BASE}/v1/documents/{id}:checkAccess
```

Nothing above the port changes. `pgp_base_url` and `ecm_base_url` already exist
in configuration and are unused.

**Open question that must be settled first.** `pg_pgp.py` answers A1
optimistically — it accepts metadata filters in the query, because the stub is
a database we control. **If the real PGP is kNN-only, this is the single most
important thing to find out before the adapter is written.**

### 2.2 Widget identity — NOT PRODUCTION SAFE

`api/widget.py::_principal_from_token` does not verify the token. It reads the
`Authorization` header, confirms it is non-empty, and derives a principal from
the request body.

**Anyone can present any token and be believed.**

What replaces it: verify a signed JWT — signature against the host
application's JWKS, audience, expiry — and take the principal from the verified
subject, never from the body. The function carries this warning in a box.

**Until then the widget is safe to demonstrate and unsafe to expose.** The
origin allowlist and the publishable key are not a substitute: neither
authenticates a person.

### 2.3 The LLM reranker — temporary by construction

`adapters/rerankers/llm.py` scores passages with a language model. It is a
stand-in for a cross-encoder and says so in a box at the top of the file and in
a startup warning.

The cross-encoder is now the default where the runtime is installed
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, ~90 MB, ~190 ms for 24 pairs, ~10×
faster than the LLM path and free per query). The LLM reranker remains only for
deployments that cannot ship model weights.

**What to do:** bake `BAAI/bge-reranker-v2-m3` into the image — stronger,
multilingual. It must be *in the image*: a reranker that downloads weights on
first request fails as a latency spike rather than an error.

### 2.4 The offline adapters

`hashing` embedder, `lexical` reranker, `extractive` answerer and `mock_*`
index/repository exist so the test suite runs with no network and no API key,
which is a requirement, not a convenience.

They are **materially worse**, and each reports itself so an offline deployment
is never mistaken for the product. The extractive answerer quotes rather than
paraphrases for the same reason: an extractive answerer that rewrites is one
that lies.

---

## 3. Known gaps

Honest list. None is hidden behind a happy path.

| Gap | Impact | Effort |
|---|---|---|
| Widget token unverified | Blocks any external exposure | Small — JWKS verification |
| No human handoff from the widget | A refused visitor has nowhere to go | Medium |
| No clarifying questions | An ambiguous question gets a best guess | Medium |
| Conversation memory is 4 turns, unsummarised | Long threads lose early context | Small |
| `bge-reranker-v2-m3` not baked | Running the weaker English-only model | Small — build step |
| Discovery is bounded at 60 terms | A large corpus proposes only the top 60 | Small |
| No rate limiting on `/api/widget/ask` | A leaked key can be run up | Small |
| Feedback has no aggregate view | Trends are invisible; individual items are not | Small |
| Eval suite is 20 cases | Smoke test, not coverage | Ongoing |

---

## 4. Operational notes that have already bitten

Each of these cost real time. They are here so they cost nobody else any.

**The pooler drops idle connections.** `pool_pre_ping` alone does not detect a
half-open socket — the ping itself blocks. A crawl died after 65 seconds of a
wedged read. Fixed with `pool_recycle` below the idle cutoff and TCP
keepalives.

**Supavisor drops the `options` startup parameter.** `search_path` and
`statement_timeout` are set by a connect listener instead.

**pgvector over a raw `text()` query returns a string.** `'[0.1,0.2,…]'`, not a
list — the driver only builds a list when the column is typed. `list(raw)`
silently produces a list of *characters* and fails validation a thousand
elements later with a message about a comma.

**A declared content type is a hint.** The ECM reports
`application/octet-stream` for everything. Anywhere the declared type is
trusted, it eventually decides something — parser selection must sniff.

**Prompt rules phrased as constraints suppress answers.** Three regressions in
this codebase came from it: "name the exact screen and button" read as a
precondition, "a procedure is a numbered list" read as a precondition, and "do
not open with a preamble" read as "do not orient the reader". Each failure was
silent and looked like a retrieval problem. Formatting instructions must be
explicitly subordinate to answering.

---

## 5. The integration order I would follow

1. **Settle A1** — can PGP filter, or is it kNN-only? Everything else is
   cheaper than discovering this late.
2. **Write the PGP adapter** against the real API. Keep `pg_pgp` for tests.
3. **Write the ECM adapter.** Resolution and ACL are the load-bearing parts.
4. **Verify the widget token.** Nothing external until this is done.
5. **Bake the reranker weights.**
6. **Run the eval suite against the real estate.** It will go red; that is the
   point, and the failures are the integration's actual work list.
