# 01 · Connectors and knowledge scope

`CNT-CON-*`, `CNT-SCP-*`, `CNT-ACL-*`

The containment model. If one document in this package is read carefully, it is
this one: everything the platform is allowed to know flows from here, and every
serious failure mode of a content assistant is a failure of this document.

---

## 1. What a connector is

**CNT-CON-01 (MUST).** A connector is exactly four things, none of them
optional:

| Part | Meaning |
|---|---|
| **Source binding** | Which `ContentSource` adapter, and its configuration |
| **Credential** | What the platform authenticates as when it reads that source |
| **Knowledge scope** | The bounded region of that source this connector may ever see |
| **Access binding** | Which principals may ask questions of it |

**Why.** The common product mistake is to model a connector as *a credential*
and treat scope as a filter bolted on later. Scope is then advisory: a bug, a
migration or a forgotten code path exposes the whole source. Making scope
constitutive means a connector without one **cannot be constructed**.

**CNT-CON-02 (MUST).** A connector is owned by exactly one business group and
is not shareable. Two groups needing the same source create two connectors with
two scopes.

**Why.** Shared connectors accumulate the union of everyone's scope. The
sharing that users actually want is at the *assistant* level (`CNT-CON-11`),
where it can be reasoned about.

**CNT-CON-03 (MUST).** A connector declares a **sensitivity ceiling**: the
highest classification of material it may ingest. Documents classified above
the ceiling are **quarantined**, not indexed, and are reported in the ingest
summary with counts and reasons.

**Why.** The ceiling is what lets a group point a connector at a broad space
without auditing every document first. Without it, the safe move is to connect
nothing, and the product never gets adopted.

**CNT-CON-04 (MUST).** A connector can never reach material broader than the
credential it was created with. On every ingest run the platform records the
credential's own reach as observed, and a scope that exceeds it is a **scope
error**, not a silent empty result.

**Trap.** A scope naming a space the credential cannot read looks identical to
a scope naming a space that is empty. One is a misconfiguration to fix; the
other is a correct, deliberate state. Conflating them produces the single most
common support ticket in this class of product.

---

## 2. Knowledge scope is a closed grammar

**CNT-SCP-01 (MUST).** Knowledge scope is a **typed, closed structure**. There
is no free-text query field, no raw filter expression, and no variant that
accepts a source-native query string.

```
KnowledgeScope {
  roots:          [SourceRoot]        # non-empty; the only positive grant
  include:        [PathPattern]       # narrows within roots
  exclude:        [PathPattern]       # removes; always wins
  labels_any:     [Label]             # document must carry at least one
  labels_none:    [Label]             # document must carry none
  doc_types:      [DocType]           # empty means all types
  updated_after:  Date?
  updated_before: Date?
  max_documents:  int                 # hard cap, enforced at ingest
  max_bytes:      int                 # hard cap, enforced at ingest
}
```

**Why closed.** The same reasoning as the `QuerySpec` in the root package: a
structure can be canonicalised, hashed, diffed, explained in a UI, and
evaluated identically at ingest time and at retrieval time. A string can be
none of those things, and the two evaluations will drift.

**CNT-SCP-02 (MUST).** `roots` is non-empty. There is no representation of
"the whole source", and adding one is a rejected change.

**CNT-SCP-03 (MUST).** Evaluation order is fixed and deterministic:

1. document is under **some** root, else **out**
2. `include` non-empty and no pattern matches → **out**
3. any `exclude` pattern matches → **out**
4. `labels_any` non-empty and no label matches → **out**
5. any `labels_none` label present → **out**
6. `doc_types` non-empty and type not in it → **out**
7. outside the date window → **out**
8. otherwise → **in**

**CNT-SCP-04 (MUST).** **Exclude always beats include.** A document matching
both is out. This is stated in the UI next to the pattern editors.

**CNT-SCP-05 (MUST).** Scope evaluation is a **pure function** of document
metadata. It performs no I/O, calls no model, and is identical in the ingest
worker, the retrieval path and the console preview — one implementation, called
three times.

**Why.** Three implementations of a predicate is three predicates. The
divergence shows up as a document that the console says is excluded and
retrieval cites anyway.

---

## 3. Scope is chosen before ingestion

**CNT-SCP-06 (MUST).** Scope is chosen **before** the first ingest run, not
after. The connector wizard has no path that reaches ingestion with an
unspecified scope.

**Why.** Mirrors `PLT-CON-13`. Parsing a hundred thousand documents in order to
then hide most of them wastes the source's capacity, our compute, and the
customer's money — and the parsed text of excluded material is then sitting in
our storage, which is the thing scope existed to prevent.

**CNT-SCP-07 (MUST).** A **preview** endpoint returns, for a candidate scope
and without ingesting anything: matched document count, total bytes, breakdown
by root, by type and by age bucket, and the count that would be **rejected** by
the sensitivity ceiling.

**CNT-SCP-08 (MUST).** The console shows the preview **before** the scope can
be saved, and shows it again on every edit.

**CNT-SCP-09 (MUST).** Editing an existing scope returns a **diff**: how many
documents this change would **add**, how many it would **remove**, and a sample
of each. The change cannot be saved from a screen that has not shown it.

**Why.** "Add three hundred, remove eleven thousand" is a sentence that stops a
mistake. A save button that just says *Save* does not.

---

## 4. Two gates, because one is a leak

**CNT-SCP-10 (MUST).** Scope is enforced **twice**:

| Gate | Where | Against |
|---|---|---|
| **Ingest gate** | Before parsing, in the ingest worker | Source metadata |
| **Retrieval gate** | Compiled into every retrieval query | Stored document metadata |

