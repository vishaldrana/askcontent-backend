# 05 · The admin console

`CNT-ADM-*`, `CNT-MAP-*`, `CNT-SCR-*`

---

## 1. The measurable goal

**CNT-ADM-01 (MUST).** Onboarding a knowledgebase that already exists in PGP
must require **no code change, no deployment, and no engineer**. A configured
administrator does it through this console, end to end, and can tell whether it
worked.

**Why this is the whole product.** "Content search" is not a differentiator;
every vendor has one. What a large organisation cannot buy is the ability to
take four hundred existing knowledgebases with inconsistent metadata,
inconsistent ownership and inconsistent quality, and make a chosen subset of
them answer questions safely for a specific business group. That capability is
an admin surface, not a model.

**CNT-ADM-02 (MUST).** Every behaviour of the retrieval pipeline that varies
per knowledgebase is **configuration data**, versioned in the platform
database. There is no per-knowledgebase branch anywhere in the code.

**Trap.** The natural first implementation special-cases the first two
knowledgebases "just until the pattern is clear". The pattern is never clear,
because the next knowledgebase is always different, and by the fifth the
retrieval path has five branches and no one can predict what any of them do.
Push the variance into the mapping (`CNT-MAP-*`) from the first one.

---

## 2. The knowledgebase registry

**CNT-ADM-03 (MUST).** The console **discovers** knowledgebases from PGP rather
than requiring them to be typed in: a browse screen listing every knowledgebase
the platform's credential can see, with document count, last index time,
embedding model and dimension, and available metadata fields with observed
value samples.

**CNT-ADM-04 (MUST).** A discovered knowledgebase is in one of four states, and
the state is always visible:

| State | Meaning |
|---|---|
| `unregistered` | Visible in PGP, not configured here |
| `draft` | Being configured; not retrievable by anyone |
| `active` | Mapped, scoped, tested, serving |
| `suspended` | Configuration retained; retrieval refuses |

**CNT-ADM-05 (MUST).** Suspending is one click, takes effect on the next query,
and is available to any administrator without a deploy.

**Why.** The first thing anyone needs during a content incident is a switch
that stops a knowledgebase being answered from. If that requires a release,
the answer during the incident is to take the whole product down.

**CNT-ADM-06 (MUST).** A knowledgebase can be registered more than once, with
different scopes, for different business groups. The registry key is
`(knowledgebase, connector)`, never the knowledgebase alone.

---

## 3. Field mapping — the robustness core

**CNT-MAP-01 (MUST).** Every knowledgebase declares a **field map** from its
own metadata fields to the platform's canonical fields:

| Canonical field | Required | Used for |
|---|---|---|
| `doc_id` | yes | Resolution against the ECM |
| `title` | yes | Citations |
| `url` | yes | The link that opens the document in the ECM |
| `updated_at` | yes | Freshness, staleness notices, authority |
| `space` | no | Scope roots, diversity, citation context |
| `owner` | no | Conflict presentation, "who to ask" |
| `labels` | no | Scope include/exclude |
| `doc_type` | no | Classification prior; falls back to `04`'s ladder |
| `sensitivity` | no | Sensitivity ceiling |
| `acl_principals` | no | Permission predicate; absence forces `CNT-ACL-03` |

**CNT-MAP-02 (MUST).** A mapping entry is a **typed transform**, not free-form
code: source field, target field, type coercion, an optional value map for
enumerations, and a default. There is no scripting hook.

**Why not scripting.** A per-knowledgebase transform script is a per-knowledge
base code branch wearing a costume — unreviewable, untestable in aggregate, and
a remote-execution surface in an admin console.

**CNT-MAP-03 (MUST).** The mapping editor shows **live sample values** from the
knowledgebase beside every field, before and after transform, over a sample of
real documents.

**Why.** Mapping metadata you cannot see is guesswork, and the failure is
silent: a date field mapped from a string that does not parse yields every
document dated "unknown", which quietly disables freshness for the entire
knowledgebase.

