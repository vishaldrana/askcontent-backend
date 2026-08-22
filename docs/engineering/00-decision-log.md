# Engineering record · Decision log

Chronological. Each entry states the decision, the reasoning, and what it cost,
written so that a reviewer who disagrees can find the reasoning and argue with
it (`ARC-REP-11`). Every measured number states what it was measured against
(`ARC-REP-12`).

---

## 2026-08-22 · Two ports for PGP and the ECM, not one `ContentSource`

**Decision.** `ContentIndex` and `ContentRepository` are separate ports.

**Reasoning.** The first sketch had a single `ContentSource` that both searched
and fetched. It hid the fact the whole design turns on: **a hit is not a
document.** PGP returns identifiers; the ECM returns bytes. They fail
independently, scale differently, disagree about metadata, and one will be
replaced before the other.

**Cost.** Every adapter author now implements two interfaces instead of one,
and the composition root wires two objects. Accepted: the alternative pushes
that complexity into every call site instead.

---

## 2026-08-22 · Passages are recovered locally rather than taken from the index

**Decision.** `IndexHit.passage_hint` is advisory and is never cited. The
citable span comes from our own fetch → parse → chunk → select.

**Reasoning.** We do not control PGP's chunker. Its fragment boundaries,
whitespace handling and truncation are not ours, can change without notice, and
cannot be mapped to an offset in the document a user will open. Citing a
fragment we cannot locate in the source is a citation that cannot be checked.

**Cost.** Large. It puts parsing on the critical path of every answer and makes
the passage cache the single biggest latency lever in the system. It is also
what makes the parsing work reusable by the custom-ingestion path later.

---

## 2026-08-22 · Fuse by rank, never by score

**Decision.** Reciprocal rank fusion across channels; raw scores are discarded.

**Reasoning.** PGP scores are cosine similarities from a model we do not
control; ECM scores are BM25-family relevance on a different scale. A merged
score-sorted list looks principled and is arbitrary.

**Cost.** The reranker stops being an optimisation and becomes a required
stage, because it is then the only component entitled to compare across
channels — it reads text rather than consuming a foreign score.

---

## 2026-08-22 · Scope is enforced at two gates

**Decision.** The retrieval gate re-evaluates the **current** scope against
**ECM** metadata on every query. No `in_scope` flag written at ingest is
trusted.

**Reasoning.** Single-gate designs are the norm and are wrong precisely where it
matters: with ingest-only filtering, a scope edit leaves previously ingested
documents fully retrievable until a re-index completes, so *removing* access is
asynchronous and silent. A group narrowing a scope in response to an incident
is exactly the case that must not wait for a job.

**Cost.** A metadata resolution per candidate on every query. Bounded by the
candidate set, not the corpus, and it is the same call that detects stale index
entries — so the cost buys two things.

**Measured.** Against the 16-document fixture corpus with latency simulation
disabled, a cold question resolves 5 candidates and parses 4 documents in
~40 ms end to end; the same question repeated is ~12 ms with a 100% passage
cache hit rate. These are fixture numbers, not production numbers.

---

## 2026-08-22 · Refusal is a first-class parse outcome

**Decision.** A PDF whose OCR confidence is below the floor, or whose parser is
not installed, is **refused and reported** rather than indexed at low
confidence.

**Reasoning.** Half-OCR'd text produces retrievable garbage that outranks
correct material on lexical match and cites like anything else. An unread
document is a visible gap; a badly read one is an invisible wrong answer.

**Cost.** Corpora will show gaps that a more permissive competitor hides. That
is the intended trade.

---

## 2026-08-22 · trafilatura is a filter, not the source of structure

**Decision.** Structure comes from the original markup; trafilatura decides
which non-heading blocks survive.

**What broke.** The first implementation passed `output_format="html"` and
parsed trafilatura's output. Measured against the fixture corpus, **every
heading was silently dropped** — trafilatura's HTML output contains no `h1`–`h6`
elements. Chunks were produced with empty heading paths, which disables
`CNT-CHK-02` entirely: "Rate limits" under *API › v2* and under *Support ›
Escalation* embed almost identically without the path.

**Fix.** Parse the original markup for blocks; use trafilatura's *text*
extraction as a retention set, matched on a normalised prefix. Headings are
always kept.

**Cost.** A fingerprint comparison per block, and a fallback to keeping
everything when extraction is unavailable or returns too little.

---

## 2026-08-22 · Conflict detection compares documents, never a document to itself

**What broke.** The first implementation grouped quantified claims by heading
and reported a "conflict" between three passages of the *same* policy — prose
and a table stating the same figure.

**Fix.** Claims are `(value, unit, surrounding significant terms)`; two claims
conflict only when they come from **different documents**, share a unit and at
least two surrounding terms, and give different values. Bare four-digit years
are excluded as noise.

**Reasoning.** A policy stating the same figure twice is agreement. Reporting
it as disagreement trains users to ignore the conflict banner, which is the one
thing it must never become.

**Cost.** Deliberately narrow. It will miss contradictions that are not
numeric. It is not a general contradiction detector and the specification says
so — its job is to guarantee the *presentation* rule of `CNT-RET-20` has
something to present.

---

## 2026-08-22 · Deterministic embedder and reranker for the offline suite

**Decision.** A hashed-n-gram embedder and a lexical reranker stand in for real
models; the cross-encoder adapter is written but lazily imported.

**Reasoning.** The root package's rule is that the full suite runs offline — no
network, no API key, no model download. That rule is what keeps the evaluation
gate runnable in CI.

**Cost.** Neither stand-in is a quality component and the code says so at the
top of both files. Ranking assertions in tests are therefore about *mechanism*
(what was dropped, by which rule, in which order) rather than about relevance.

---

## Open questions carried into the next phase

1. Whether PGP accepts metadata filters, or only kNN. Everything about where the
   scope predicate is enforced depends on the answer.
2. Whether PGP enforces per-user ACLs. If not, the resolution gate is the only
   thing preventing a leak.
3. Whether the ECM exposes an etag. Without one the passage cache loses most of
   its value.
4. Whether there is a batch access-check endpoint. Per-document authorization
   across a 40-candidate fan-out will otherwise dominate the latency budget.
5. Whether PGP exposes enumeration. If not, the scope preview and corpus browser
   become an ECM cost rather than an index one.
