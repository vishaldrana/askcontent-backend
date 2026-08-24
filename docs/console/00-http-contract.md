# 00 · The HTTP contract

`CON-API-*`

This document lives **in this repository** on purpose (`ARC-REP-02`). A console
developer must be able to build against the API without checking the backend
out. If the two disagree, the backend is authoritative and this file is a
defect.

---

## 1. Shape

**CON-API-01 (MUST).** All paths are under `/api`. In development the Vite dev
server **proxies** them, so the application is same-origin: no CORS to
configure, and the streaming endpoint streams rather than being buffered by a
proxy that thinks it is helping.

**CON-API-02 (MUST).** Pointing the console at a remote API is a single
build-time variable. Setting it bypasses the proxy, so the API must then allow
that origin and must not buffer streaming responses.

**CON-API-03 (MUST NOT).** Do not use a browser-native `EventSource`. The
answer stream is a POST with headers. Use `fetch` with a readable stream.

---

## 2. Endpoints

### Discovery

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/health` | Parser capabilities, reranker identity, embedder, which adapters are wired |
| `GET` | `/api/knowledgebases` | Every knowledgebase visible in PGP, each with its registration state |
| `GET` | `/api/knowledgebases/{kb_id}` | One descriptor with metadata fields, observed coverage and live samples |
| `POST` | `/api/knowledgebases/{kb_id}/suggest-map` | A **suggested** field map — never applied automatically |
| `POST` | `/api/knowledgebases/{kb_id}/validate-map` | Per-field coverage, parse failures, and what blocks activation |

`GET /api/health` is called **before rendering**, not guessed at: a missing PDF
extra must show as a capability gap rather than as a mysteriously empty corpus.

### Connectors

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/connectors` | List with state, corpus size and visible-document count |
| `GET` | `/api/connectors/{id}` | Full configuration: field map, scope, access, retrieval parameters |
| `POST` | `/api/connectors/{id}/state` | `{state: "active" \| "suspended" \| "draft"}` |
| `POST` | `/api/connectors/{id}/probe` | The five ordered checks with remediation |

### Scope

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/connectors/{id}/scope/preview` | Matched count, breakdowns, and the **add/remove diff** against the saved scope |
| `PUT` | `/api/connectors/{id}/scope` | Saves, and returns the diff plus the effect statement |

**CON-API-04 (MUST).** The console never offers `PUT` from a screen that has
not shown the preview. "Add three hundred, remove eleven thousand" is a
sentence that stops a mistake; a button that just says *Save* does not.

**CON-API-05 (MUST).** The response's `effect` string is rendered verbatim:
*narrowing takes effect on the next query, widening takes effect after the next
ingest run.*

### Corpus, diagnostics and audit

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/connectors/{id}/corpus` | Every visible document with `in_scope`, and for excluded ones the single named rule |
| `POST` | `/api/connectors/{id}/diagnose` | An answer plus the **full pipeline trace**, runnable as another principal |
| `GET` | `/api/connectors/{id}/health` | Metrics, each with its likely cause and next action |
| `GET` | `/api/audit` | Scope changes and retrieval audit |
| `POST` | `/api/ask` | The answer surface: evidence, conflicts, notices, trace |

---

## 3. The answer payload

**CON-API-06 (MUST).** `/api/ask` returns **evidence, not prose**. Synthesis
sits above it and may emit only sentences backed by one of these citations.
Keeping the boundary at the API is what makes "a claim with no supporting span
is not emitted" enforceable rather than aspirational.

```ts
interface Evidence {
  citations: Citation[];
  conflicts: { subject: string; citations: Citation[] }[];
  notices: string[];           // staleness, unknown dates
  refused: boolean;
  refusal_reason: string | null;
  trace: RetrievalTrace;
}

interface Citation {
  chunk_id: string;
  doc_id: string;
  title: string;
  url: string;                 // opens in the ECM, never in our copy
  space: string | null;
  owner: string | null;
  authority: "authoritative" | "supporting" | "archive";
  updated_at: string | null;
  staleness: "fresh" | "ageing" | "stale" | "expired" | "unknown_age";
  heading_path: string[];
  span: string;                // the exact supporting text
  rerank_score: number;
  fusion_rank: number;
}
```

**CON-API-07 (MUST).** `citation.url` is rendered as the link. Our parsed copy
can be stale, and sending the user to it makes us the system of record for
content we do not own.

**CON-API-08 (MUST).** `refused: true` is a **first-class outcome**, rendered
as an answer, not as an error state. A refusal with `trace.forbidden_count > 0`
renders the access notice — and never summarises, paraphrases or hints at the
content.

**CON-API-09 (MUST).** `conflicts` is rendered **above** the evidence list,
with both sources' dates, owners and tiers. The console never picks between
them, and never hides one because it ranked lower.

---

## 4. The trace

**CON-API-10 (MUST).** Every candidate in `trace.candidates` carries
`dropped_by` — a single named rule — or `null`. The console renders that name
verbatim. Never "filtered".

```ts
interface CandidateTrace {
  doc_id: string;
  channel_ranks: Record<string, number>;   // {pgp: 1, ecm: 3}
  fusion_rank: number | null;
  resolution: "resolved" | "not_found" | "forbidden" | "unavailable" | null;
  dropped_by: string | null;               // one named rule
  drop_detail: string | null;
  chunks_selected: number;
  cache_hit: boolean | null;
  parse_path: string | null;
  rerank_score: number | null;
  rerank_rank: number | null;
}
```

**CON-API-11 (MUST).** Fusion rank and reranker score are shown **side by
side**. "The right document was retrieved and the reranker buried it" and "the
right document was never retrieved" are different bugs with different fixes,
and are indistinguishable without both numbers.

**CON-API-12 (MUST).** `trace.degraded` is rendered whenever non-empty. A
channel that failed must be named in the answer; silent narrowing of the
evidence base is prohibited.

---

## 5. Types

**CON-API-13 (MUST).** `src/lib/api.ts` mirrors these types by hand rather than
by generation, so a backend change that breaks the contract surfaces as a type
error here rather than as `undefined` at runtime.
