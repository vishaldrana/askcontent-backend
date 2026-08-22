# 07 · Collections: building a knowledgebase that does not exist yet

`CNT-COL-*`

---

## 1. The assumption this document removes

Everything up to here assumed the index offers a tidy list of knowledgebases to
pick from. That assumption is wrong, and it is wrong in the way that matters:
**PGP holds everything the company has, and almost none of it is grouped the way
any one business group needs it.**

So the administrator's job is not to *choose* a knowledgebase. It is to **build**
one — out of a site here, a folder there, a spreadsheet of links somebody
maintains, forty PDFs on a shared drive, and a handful of pages nobody can name
but everybody cites.

**CNT-COL-01 (MUST).** A **Collection** is a first-class, owned object: a set of
documents assembled from one or more source rules and materialised into an
explicit membership list.

**CNT-COL-02 (MUST).** A connector's source is a Collection. Where PGP *does*
happen to offer a coherent knowledgebase, that is expressed as a single rule
inside a Collection — **discovery is a rule, not a mode**.

**Why unify them.** Two entry points means two code paths, two review screens
and two answers to "what is in this corpus". One rule type costs nothing and the
tidy case stays a two-click operation.

---

## 2. The composition grammar

**CNT-COL-03 (MUST).** Source rules are a **closed, typed union**, ordered, each
either `include` or `exclude`. There is no free-text rule and no scripting hook —
the same prohibition, for the same reasons, as the scope grammar.

| Rule | Takes | Enumerable | Use when |
|---|---|---|---|
| `pgp_knowledgebase` | knowledgebase id | yes | The index already groups it correctly |
| `pgp_space` | space / site / library id | yes | A whole SharePoint site, Confluence space, ECM library |
| `pgp_path_prefix` | path prefix | yes | A folder tree |
| `pgp_query` | terms, filters, cap | **no — proposes** | "Everything about wire transfers" |
| `similar_to` | seed doc ids, k, floor | **no — proposes** | "And whatever looks like these twelve" |
| `link_expansion` | seed rule, hops, host policy | **no — proposes** | "And whatever these link to" |
| `doc_id_list` | ids, or a pasted CSV | yes | The spreadsheet somebody already maintains |
| **`url_list`** | **URLs** | **yes, once resolved** | **The links people already paste to each other** — see §3 |
| `crawl` | root URL, depth, include/exclude, same-host | yes, once run | An intranet site with no API |
| `upload_batch` | batch id | yes | Content that is genuinely not in PGP — the **last** resort, see §8 |

**CNT-COL-04 (MUST).** `upload_batch` is a rule like any other. The custom
ingestion path of [`02`](02-ingestion-and-parsing.md) is therefore not a separate
product surface — it is one row in this table, and it inherits the review, the
diff and the audit for free.

**CNT-COL-05 (MUST).** Exclude rules always beat include rules, whatever the
order.

---

## 3. Pasting URLs is resolution, not ingestion

`CNT-URL-*`

**This is the primary way a collection gets built**, because it is the only
artefact a disorganised group reliably has: the links they already send each
other in chat, in tickets and in onboarding docs.

**CNT-URL-01 (MUST).** A URL in a `url_list` rule is **resolved to a document
that already exists in the index**. It is not fetched, not crawled, and not
copied.

**Why this distinction carries the whole feature.** The naive reading — "paste a
URL, we'll go and download it" — creates a second copy of a document PGP already
holds. That copy has its own parse, its own vectors and its own staleness, and it
will disagree with the system of record. The corpus already has a planted effect
for exactly this failure (`stale_duplicate`), and building the front door so that
it *manufactures* that failure would be perverse.

### The resolution ladder

**CNT-URL-02 (MUST).** Resolution runs a fixed ladder, and **which rung matched
is recorded on the member**:

| # | Rung | Confidence | Matches on |
|---|---|---|---|
| 1 | Exact | 1.00 | Normalised URL equals the document's canonical URL |
| 2 | Alias | 0.95 | A known alternate form — a document-id link, a page-id link, a short link |
| 3 | Redirect | 0.90 | The URL redirects to something that matches at rung 1 or 2 |
| 4 | Path | 0.85 | Host and path match, ignoring query, fragment and case |
| 5 | Title | 0.60 | The final path segment matches a document title within a known space |
| 6 | Search | ≤0.50 | Full-text on the slug — a **proposal**, never auto-accepted |

**CNT-URL-03 (MUST).** Three thresholds, the same discipline as term
resolution:

- **above accept** — resolved, and added as a member;
- **between accept and the floor** — **ambiguous**: every candidate is shown
  with its rung and score, and a human picks;
- **below the floor** — **unresolved**. Nothing is added and nothing is guessed.

**Trap.** The tempting behaviour is to take the best candidate whatever its
score. A URL that resolves to the wrong document is worse than one that resolves
to nothing: the collection looks complete, the answer cites a real document, and
nobody can tell it is the wrong one.

