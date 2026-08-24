# 01 · Planted effects

`QA-FIX-09`, `QA-FIX-10`

Each effect below is planted deliberately, produced by construction rather than
by chance, and paired with the check that verifies it. `askcontent-samples
verify` measures all nine and prints what it found.

Run it before trusting any evaluation number.

---

## people-ops

### `contradiction`

An authoritative policy and a supporting guidance page state different figures
for the same entitlement.

**Expected.** `HR-1041` (authoritative, labelled `approved`, 45 days old) says
**18 weeks**. `HR-0887` (supporting, 400 days old) says **12 weeks**. Both must
be returned, with dates and tiers, and neither silently dropped.

**Verified by.** Ask *"paid parental leave for a primary caregiver"* → the
`conflicts` array contains exactly `{HR-1041, HR-0887}`.

**Why it is here.** Ranking will always prefer one of them. A system that
returns the winner and hides the loser is indistinguishable from a system that
is right — until the day it is wrong.

---

### `stale_index`

Identifiers present in the index that the store no longer has.

**Expected.** Exactly **3 of 128** documents (2.3%), pinned by the generator.
Each must resolve to `not_found`, be dropped **before ranking**, and be counted.

**Verified by.** Probe check ④ reports a **97.7% resolution rate**; the
retrieval trace reports `stale_index_count > 0` and attributes each drop to
`stale_index`.

**Why it is here.** This is the most common production surprise in a two-system
retrieval topology, and it is silent: the product keeps answering, from a
shrinking corpus, with no error anywhere.

---

### `permission_boundary`

A document that answers a commonly asked question but is readable only by one
group.

**Expected.** `FIN-3300` answers *"how is revenue recognised"* and is readable
only by `group:finance`. For any other principal the answer is a **refusal**
naming the owner — never a summary, paraphrase or hint.

**Verified by.** Ask as `user:asha` → refused, and `FIN-3300` absent from every
citation.

**This is the leakage gate. It must be exactly zero.**

---

### `stale_duplicate`

A canonical policy and a staler near-duplicate that scores *higher* on lexical
match because its title is an exact query match.

**Expected.** `FIN-3301` (canonical, 30 days old) and `WEB-500` (duplicate, 200
days old). The **canonical** copy must be cited.

**Verified by.** Ask *"refund policy"* → citations contain `FIN-3301` and not
`WEB-500`.

**Why it is here.** Citing the duplicate makes the answer diverge from what the
reader sees when they open the system of record. That is the fastest available
way to lose a room.

---

### `metadata_disagreement`

The index's copy of a title lags the store's.

**Expected.** `WEB-501` is indexed as *"Security Overview (draft)"* and stored
as *"Security and Compliance Overview"*. The citation must show the **stored**
title.

**Verified by.** Ask *"where is customer data stored"* → the citation title is
the stored one.

**Why it is here.** The ECM is the system of record. Trusting the index's cached
metadata means enforcing yesterday's permissions and citing yesterday's titles.

---

## northwind

### `label_trap` — the status-filter trap, in its content form

**QA-FIX-10.** A scope narrowed to `labels_any: ['approved']` looks obviously
correct and silently drops a large fraction of the authoritative corpus, because
three spaces label their approved documents `published` instead.

**Expected.** **798 of 2,935** in-scope documents (**27.2%**) carry `published`
and not `approved` — the whole of `NW-LEGAL`, `NW-PROC` and `NW-QUAL`. The scope
preview must show the removal **before** the change is saved.

**Verified by.** Preview a scope with `labels_any=['approved']` → the diff
reports `removed ≈ 798`; the corpus browser filters them under
`required_label_absent`.

**Why it is here.** This is the effect the whole scope-diff requirement exists
for. "Add 0, remove 798" is a sentence that stops the mistake; a button that
just says *Save* does not.

---

### `unparseable`

A scanned PDF with no text layer, in a deployment without the OCR extra.

**Expected.** `NW-NW-SEC-SCAN01` is **refused with a named reason** and
reported. It is never indexed at low confidence.

**Verified by.** Ingest → `parse_path` is `refused`, `refusal_reason` is
populated, and the document appears in the parse-refusal metric.

**Why it is here.** An unread document is a visible gap; a badly read one is an
invisible wrong answer.

---

### `empty_space`

A space inside the scope's roots that contributes nothing after exclusions.

**Expected.** `OPS-ARCHIVE` holds **266 documents**, every one labelled
`archive` and therefore excluded — so it contributes **0** in-scope documents
while being far from empty.

**Verified by.** The scope preview's `by_root` includes `OPS-ARCHIVE` with a
count of 0, and the corpus browser shows its 266 documents under
`exclude_pattern_matched`.

**Why it is here.** A count that groups only over matched documents drops the
space entirely, and the operator cannot then tell *"excluded everything"* from
*"was never there"*.

---

## cryptic

### `unknown_dates`

A knowledgebase whose date field is `DD/MM/YYYY`, mapped by a suggestion that
assumes ISO-8601.

**Expected.** All **96** documents fail to coerce. Coverage for `updated_at`
drops to 0%, **activation is blocked**, and freshness is disabled for the whole
knowledgebase until the coercion is corrected.

**Verified by.** `validate-map` with the suggested map → `blocking` contains an
`updated_at` coverage failure; every document is `unknown_age`.

**Why it is here.** This is the silent failure the mapping editor's live samples
exist to prevent — and the reason a missing date is never treated as fresh.
