# 02 · Ingestion and parsing

`CNT-PAR-*`, `CNT-CHK-*`

Parsing quality dominates answer quality. More retrieval tuning has been wasted
on badly extracted text than on any other cause in this class of system.

---

## 1. Phase 1 accepts exactly two formats

**CNT-PAR-01 (MUST).** Phase 1 accepts `application/pdf` and `text/html`.
Nothing else is ingested.

**CNT-PAR-02 (MUST).** An unsupported document is **rejected with a named
reason** recorded against the document, visible in the console. It is never
silently skipped.

**Why.** A silent skip is indistinguishable from a document that was never
discovered. The group then believes their corpus is complete when a tenth of it
never arrived, and the first symptom is a confidently wrong answer.

**CNT-PAR-03 (MUST).** Format is determined by **content sniffing** (magic
bytes plus structure), never by file extension and never by a `Content-Type`
header alone.

**Why.** Uploads and web sources both lie routinely. A `.pdf` that is actually
HTML, and an `application/octet-stream` that is a perfectly good PDF, are both
common.

---

## 2. The parser port

**CNT-PAR-04 (MUST).** All parsing sits behind one port:

```
parse(blob: bytes, mime: str, hints: ParseHints) -> ParsedDocument

ParsedDocument {
  blocks:         [Block]     # ordered, reading order
  metadata:       ParsedMeta
  parser_id:      str
  parser_version: str
  parse_path:     ParsePath   # which ladder rung produced this
  quality:        ParseQuality
}

Block {
  kind:         heading | paragraph | list_item | table | caption | code | figure
  text:         str
  heading_path: [str]         # ancestor headings, outermost first
  level:        int?          # headings only
  table:        TableData?    # tables only; never flattened to text
  page:         int?
  ordinal:      int
}
```

**CNT-PAR-05 (MUST).** Parser implementations are confined to one adapter
directory, and a test asserts that no third-party parsing library is imported
anywhere else. This is the root package's vendor-isolation rule (`ARC-TEC-*`),
applied to parsers.

**CNT-PAR-06 (MUST).** No model call occurs anywhere in parsing, chunking or
classification in phase 1.

**Why.** These are pure functions of bytes we already hold. Making one a model
call converts a guarantee into a probability, and makes the parse
non-reproducible — see `CNT-PAR-14`.

---

## 3. The chosen implementations

**CNT-PAR-07 (MUST).** Phase 1 uses **only OSI-approved open-source** parsing
components:

| Format | Component | Licence | Role |
|---|---|---|---|
| PDF | **Docling** | MIT | Primary. Layout model + table-structure recognition, emits a structured document model |
| PDF | **pypdfium2** | Apache-2.0 / BSD-3 | Fast path for digital PDFs with a clean text layer |
| PDF (scans) | **RapidOCR** | Apache-2.0 | OCR rung of the ladder |
| HTML | **trafilatura** | Apache-2.0 | Main-content extraction, boilerplate removal |
| HTML | **selectolax** / **lxml** | MIT / BSD | Structural block extraction |
| Sniffing | **puremagic** | MIT | Content type detection |

**CNT-PAR-08 (MUST NOT).** No component under AGPL, GPL, or a custom
revenue-capped licence is used, and no hosted parsing service is called.

**Why, specifically.** PyMuPDF is the fastest and best PDF text extractor
available and it is **AGPL-3.0 or commercial**. Marker, Surya and MinerU are
excellent and carry GPL or revenue-capped terms. Every one of these is a
reasonable engineering choice and an unreasonable legal surprise eighteen
months later. The constraint is deliberate; relaxing it is a decision for
counsel, not for a pull request.

**CNT-PAR-09 (MUST).** A `LICENCES.md` register lists every parsing dependency
with its licence and the date it was verified, and CI fails if an installed
parsing dependency is absent from the register or its licence has changed.

**Why.** Several libraries in this space have relicensed after adoption. A
one-time check at selection is not a control.

---

## 4. The fallback ladder

**CNT-PAR-10 (MUST).** PDF parsing follows a fixed ladder, and **which rung
produced the document is recorded**:

| Rung | Condition | Action |
|---|---|---|
| 1 | Text layer present, yield above threshold, no complex tables detected | `pypdfium2` fast extraction |
| 2 | Tables detected, or structure required | Docling layout + table structure |
| 3 | Text yield per page below threshold → the page is a scan | RapidOCR, then Docling structure |
| 4 | OCR confidence below floor | **Refuse.** Record `unparseable`, surface for review |

**CNT-PAR-11 (MUST).** Refusal is a first-class outcome. A document the
platform cannot read reliably is not indexed at a low confidence — it is
reported.

**Why.** Half-OCR'd text produces retrievable garbage that outranks correct
material on lexical match and cites like anything else. An unread document is a
visible gap; a badly read one is an invisible wrong answer.