### Normalisation is where the real work is

**CNT-URL-04 (MUST).** Before matching, a URL is normalised: scheme and host
lower-cased, default ports dropped, tracking parameters removed, fragment
removed, percent-encoding canonicalised, trailing slash removed, and known host
aliases folded to one.

**CNT-URL-05 (MUST).** The normaliser is **configured per deployment**, because
the aliases are: an intranet reached at three hostnames, a document platform that
serves the same file under a viewer URL, a download URL and a document-id URL,
and a wiki that accepts both a numeric page id and a title slug.

**Why this is not a detail.** In practice most of the URLs a group pastes are one
of these alternate forms. A resolver that only does exact matching resolves
perhaps half of them, and the other half look to the administrator like
"the platform can't find our documents".

### What the administrator sees

**CNT-URL-06 (MUST).** After a paste, one table: every URL, its outcome
(`resolved` / `ambiguous` / `unresolved`), the rung that matched, the document it
matched, and its confidence. Sortable by outcome, because the unresolved rows
are the work.

**CNT-URL-07 (MUST).** Unresolved rows offer exactly two routes forward, and
naming both is the point: **fix the URL**, or **flag it for upload** — the latter
being the admission that this content is genuinely not in PGP (§8).

**CNT-URL-08 (MUST).** Pasted input accepts a bare list, one URL per line, and
also **text containing URLs**, which are extracted. People paste from chat
threads and email; requiring a clean list means they clean it by hand first.

---

## 4. The decision the whole design rests on

**CNT-COL-06 (MUST).** **Rules propose. Membership decides.**

A rule set is *evaluated* to produce **candidate members**. Membership is a
stored, enumerated list of `(doc_id, contributed_by_rule, state, first_seen)`.
The platform answers from **membership**, never from a live rule evaluation.

**Why, and this is not a preference.** Three requirements already in this
package become impossible if a query is the corpus:

- `CNT-SCP-09` — the add/remove diff before a scope is saved. You cannot diff a
  set you cannot enumerate.
- `CNT-SCP-16` — "which rule excluded this document". A live query has no answer
  beyond "it didn't match today".
- `CNT-CON-15` — an audit that answers *what could this account have seen*. A
  corpus that silently changes between two identical questions cannot be audited
  at all; it can only be re-run and hoped about.

A live query also fails the human test. A group that adds one document to a
SharePoint site has not decided to put it in front of everyone who asks a
question — and a corpus defined by a query decides that for them, silently, at
3am.

**CNT-COL-07 (MUST).** Every member records **which rule contributed it**, and
that attribution is shown in the membership browser and filterable by.

**CNT-COL-08 (MUST).** Where two rules contribute the same document, all
contributing rules are recorded. Removing one rule must not silently remove a
document another rule also claims.

---

## 5. Re-materialisation is a reviewed diff

**CNT-COL-09 (MUST).** Re-running the rules produces a **proposal**, not a
mutation: documents to add, documents that disappeared, and documents whose
metadata changed materially — with counts and samples, exactly like the scope
diff.

**CNT-COL-10 (MUST).** Additions from **enumerable** rules may be auto-accepted
under a per-collection setting. Additions from **proposing** rules
(`pgp_query`, `similar_to`, `link_expansion`) **MUST NOT** be auto-accepted.

**Why the asymmetry.** "Everything in this folder" is a statement about a place,
and a new file in that place is inside the intent. "Everything matching these
terms" is a statement about a guess, and the corpus drifts every time the index
changes its mind. The second is exactly how a legal-hold document ends up
answering a question about parental leave.

**CNT-COL-11 (MUST).** A document that disappears upstream is marked
`missing_since` and retained for a configurable interval before its parsed text
and vectors are swept — the same treatment, for the same reason, as a document
that falls out of scope.

**CNT-COL-12 (MUST).** A human decision — pinned **in**, pinned **out** — is
recorded against the document and **survives every future re-materialisation**,
whatever the rules then say.

**Why.** This is the requirement that makes a disorganised corpus tractable. The
rules will never be exactly right; the curator's corrections are what close the
gap, and a correction that gets overwritten is a correction nobody makes twice.

---

## 6. Before you materialise: what it will cost

**CNT-COL-13 (MUST).** A dry run reports, per rule: candidate count, how many are
already members, how many are new, estimated bytes, estimated parse cost and
estimated embedding cost — **before** anything is fetched.

**CNT-COL-14 (MUST).** Caps are per rule and enforced during evaluation, not
after. A `crawl` or `pgp_query` that hits its cap **stops and says so**; it never
silently truncates.

**Why.** A silent truncation reads as "that's everything there is", and the gap
only surfaces later as a question the corpus should have answered and didn't.

---

## 7. Helping a disorganised group become organised

The product's job is not only to accept a mess. These three surfaces exist to
reduce it, and they are what make this more than a bulk-import screen.

