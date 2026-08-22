# Parsing and ranking dependency licence register

`CNT-PAR-09` — CI fails if an installed parsing dependency is missing here, or
if its licence differs from the value recorded. Verified 2026-08-22.

| Component | Role | Licence | Verified | Notes |
|---|---|---|---|---|
| `puremagic` | Content sniffing | MIT | 2026-08-22 | |
| `trafilatura` | HTML main-content extraction | Apache-2.0 | 2026-08-22 | |
| `selectolax` | HTML structural parsing | MIT | 2026-08-22 | |
| `pypdfium2` | PDF fast text path | Apache-2.0 / BSD-3 | 2026-08-22 | PDFium is BSD-3 |
| `docling` | PDF layout + table structure | MIT | 2026-08-22 | IBM |
| `rapidocr-onnxruntime` | OCR rung | Apache-2.0 | 2026-08-22 | |
| `sentence-transformers` | Cross-encoder runtime | Apache-2.0 | 2026-08-22 | |
| `BAAI/bge-reranker-v2-m3` | Reranker weights | Apache-2.0 | 2026-08-22 | Model licence, not code |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Reranker weights (fallback) | Apache-2.0 | 2026-08-22 | Model licence, not code |

## Excluded by policy — `CNT-PAR-08`

Each is a good engineering choice and an unacceptable licence for this build.
Re-evaluating any of these is a decision for counsel, not a pull request.

| Component | Licence | Why excluded |
|---|---|---|
| PyMuPDF (`fitz`) | AGPL-3.0 or commercial | Best-in-class PDF extraction; AGPL is disqualifying |
| Marker | Revenue-capped custom | Relicensed after initial release |
| Surya | Revenue-capped custom | Same |
| MinerU | AGPL-3.0 | |
| `extract-msg` | GPL-3.0 | Not needed in phase 1 (no email) |