**CNT-PAR-12 (MUST).** `parse_path` and `quality` (text yield per page, OCR
confidence, table count, block count) are stored per document and surfaced in
the catalog as a **reviewable field**.

**Why.** "This 400-page contract came in via OCR at 0.61 confidence" is
precisely what a curator needs to see before marking it authoritative.

---

## 5. Hashing: two hashes, not one

**CNT-PAR-13 (MUST).** Every document carries two hashes:

| Hash | Over | Skips |
|---|---|---|
| `file_hash` | Raw bytes | Re-parsing |
| `text_hash` | Extracted text **plus `parser_id` and `parser_version`** | Re-embedding |

**CNT-PAR-14 (MUST).** A parser upgrade therefore re-parses only the affected
MIME types, and re-embeds only the documents whose extracted text actually
changed.

**Why.** This is `PLT-VEC-09` carried into ingestion — it is what makes a
nightly run after a parser bump cost cents rather than a full rebuild. Omitting
`parser_version` from `text_hash` is the specific bug: the upgrade produces
better text, the hash is unchanged, and the improvement is never embedded.

**CNT-PAR-15 (MUST).** The `ParsedDocument` is the **artifact of record**:
stored, versioned, immutable, and never re-derived at query time.

---

## 6. The sandbox

**CNT-PAR-16 (MUST).** Parsers run in an isolated subprocess with: no network
access, a memory limit, a CPU limit, a wall-clock timeout, a maximum input
size, and a maximum page count.

**CNT-PAR-17 (MUST).** A parser crash, hang or resource kill fails **that
document only**, with a recorded reason. It never fails the ingest run and
never takes down the worker.

**Why.** Document parsers are a standing attack surface — malformed-file memory
corruption, decompression bombs, XXE in HTML and embedded XML, pathological
layouts that run for hours. Content arriving from a wiki that anyone in the
company can edit is not trusted input.

**CNT-PAR-18 (MUST).** HTML parsing resolves no external entities, fetches no
subresources, and follows no redirects during parse.

---

## 7. Chunking

**CNT-CHK-01 (MUST).** Chunking is **structure-aware** and driven by the
`ParsedDocument` block model, never by character offsets over flattened text.

**CNT-CHK-02 (MUST).** Every chunk carries its **heading path**, and that path
is prepended to the embedded text.

**Why.** "Rate limits" under *API → v2 → Rate limits* and under *Support →
Escalation → Rate limits* are different subjects. Without the path they embed
almost identically and the reranker cannot separate them.

**CNT-CHK-03 (MUST).** **A table is never split**, and a table that exceeds the
chunk budget is emitted whole as its own chunk with its caption and heading
path.

**CNT-CHK-04 (MUST).** Chunking is **parent-child**: a small chunk is embedded
for retrieval precision, and the parent section is what gets returned into the
answer context.

**Why.** Small chunks retrieve accurately and read incoherently. Retrieving
small and returning large is the cheapest large improvement available in this
pipeline.

**CNT-CHK-05 (MUST).** Chunking is deterministic: the same `ParsedDocument`
and the same chunker version produce byte-identical chunks with identical ids.

**CNT-CHK-06 (MUST).** A chunk id is stable across re-ingest when its content
and heading path are unchanged, so citations in stored conversations do not
rot.

**CNT-CHK-07 (MUST).** Chunk policy is **versioned**, and the version
participates in the embedding hash exactly as `parser_version` does.

---

## 8. Incrementality and deletion

**CNT-PAR-19 (MUST).** Ingest is incremental by construction: unchanged
documents cost neither a parse nor an embedding call.

**CNT-PAR-20 (MUST).** Source deletions propagate. A document removed at the
source is tombstoned, its chunks and embeddings deleted, and **any cached plan
citing it is invalidated**.

**Why.** Citing a document that no longer exists is worse than failing to
answer. The user follows the link, finds nothing, and stops trusting every
other citation on the page.

**CNT-PAR-21 (MUST).** Near-duplicate detection runs across the **union** of a
connector's sources. Where a document exists in more than one place, the
**canonical** copy is cited and the duplicates are collapsed.

**Why.** When the answer cites the shadow copy and the reader opens the system
of record, the two disagree. That is the fastest available way to lose a room.

---

## 9. The regression corpus

**CNT-PAR-22 (MUST).** A committed fixture corpus exercises: a rotated scan, a
multi-column layout, merged table cells, a table spanning pages, a
right-to-left document, a 500-page document, an HTML page with heavy
navigation boilerplate, and a malformed PDF that must be refused cleanly.

**CNT-PAR-23 (MUST).** Parse-quality assertions over that corpus run **offline
with no network** and gate every parser or parser-version change.

**Why.** A parser upgrade is the change most likely to silently degrade answers
across the entire corpus at once, and the only change whose blast radius is
100%.