**CNT-COL-15 (MUST).** **Facet browse.** Before any rule is written, the
administrator can browse what the index actually holds — spaces, path segments,
labels, owners, document types, age buckets — with counts. This replaces the
knowledgebase picker for the common case where no such list exists.

**CNT-COL-16 (SHOULD).** **Coverage report.** Questions that were asked and
refused, grouped by the terms in them, alongside documents in the index that
would have answered them and are in no collection. That turns "we are not
organised" into a ranked work list, derived from what people actually asked.

**CNT-COL-17 (SHOULD).** **Overlap report.** Which documents belong to more than
one collection, and which belong to none. A document in no collection is
invisible to the product; a document in five is five places to keep a correction
in step.

**CNT-COL-18 (SHOULD).** **Seed and expand.** Given a handful of documents an
expert names, propose neighbours by link graph and by similarity, with each
proposal showing its evidence — which seed, how many hops, what score. Bounded,
reviewed, and never auto-accepted (`CNT-COL-10`).

**Why this earns its place.** Every group has an expert who can name ten
documents and cannot name the other four hundred. Expansion turns the ten they
know into a candidate list they can review, which is a different and far easier
task than remembering.

---

## 8. Uploading is the last step, and it says so

**CNT-COL-20 (MUST).** Upload is offered **after** resolution has failed, never
before. The order is not cosmetic: a group that starts by uploading will upload
things PGP already has.

**CNT-COL-21 (MUST).** Before an upload is accepted, the platform checks whether
the content already exists in the index — by URL, by title and by content hash —
and **says so**: *"This already exists as `RSK-POL-3301`. Use that instead."*

**Why.** A second copy is not a convenience, it is a divergence with a date on
it. The copy will be answered from after the original changes, and the reader
who opens the citation sees something the system of record no longer says.

**CNT-COL-22 (MUST).** An accepted upload records **why** it was accepted —
not in PGP, restricted, or PGP-indexed-but-unresolvable — and that reason is
reportable. A growing pile of "not in PGP" uploads is the evidence that a
knowledgebase belongs in PGP, and it is the argument that gets it there.

---

## 9. Where this leaves scope

Unchanged, and now clearly separated:

| Layer | Question it answers | Changes when |
|---|---|---|
| **Collection** | What material exists for this group? | Rules are re-materialised and the diff is accepted |
| **Scope** | What of that may this connector see? | An administrator edits it — narrowing takes effect on the next query |

**CNT-COL-19 (MUST).** Scope is evaluated **within** membership. A document that
is not a member is not in scope, whatever the scope says.

**Why keep both.** They fail differently. Membership is wrong when the corpus is
incomplete; scope is wrong when it is over-broad. Collapsing them into one
control means every fix to one risks the other, and the audit can no longer say
which decision let a document through.

---

## 10. What was built, and what the build changed

### Sub-pages are a path-prefix expansion, not a platform API

`url_list` takes `include_descendants`. A resolved URL contributes its document
*and* everything beneath its path.

The alternative — asking each platform for a page tree — is one integration per
platform for the same answer, because every hierarchical system already encodes
the hierarchy in the path and the index already holds it. Confluence,
SharePoint, a wiki and a plain intranet all work through one rule.

Measured on the fixture: pasting `https://ecm.example.com/legal/poa` with
sub-pages enabled contributes **11 documents** — the section page, two
sub-sections, five state summaries, two internal procedures and a scanned
specimen.

The limitation is real and worth stating: expansion runs from a **resolved
page**. A section root that is not itself a document has nothing to expand from,
and `pgp_path_prefix` is the rule for that case.

### Confluence has two doors, and the native one may not be needed

`confluence_space` binds a space through the Confluence API; `url_list` handles
a Confluence page exactly as it handles anything else.

The open question sits in the adapter: **is the space already in PGP?** If it
is, `pgp_space` does the job with no second credential, no second rate limit and
no second copy. Build the native integration only for spaces the index does not
reach — a native integration that duplicates the index is a second system of
record.

### The duplicate check is only as good as the title

The first implementation searched the index using the first block of extracted
text. For a PDF that block is the title run together with the opening
paragraph, and it ranked the correct document **third** — so a California
statutory summary the index plainly held was accepted as new.

`ParsedDocument` now carries the title the *document* declares: a PDF's Info
dictionary, an HTML `<title>`. Duplicate detection matches on title equality
first and similarity second, because a similarity score is a statement about
prose and two different documents on one subject score highly against each
other.

With the declared title the same upload is held at **0.797** against
`LGL-POA-CA`, with the message naming the document to use instead.

### Accepting a duplicate is a decision on the record

A held upload is not rejected — sometimes a copy is genuinely needed. It
requires a reason from a closed set: `not_in_index`, `restricted`,
`unresolvable`. The reason is stored with the actor.

Without it the upload table says only that people uploaded things. With it, the
table says **which knowledgebases are missing from the index**, which is the
case for putting them there.