**CNT-MAP-04 (MUST).** Mapping **validation** runs over a sample and reports,
per field: coverage percentage, parse failure count, distinct value count, and
example failures. A mapping with a required field below a coverage threshold
cannot be activated.

**CNT-MAP-05 (MUST).** Where PGP and the ECM both expose a field, the map
declares which system wins. The default is **the ECM**, as the system of
record.

**CNT-MAP-06 (MUST).** Unmapped source fields are retained verbatim in an
extras bag, queryable but not used by the pipeline.

**Why.** The field nobody mapped is routinely the one that turns out to carry
the authority signal. Discarding it means re-ingesting to get it back.

---

## 4. The probe

**CNT-ADM-07 (MUST).** Registration runs **five ordered checks**, each with a
specific remediation naming what to change and who to ask. Never a bare
"connection failed".

| # | Check | Failing means |
|---|---|---|
| ① | PGP reachable, credential valid | Platform credential or network |
| ② | Knowledgebase visible and non-empty | Wrong identifier, or no grant to it |
| ③ | Sample search returns hits | The knowledgebase is indexed but not queryable as configured |
| ④ | **Sample hits resolve in the ECM** | The index and the store disagree — the highest-value check |
| ⑤ | Required mapped fields meet coverage | Field map is wrong or the source is inconsistent |

**CNT-ADM-08 (MUST).** Check ④ reports the **resolution rate** over the sample
and lists failures individually with their outcome (`not_found`, `forbidden`,
`unavailable`).

**Why it is the important one.** A knowledgebase can pass every other check and
be useless: PGP indexed it from a source the ECM no longer serves, or under
identifiers the ECM does not recognise. Nothing else detects that, and without
this check it surfaces months later as a knowledgebase that answers nothing and
reports no error.

---

## 5. Dry run and the pipeline trace

**CNT-ADM-09 (MUST).** An administrator can run a question against a draft
configuration without exposing it to anyone, and see the **full trace**:

| Stage | Shown |
|---|---|
| Compile | The canonical `RetrievalSpec`, its hash, the compiled scope and permission predicates |
| Candidates | Per channel: hits, scores, latency, timeouts |
| Fusion | Fused order with each hit's contributing channel ranks |
| Resolution | Per candidate: outcome, and which gate dropped it |
| Passages | Per document: chunks selected, cache hit or miss, parse path and parse quality |
| Rerank | Fusion rank against reranker score, side by side, with the floor marked |
| Assembly | What entered the context, what was cut by budget or diversity cap |

**CNT-ADM-10 (MUST).** Every drop is attributed to **exactly one named rule**.
"Excluded by `exclude` pattern `/archive/*`" — never "filtered".

**Why.** Without per-stage attribution, tuning a content pipeline is
superstition. This screen is the difference between an administrator who can
own their configuration and one who files a ticket.

**CNT-ADM-11 (MUST).** The trace is available for **any** production answer,
subject to permission, not only for dry runs.

**CNT-ADM-12 (MUST).** A dry run can be executed **as another principal**, by
an administrator with the appropriate grant, to answer "why can this person not
see this document" without impersonation of their session.

---

## 6. Diagnostics that surface decay

**CNT-ADM-13 (MUST).** Per knowledgebase, tracked over time and alertable:

| Metric | Detects |
|---|---|
| Stale-index rate (`CNT-RET-07`) | PGP sync broken or lagging |
| Resolution outcome mix | ECM permission or availability changes |
| Passage cache hit rate | Version churn, or a parser change invalidating everything |
| Parse refusal rate by format | A source that started emitting scans |
| Reranker floor-refusal rate | The knowledgebase no longer answers its own questions |
| Median rerank delta from fusion rank | Whether candidate generation is degrading |
| Quarantine volume | A source that started carrying restricted material |

**CNT-ADM-14 (MUST).** Each metric names the **likely cause and the next
action** in the console. A number an administrator cannot act on is decoration.

