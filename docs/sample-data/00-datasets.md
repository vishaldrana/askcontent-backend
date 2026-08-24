# 00 · The datasets

`QA-FIX-*`

---

## 1. Why this is a deliverable, not a convenience

**QA-FIX-01 (MUST).** The corpora ship as a **first-class repository** with
schemas, seeded generators and loaders.

**QA-FIX-02 (MUST).** Both generators are **seeded**, so the same command
always produces the same corpus. No wall-clock reads, no unseeded randomness,
no UUIDs.

**QA-FIX-03 (MUST).** **Every interesting effect is planted and documented**,
and each is paired with the check that verifies it.

**Why this is the requirement that matters.** An answer can then be checked
against a **known truth** rather than eyeballed. Without it, every evaluation of
the product's accuracy is somebody's impression.

**QA-FIX-08 (MUST).** Generated files are **not committed**; they are
reproducible from the seed.

---

## 2. The three datasets

| Dataset | Size | Role |
|---|---|---|
| **people-ops** | 128 documents, 5 knowledgebases | The determinism and evaluation fixture. Small enough to reason about document by document, and every planted effect is reachable by a question a real employee would ask |
| **northwind** | 3,201 documents, 12 spaces | The scale fixture. Retrieval above the inline-catalog threshold, ingest and parse budgets, cost estimation, and the label trap that only appears when a corpus is too large to read |
| **cryptic** | 96 documents, opaque field codes | The field-map and glossary fixture. It exists to prove a wrong coercion is caught by validation *before* activation, rather than discovered months later as a knowledgebase where nothing is ever fresh |

---

## 3. Five vocabularies, on purpose

The single most important property of `people-ops` is that its five
knowledgebases spell and shape the same concepts differently:

| Concept | HR | Engineering | Finance | Legal | Web |
|---|---|---|---|---|---|
| identifier | `documentNumber` | `id` | `controlDocId` | `matterDocId` | `pageId` |
| title | `docTitle` | `name` | `heading` | `subject` | `pageTitle` |
| modified | `lastModified` | `updated` | `revisedOn` | `issuedOn` | `publishedAt` |
| date shape | ISO-8601 | epoch seconds | `DD/MM/YYYY` | ISO-8601 | ISO-8601 |
| labels | comma string | list | comma string | list | list |
| ACL field | `readGroups` | `acl` | `entitlements` | `permittedGroups` | **absent** |

This is why the field map exists, why a mapping entry is a typed transform
rather than a scripting hook, and why per-knowledgebase code branches are
prohibited. A pipeline that handles all five without a branch handles the sixth.

The Web knowledgebase exposes **no ACL field at all**, which forces the explicit
access-class declaration of `CNT-ACL-03`. That is the dangerous middle case: a
source with unreadable permissions where a team assumes something reasonable.

---

## 4. The stand-in source

`schema/ecm_stub.sql` defines **two tables, because they are two systems**:

| Table | Stands in for | Holds |
|---|---|---|
| `ecm_document` | The ECM | Bytes and authoritative metadata |
| `pgp_index_entry` | PGP | Identifiers and the knowledgebase's own field names |

They are deliberately allowed to disagree. Rows exist in `pgp_index_entry` whose
`doc_id` is absent from `ecm_document`, and some titles differ between the two.
A fixture where the index and the store always agree lets a design through that
cannot survive either real system.

---

## 5. Commands

```bash
askcontent-samples list                       # datasets and their planted effects
askcontent-samples generate                   # all three -> dist/*.json
askcontent-samples generate people-ops        # one
askcontent-samples verify                     # every effect, measured
askcontent-samples load people-ops "$DSN"     # into ecm_stub
```

`verify` runs without a database and without `dist/`. It is a CI gate: if a
generator change quietly removes an effect, this fails rather than the
evaluation suite mysteriously improving.
