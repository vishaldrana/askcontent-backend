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

---

## 2026-08-22 · What the first run against a real database found

Four defects, none of which the mock could surface. Recorded together because
the pattern matters more than the individual bugs: **each one was invisible
precisely because the mock was convenient.**

### The HNSW cast was impossible, and the index still built

Revision 0002 indexed `(vector::vector(2000))`, reasoning that HNSW caps width
at 2000 dimensions. The column is `vector(1536)`, and pgvector rejects that
cast: *expected 2000 dimensions, not 1536*.

The index **built successfully**, because an empty table has no row to reject.
The failure was queued up for the first insert, somewhere that would have
looked unrelated.

*Lesson: an index expression that has never been evaluated against a row has
not been tested.* Fixed in 0003; the cast condition now lives in `vector_ops`,
which both the migration and the query builder read.

### Naive and aware datetimes

The mock returned naive `datetime`s; Postgres returns `timestamptz`. Comparing
them raises — at retrieval time, on whichever document came back first, which
reads like a retrieval bug and is a timezone bug. `catalog.as_utc` now
normalises, treating naive input as UTC, because every source we accept stores
instants rather than local wall-clock times.

### The keyword channel returned nothing for questions

`plainto_tsquery` ANDs every term, so *"how many weeks of paid parental leave
does a primary caregiver get"* required all eleven words in the title. The
channel returned zero hits for every natural-language question and the mock hid
it by counting substrings.

Significant terms are now OR-ed and ordered by `ts_rank`, which keeps the
property the channel exists for — a rare token or exact identifier still
matches, and now scores *highest* because it is rare.

### The index filtered by joining to the store

The worst of the four. `PgPgpIndex.search` applied the scope predicate by
joining `pgp_index_entry` to `ecm_document`. An entry whose document the store
has dropped has no row to join to, so **every stale identifier was filtered out
before the resolution gate could see it** — silently removing the only signal
that a sync is broken. `stale_index_count` read 0 while three planted stale
entries sat in the database.

PGP filters on what PGP knows. The stub now carries its own `facets` column,
the adapter reads only that, and there is no join. Stale entries survive
filtering and are dropped at resolution, which is the designed behaviour and
the reason `CNT-RET-08` re-checks against the ECM.

*Lesson: an adapter that reaches into the other system's storage is not an
adapter for a separate system.*

### And one that was only ever a reporting bug

`/api/health` named its adapters with string literals, so it reported
`MockPgpIndex` while `PgPgpIndex` was serving. It now reports
`type(...).__name__`. A health endpoint that names the adapter it *expects*
will confidently tell you the mocks are running when they are not.

---

## 2026-08-22 · Measured, against the shared Supabase project

All figures from the `people-ops` corpus, 128 documents, over a remote pooler.
They are network-dominated and would look nothing like this on a local
database — which is the point of measuring them here.

| Change | Before | After |
|---|---|---|
| Per-document → batch resolution | 14.3 s | 8.6 s |
| Cold vs. warm passage cache | 8.4 s | **0.40 s** |

**The passage cache is worth 21x.** `CNT-RET-10` claims it is the single
largest latency lever in the system; that is the measurement behind the claim.

The residual 8.4 s on a cold cache is one body fetch per candidate document.
That is the cost the cache exists to pay once, and it is also the argument for
the batch-fetch endpoint listed as an open question — the same argument as for
batch authorization, with the same shape.

---

## 2026-08-22 · What the Power of Attorney corpus taught

The POA knowledgebase was added as a demonstration and immediately behaved like
a real corpus, which is the point of building fixtures that look like the work.

### Jurisdictional documents legitimately disagree

The first run flagged the Texas summary (10 business days) against the Florida
summary (4 business days) as a contradiction. Both are correct. A conflict
surface that reports federalism as an error trains people to ignore it, and a
banner nobody reads is worse than no banner.

Conflict detection now reads scope labels — `state-*`, `region-*`,
`jurisdiction-*` — and the rule is deliberately **asymmetric**: two documents
that both declare a scope and declare different ones are parallel; a document
that declares none applies everywhere and can still contradict any of them.