**CNT-SCP-11 (MUST).** The retrieval gate re-evaluates the **current** scope
against **stored** metadata. It never trusts an `in_scope` flag written at
ingest time.

**Trap.** Single-gate designs are the norm and they are wrong. Ingest-only
filtering means every scope edit leaves previously-ingested documents fully
retrievable until a re-index completes — so *removing* access is asynchronous
and silent. A group that narrows a scope in response to an incident is
specifically the case that must not wait for a job.

**CNT-SCP-12 (MUST).** Therefore: **narrowing takes effect on the next query;
widening takes effect after an ingest run.** The console states this asymmetry
in those words on the scope screen.

**CNT-SCP-13 (MUST).** Documents that fall out of scope are marked
`out_of_scope`, retained but unretrievable, and swept by a retention job after
a configurable interval. Their parsed text and embeddings are deleted by the
sweep; the audit row is not.

**Why retain briefly.** Scope edits are frequently mistakes. An immediate hard
delete converts a two-minute undo into a full re-ingest.

**CNT-SCP-14 (MUST NOT).** Scope must never be applied by filtering a result
set after retrieval. The predicate is part of the query sent to the store.

**Why.** Identical reasoning to `askdb`'s masking rule. Post-filtering means
excluded documents influenced ranking, occupied the `k` budget, and were
present in process memory — and one forgotten code path emits them.

---

## 5. The effective corpus

**CNT-SCP-15 (MUST).** Each connector has exactly one **effective corpus**
definition — scope ∩ sensitivity ceiling ∩ non-quarantined ∩ non-deleted — and
the console, the retrieval path, the counts, the cost estimate and the audit
export all read that one definition.

**Why.** When "how many documents are in this connector" has two
implementations, one of them is wrong, and it is always the one shown to the
customer.

**CNT-SCP-16 (MUST).** The effective corpus is queryable and exportable as a
document list with reasons: every document the connector saw, and for each
excluded one, **which rule excluded it**.

**Why.** "Why isn't the platform answering from this page?" is the single most
common question a content assistant receives. The answer must be a lookup, not
an investigation.

---

## 6. Access: what the connector allows is not what the user gets

**CNT-ACL-01 (MUST).** A user's retrievable set is
`connector effective corpus ∩ that user's own permissions`. Never the connector
corpus alone.

**Why.** A connector's credential is typically broader than any individual
member. Serving the connector's view to every member turns a scoping product
into an access-escalation product.

**CNT-ACL-02 (MUST).** Where the source can answer "may this principal read
this document", the platform stores the resolved principal set per document at
ingest and compiles it into the retrieval predicate.

**CNT-ACL-03 (MUST).** Where the source **cannot** answer that, the connector
must declare an explicit **access class** applied to everything it ingests, and
the console states that all documents from this connector are visible to every
member of the bound groups. There is no third option and no inference.

**Why.** The dangerous middle case is a source with unreadable ACLs where the
team assumes something reasonable. Force the declaration.

**CNT-ACL-04 (MUST).** **No answer may cite a document the asker cannot open.**
Where the only supporting evidence is inaccessible, the answer states that
relevant material exists which the asker cannot access, and names the owner to
approach. It does not summarise, paraphrase, or hint at the content.

**CNT-ACL-05 (MUST).** Permission changes at the source propagate within a
stated interval, and that interval is published in the product. Revocation is
never slower than the interval.

**CNT-ACL-06 (MUST).** The roles `may ingest`, `may edit scope`, `may review
the catalog` and `may ask` are distinct grants. In particular, uploading or
connecting content is an access decision and is not implied by the ability to
ask questions.

---

## 7. Ingest-time containment

**CNT-CON-05 (MUST).** Ingest enforces `max_documents` and `max_bytes` as hard
caps. On reaching a cap the run **stops and reports**; it does not silently
truncate.

**CNT-CON-06 (MUST).** Every ingested document is scanned for secrets and
personal-data classes before it is chunked. A hit **quarantines** the document:
parsed text is retained encrypted for review, no embedding is produced, and it
is unretrievable until a reviewer clears it.

**CNT-CON-07 (MUST).** Quarantine is visible work, not a silent drop. The
console shows the quarantine queue with the matched class and the offending
span redacted.

**CNT-CON-08 (MUST).** Connector-level cost and rate caps exist for parsing and
embedding, with the same per-tier estimate-before-run discipline as
`PLT-VEC-13`.

---

## 8. Multiple connectors

**CNT-CON-11 (MUST).** An **assistant** binds one or more connectors and is the
unit a user actually talks to. Cross-connector retrieval happens only through an
assistant, and the assistant's corpus is the **union of the effective corpora**,
intersected with the asker's permissions.

**CNT-CON-12 (MUST).** Retrieval across connectors fans out per connector with
a per-connector budget and timeout, and **fuses by rank, never by raw
similarity score**.

**Why.** Two connectors may embed with different models and different score
distributions. A cosine of 0.82 from one and 0.82 from the other are not
comparable quantities, and a merged score-sorted list is arbitrary.

**CNT-CON-13 (MUST).** If a connector fails or times out, the answer is
produced from the remainder **and says so**, naming which connector was
unavailable. Silent narrowing of the evidence base is prohibited.

---

## 9. Audit

**CNT-CON-14 (MUST).** Every scope change writes an audit row carrying: actor,
timestamp, the scope before, the scope after, and the measured add/remove
counts that the console displayed at save time.

**CNT-CON-15 (MUST).** Every retrieval writes an audit row carrying: actor,
connector, the canonicalised `RetrievalSpec`, its hash, the returned document
identifiers, and whether the answer was emitted or refused — whether or not it
succeeded.

**Why.** After a leak, the question is "what could this account have seen, and
what did it see". Both halves must be answerable from stored rows without
reconstruction.
