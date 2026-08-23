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

---

## 2026-08-22 · Workers, and dates that only exist in the text

### Adding a knowledgebase enqueues work; it does not do it

Materialising a collection fetches and parses every member. Measured on the
fixture: 2.5 s to materialise 34 documents and 9.7 s to enrich them. That is not
a thing to do inside an HTTP request, and the first realistic corpus would have
made it a timeout rather than a slow page.

Adding a rule now returns a job id. The worker claims jobs with
`FOR UPDATE SKIP LOCKED`, so several can run against one queue without
coordinating and without two of them doing the same job. A failure retries with
exponential backoff to an attempt limit and then stays `failed` **with its
error** — a job that vanishes looks exactly like one that succeeded.

Enqueueing is idempotent while a job is still queued: adding three rules to a
collection schedules one materialisation, not three.

### The chain

    rule added → collection.materialise → collection.enrich
    collection.refresh → (only if content moved) → connector.index

Verified end to end. Editing `$35` to `$40` in the fee schedule at source:

    collection.refresh   checked 34, changed 1, gone 2, unchanged 31
    connector.index      2 parsed, 29 unchanged, 8 chunks, 8 embedded

The answer then cites $40. Twenty-nine untouched documents were neither
re-parsed nor re-embedded.

### Change is detected by content hash, not by a reported date

A source that does not maintain a modified date is exactly the source whose
dates cannot be trusted to signal a change — and one that does maintain it can
still touch the date without changing a word. The hash answers the question
that was actually asked.

### Many documents carry their dates only in prose

"Effective date: 14 April 2026. Last reviewed: 2 June 2026." is a document
control block, and for a great deal of policy content it is the *only* date
there is. Without reading it, a page that plainly states when it took effect is
`unknown_age` forever.

Two rules make this safe:

  * **Provenance travels with the value.** `metadata` / `content` / `none` is
    stored per date and shown in the review table, along with the sentence the
    value was read from. A date recovered from prose is weaker evidence, and a
    reviewer deciding whether a policy is current must be able to tell.
  * **Only labelled dates are taken.** A bare date in a body is far more likely
    to be a deadline, an example or a statutory citation than the document's own
    currency. Twenty-six labels are recognised; an unlabelled date is ignored.

`01/03/2026` is read day-first, and that choice is stated rather than assumed:
the deployments this is built for are not US-only, and a wrong reading silently
shifts a date by up to eleven months.

### Three SQL bugs worth naming

  * `:c::text` — SQLAlchemy's `text()` reads the second colon as the start of
    another bind parameter, and the statement reaches Postgres malformed. Use
    `CAST(... AS text)`.
  * The same parameter used as a value *and* inside a comparison gets
    conflicting types deduced (`AmbiguousParameter`). Cast it explicitly.
  * Uploads have no ECM row, so enrichment counted every one as `unreadable` —
    a permanent false number on the review screen. They are skipped and counted
    separately.

---

## 2026-08-22 · Chunk overlap, and terms the corpus already knows

### Overlap is embedded, not cited

A fact straddling a chunk boundary was in neither chunk. Each chunk now carries
the tail of its predecessor — cut on a sentence boundary, or a word boundary
where no sentence break is in range, because an overlap beginning `urns a 429`
contributes a token the embedder has never seen.

The overlap is held **separately from the chunk's text** and appears only in
`embed_text`. Overlap solves a *retrieval* problem, so it belongs in what is
embedded; putting it in the citation would make two adjacent citations show the
same sentence twice, which reads as a bug.

Overlap does not cross a heading boundary. Two sections are two subjects, and
bleeding one into the other is exactly what the heading path exists to prevent.

Code blocks are now atomic, like tables. Half a shell command is not a shorter
shell command, it is a wrong one — and a snippet split across chunks retrieves
as neither.

### The incremental skip outranked correctness

Bumping the chunker version re-chunked **nothing**: the file-hash check fired
first, and unchanged bytes short-circuited before any version was compared. The
improvement shipped and never reached the corpus.

The skip now requires unchanged bytes **and** a current parser version **and**
chunks already written at the current chunker version. Re-running after the
version bump re-chunked all 483 chunks; the run after that skipped everything.

### Glossary terms are proposed, not typed

A glossary you must hand-write is a glossary nobody writes. Terms are extracted
from the indexed chunks by three routes, and each carries the sentences it came
from:

