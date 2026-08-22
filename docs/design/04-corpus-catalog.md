# 04 · The corpus catalog

`CNT-CAT-*`

The `askdb` catalog describes a schema: finite, stable, enumerable. This one
describes a corpus: unbounded, drifting, duplicated and contradictory. The
requirements differ accordingly.

---

## 1. Classification is a pure function

**CNT-CAT-01 (MUST).** A document's type is decided by a **deterministic
ladder** over metadata and parsed structure. **No model call, ever** — the same
rule as `askdb`'s column classification, for the same reason.

| Type | Recognised by |
|---|---|
| `policy` | Path or space, title pattern, presence of an effective date or approver block |
| `procedure` | Imperative step structure, numbered headings, "how to" title forms |
| `decision` | Decision-record template markers, `Status:` / `Context:` / `Decision:` blocks |
| `specification` | Requirement identifier patterns, normative keyword density |
| `reference` | High table-to-prose ratio, definition-list structure |
| `faq` | Question-form heading density |
| `notes` | Meeting-note templates, date-led titles, attendee blocks |
| `report` | Period in title, figure and table density |
| `page` | Default when nothing else matches |

**CNT-CAT-02 (MUST).** A mapped `doc_type` from the source (`CNT-MAP-01`) takes
precedence over the ladder, and the catalog records which of the two decided.

**CNT-CAT-03 (MUST).** Every classification carries a **confidence** and the
**evidence** that produced it, and the catalog is sortable by confidence
ascending.

**Why.** Sorting by lowest confidence is the fastest path to a correctly
reviewed corpus. The indicator is not decoration.

---

## 2. Authority

**CNT-CAT-04 (MUST).** Every document sits in exactly one authority tier:

| Tier | Meaning | Retrieval effect |
|---|---|---|
| `authoritative` | The system of record for its subject | Always eligible; conflicts surfaced between peers |
| `supporting` | Useful, not binding | Eligible unless the spec says otherwise |
| `archive` | Superseded or expired | Excluded unless explicitly requested |

**CNT-CAT-05 (MUST).** Tier is assigned by rule — space, path, template,
label — and is **overridable by a human**, with the override recorded and
attributed.

**CNT-CAT-06 (MUST).** Where two documents claim to be authoritative on the
same subject and disagree, that is surfaced as **a fact about the corpus** in
the catalog, not resolved silently at answer time. See `CNT-RET-20`.

**CNT-CAT-07 (MUST).** Supersession is modelled explicitly: a document may
declare a successor, and the successor is cited in its place with a note.

---

## 3. Freshness

**CNT-CAT-08 (MUST).** Every document carries `updated_at` from the mapped
field, and a **staleness state** derived from the connector's freshness policy:
`fresh`, `ageing`, `stale`, `expired`.

**CNT-CAT-09 (MUST).** `expired` documents are excluded from retrieval by
default and reported as a work list to the content owner.

**CNT-CAT-10 (MUST).** A document with no parseable `updated_at` is
`unknown_age`, is reported as a mapping defect, and is **never** treated as
fresh.

**Trap.** The intuitive default is to treat a missing date as "recent enough".
That makes an entire badly-mapped knowledgebase permanently authoritative and
permanently stale at the same time.

---

## 4. Human review survives everything

**CNT-CAT-11 (MUST).** Every human correction — type, tier, hide, supersession,
owner — writes a **pin** that survives every future ingest, re-map, re-parse and
re-classify.

**CNT-CAT-12 (MUST).** The console states, on the editing surface, that the
edit is permanent.

**Why.** A user who believes their edit will be overwritten will not make it,
and the catalog never improves. This is `FE-SCR-12` restated because it matters
more here: the corpus changes continuously, so re-runs are constant.

**CNT-CAT-13 (MUST).** A bulk-edit affordance exists. Reviewing ten thousand
documents one field at a time is not a workflow anyone completes.

**CNT-CAT-14 (MUST).** Pins are exportable with the configuration
(`CNT-ADM-17`), so review effort is not lost when a connector is rebuilt.