**CNT-ADM-15 (MUST).** A knowledgebase whose stale-index rate crosses a
configured threshold raises an alert and is flagged in the registry list. It is
not silently suspended.

---

## 7. Configuration lifecycle

**CNT-ADM-16 (MUST).** Every configuration object — registration, field map,
scope, access binding, retrieval parameters — is **versioned**, with actor,
timestamp and diff, and is revertible to any prior version in one action.

**CNT-ADM-17 (MUST).** Configuration is exportable and importable as a
documented, human-readable artifact, so a knowledgebase proven in a lower
environment is promoted rather than re-entered.

**CNT-ADM-18 (MUST).** Import is **validated and dry-run first**: what would
change, what would break, which required fields would fall below coverage.

**CNT-ADM-19 (MUST).** Retrieval parameters are configurable per knowledgebase
with platform defaults and explicit inheritance shown in the UI: `k` per
channel, fusion constant, reranker model and floor, per-document passage cap,
context budget, diversity dimension, freshness policy, and every timeout.

**CNT-ADM-20 (MUST).** Every such parameter states its default, its effective
value, and **where the effective value came from** — platform default,
knowledgebase override, or connector override.

**Why.** Inherited configuration whose provenance is invisible is configuration
nobody dares change.

---

## 8. Routes

**CNT-SCR-01 (MUST).** Exactly these routes exist. The shell, sidebar,
primitives and token system are those of the `askdb` console
(`FE-THM-*`) — an operator moving between the two products is in the same
application.

| Route | Screen |
|---|---|
| `/login` | Authentication |
| `/` | Redirect to last used connector |
| `/knowledgebases` | **Discovery**: everything visible in PGP, with state |
| `/knowledgebases/:kbId` | Knowledgebase detail: fields, samples, index health |
| `/connectors` | List: state, knowledgebase, corpus size, health |
| `/connectors/new` | The six-step wizard |
| `/connectors/:id` | Overview: counts, last runs, quick actions, alerts |
| `/connectors/:id/mapping` | Field map editor with live samples and validation |
| `/connectors/:id/scope` | Scope editor with preview and **add/remove diff** |
| `/connectors/:id/corpus` | Two-pane document browser with exclusion reasons |
| `/connectors/:id/access` | Access bindings, sensitivity ceiling, effective access |
| `/connectors/:id/retrieval` | Retrieval parameters with inheritance provenance |
| `/connectors/:id/diagnose` | Dry run and the full pipeline trace |
| `/connectors/:id/health` | Diagnostics and alerts |
| `/connectors/:id/settings` | Limits, quarantine queue, danger zone |
| `/chat`, `/chat/:threadId` | The conversation |
| `/jobs/:jobId` | Ingest and index progress |
| `/admin/audit` | Scope changes and retrieval audit |

**CNT-SCR-02 (MUST).** The wizard's six steps, with the connector row created at
step ① so it is **resumable**:

| Step | Contents |
|---|---|
| ① Source | Pick a discovered knowledgebase, or declare a custom source |
| ② Mapping | Field map, with live samples and coverage validation |
| ③ **Probe** | The five ordered checks with remediation |
| ④ **Scope** | Roots, include/exclude, labels, ceiling — **with live counts** |
| ⑤ Access | Group bindings and the ACL mode declaration of `CNT-ACL-03` |
| ⑥ Verify | Dry-run a question, see the trace, then activate |

**CNT-SCR-03 (MUST).** Step ③ is the screen that decides whether onboarding
succeeds, and step ④ is the screen that decides whether it is safe. Neither may
be skipped, and neither may be passed with warnings unacknowledged.

**CNT-SCR-04 (MUST).** The scope screen states, in these words, that
**narrowing takes effect on the next query and widening takes effect after the
next ingest run** (`CNT-SCP-12`).

**CNT-SCR-05 (MUST).** The corpus browser shows, for every excluded document,
the single named rule that excluded it (`CNT-SCP-16`), and is filterable by
that reason.