| Route | Confidence | Example |
|---|---|---|
| Acronym with its expansion in brackets | 0.92–0.96 | `KYC` → Know Your Customer |
| A sentence that defines something outright | ~0.77 | "Structuring means…" |
| A repeated acronym with no expansion anywhere | ~0.5 | `SLO`, flagged as needing a definition |

The third route never invents a definition. Term resolution exists to stop the
system substituting a plausible synonym for a term the corpus does not contain;
a platform that writes its own definitions defeats the feature it is feeding.

Confirming a proposal requires a definition — a term without one resolves
nothing. A rejection is stored rather than deleted, so the next scan does not
propose it again; a reviewer who has said no should not have to keep saying it.

### Three false positives, and what each was really about

**`POST`.** An HTTP verb from `POST /v2/payments`. Filtering code chunks was not
enough, because that line is a paragraph, not a code block — so the chunker now
records `is_code` and discovery reads prose only, *and* a short, explicitly
labelled list catches the verbs that appear in prose. A longer keyword file
would be a guess that needs extending forever; the reviewer's dismissal is the
general mechanism.

**`existing resource`.** From "A 404 for an existing resource means the caller
lacks visibility" — the article was matched mid-sentence and the real subject
skipped. The pattern is now anchored to a sentence start. A definition naming
the wrong thing is worse than none.

**`KYC` at two documents.** A frequency-only proposal now needs three distinct
documents. Two proposed too much noise on a technical corpus.

### The architecture test earned its keep

Comparing parser versions, I imported `adapters.parsers.pdf` from a service —
and `test_the_services_layer_imports_no_adapter` failed on the next run. The
registry now exposes `parser_version_for`, and the boundary holds: the façade is
the seam, and reaching past it is the vendor-isolation break the test exists to
catch.

---

## 2026-08-22 · A byte hash is not a change detector

`file_hash` answers "are these the same bytes", which is not the question. A
re-save, a reflowed paragraph, a wiki that smartens quotes on save — each
changes every byte and none changes what the document says. Detecting those as
changes re-parses and re-embeds a corpus for nothing, and it makes the word
*changed* on a review screen mean nothing, which is the more expensive loss.

### Three fingerprints, three questions

| Hash | Question | Buys |
|---|---|---|
| `file_hash` | Same bytes? | Skip the parse |
| `content_hash` | Same words? | Skip the re-chunk and re-embed |
| `structure_hash` | Same layout? | Tell *reordered* from *rewritten* |

`content_hash` is computed from the **parsed, normalised** text, so it is also
stable across a change of source format: the same policy exported to HTML and to
PDF fingerprints identically.

Normalisation folds NFKC, zero-width characters, curly quotes, en and em dashes,
non-breaking spaces, and runs of whitespace. **Case is kept** — in policy text
"MUST" and "must" are not the same word, and a normaliser that lowercased would
hide the one edit most worth noticing.

Content is hashed **order-independently** and order lives in the structure hash.
That separation is what makes *reordered* reachable at all, and the two are
genuinely different: reordering needs a re-chunk, because heading paths and
adjacency move, but the words are already known to be correct.

### Measured

| Change | Verdict | Re-embed |
|---|---|---|
| Reflow, blank lines, indentation | cosmetic | no |
| Curly quotes, em dashes, non-breaking spaces | cosmetic | no |
| Paragraphs swapped | reordered | yes |
| `5:00 PM` → `4:00 PM` | changed (83% shared) | yes |

On the live corpus: a re-save touching all 13 pages of the engineering space
produced **13 cosmetic-only, 0 chunks, 0 embeddings**. A subsequent real edit to
one page produced **1 parsed, 12 unchanged, 11 chunks re-embedded**.

Similarity is a shingled Jaccard over normalised tokens, with the window scaled
down for short documents — at a 5-token window a single figure change in a short
paragraph scored 57%, which reads as a rewrite and is not one.

### The bug that disabled it silently

The first live run reported **zero** cosmetic skips despite matching
fingerprints. The version guard compared against the *declared* content type,
and the content manager reports `application/octet-stream` for everything — so
"which parser handles this" resolved to *none*, the guard never passed, and the
optimisation it gated never ran.

The parser is chosen by **sniffing**, so the comparison has to sniff too.
`parser_version_for_content` does, and the same run then reported 13 of 13
skipped.

The general lesson is the one this codebase keeps relearning: a declared content
type is a hint. Anywhere it is trusted, it eventually decides something.