That asymmetry is what preserves the finding worth having. The internal
acceptance procedure states the statutory window as 20 business days; every
jurisdictional summary says 10, or 4 in Florida. The procedure declares no
jurisdiction, so it is wrong in all five — and the conflict now fires against
each state rather than being suppressed as "a different scope".

### Money is a quantified claim and the detector could not see it

`$35` versus `$15` produced no conflict at all. The claim pattern was
`number + unit-word`, so "$35 is assessed" parsed as the quantity 35 with the
unit "is", and "is" is a stop word.

In a bank most quantified claims are amounts. Without a currency pattern the
conflict surface was blind to exactly the disagreements that reach a customer —
a fee schedule saying $35 and a branch quick-reference card saying $15.

### PDFs have no paragraphs

`pypdfium2` returns a text layer as lines, not blocks. Splitting on a blank line
yielded one paragraph per page, so every chunk from a PDF carried an empty
heading path — and `CNT-CHK-02` makes that path load-bearing.

Headings are now recovered heuristically: a numbered clause, an all-caps short
line, or a short title-case line followed by prose. It is labelled as a
heuristic at the call site. Docling's layout model replaces it and does the job
with font size and position; rung 1 exists for speed on clean digital PDFs, and
this is the cost of that speed. The recorded `parse_path` says which ran.

Measured on the California summary: 1 block before, 22 after, 9 chunks with
real heading paths.

---

## 2026-08-22 · Building our own index, and what was wrong before

### The vector store was empty

`askcontent.embedding` held **zero rows**. The HNSW index built in revisions
0002/0003 had never seen one. Passage selection embedded every chunk of every
candidate document **on every question** and discarded the vectors: correct
answers, no benefit from content hashing, and the embedding cost paid per
question rather than once.

`services/indexing.py` now writes documents, chunks and vectors, and passage
recovery reads them. Measured on the shared project: **104 documents, 379
chunks, 379 embeddings**, true dimension 384 padded into the 1536-wide column
with the real dimension recorded per row.

Incrementality is real: a second run over unchanged content reports
`0 parsed, 13 unchanged, 0 embedded`.

The effect at query time is the point — an indexed document needs no fetch, no
parse and no chunking, which are the three expensive stages. A technical-docs
question answered in **208 ms** with all 13 candidates served from the store.

### Python defaults are not database defaults

Three `NotNullViolation`s in a row — `extras`, then `authority`, then
`quarantined`, then the primary key — each on a different column, each because
a SQLAlchemy `default=` is applied by SQLAlchemy and by nothing else. The
indexing service writes raw SQL for throughput, so it hit every one.

Fixing them at the call site would have fixed that one writer. Revisions 0006
and 0007 generate `SET DEFAULT` for every NOT NULL column carrying a Python
default, and `gen_random_uuid()` for every UUID primary key, from the ORM
metadata. That fixes every writer, including the next one.

### An unmapped required field looks exactly like an empty knowledgebase

The technical-docs space calls its URL field `webui`; the POA space calls its
identifier `docRef` and its date `lastReviewed`. The suggester recognised none
of them, so every document failed to map — and `scope_population` dropped
unmapped documents **silently**, leaving a population of zero.

Zero documents because the map is wrong is a five-second fix on the mapping
screen. Zero documents because the knowledgebase is empty is a conversation
with its owner. They looked identical.

`scope_population_detailed` now returns the failure count and sample errors,
the indexer surfaces them, and the suggester knows the field names these five
real vocabularies actually use.

### The HNSW index is not used, and that is correct

`EXPLAIN` shows a sequential scan. With `enable_seqscan=off` the planner uses
`ix_embedding_vector_cosine`, so the index is present and usable — at 379 rows
a scan is simply cheaper. Worth recording so nobody "fixes" it: the measurement
to repeat is at corpus scale, not at fixture scale.

### Ingest reads broadly; retrieval does not

Indexing failed with `forbidden` on every technical-docs page, because it ran as
`service` and those pages grant `group:engineering`.

The platform must be able to index a document that only one group may later
read, or the corpus is limited to what everyone can see. So the ingest
credential reads broadly — and that is precisely why the retrieval gate
re-checks per user on every question. Conflating the two is the leak.
