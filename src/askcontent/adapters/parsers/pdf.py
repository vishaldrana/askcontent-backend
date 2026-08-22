"""PDF parser — the fallback ladder of CNT-PAR-10.

    rung 1  text layer present, good yield, no complex tables  -> pypdfium2
    rung 2  tables or structure required                       -> Docling
    rung 3  yield below threshold (it is a scan)               -> RapidOCR
    rung 4  OCR confidence below floor                         -> REFUSE

Refusal is a first-class outcome (CNT-PAR-11). Half-OCR'd text produces
retrievable garbage that outranks correct material on lexical match and cites
like anything else: an unread document is a visible gap, a badly read one is an
invisible wrong answer.

Every component is lazily imported. When one is absent the ladder records the
capability gap and refuses — it never silently drops to a worse rung and calls
the result a parse.

LICENCES (see LICENCES.md): pypdfium2 Apache-2.0/BSD-3, Docling MIT,
rapidocr-onnxruntime Apache-2.0. PyMuPDF is faster and better and is AGPL-3.0,
which is why it is not here (CNT-PAR-08).
"""

from __future__ import annotations

from ...domain.documents import (
    Block,
    BlockKind,
    ParsedDocument,
    ParseHints,
    ParsePath,
    ParseQuality,
    TableData,
)

_HEADING_NUMBERED = __import__("re").compile(r"^(\d+)(\.\d+)*\.?\s+\S")
_HEADING_CAPS = __import__("re").compile(r"^[A-Z0-9][A-Z0-9 ,&/()\-]{3,60}$")
_FOOTER = __import__("re").compile(r"page \d+ of \d+", __import__("re").I)


def _heading_level(line: str) -> int | None:
    """0 for not a heading, else its depth."""
    if len(line) > 90:
        return None
    numbered = _HEADING_NUMBERED.match(line)
    if numbered:
        return 1 + line.split(" ", 1)[0].rstrip(".").count(".")
    if _HEADING_CAPS.match(line) and not line.endswith("."):
        return 1
    return None


# Below this many characters per page, the page is a scan rather than a
# digital document, and the text layer (if any) is not worth having.
TEXT_YIELD_FLOOR = 120.0
OCR_CONFIDENCE_FLOOR = 0.70


class PdfParser:
    parser_id = "pdf-ladder"
    parser_version = "1.0.0"

    def supports(self, mime: str) -> bool:
        return mime == "application/pdf"

    def parse(
        self, doc_id: str, blob: bytes, mime: str, hints: ParseHints
    ) -> ParsedDocument:
        capability_gaps: list[str] = []

        text_pages, gap = self._extract_text_layer(blob, hints)
        if gap:
            capability_gaps.append(gap)

        if text_pages is not None:
            yield_per_page = sum(len(p) for p in text_pages) / max(1, len(text_pages))
            if yield_per_page >= TEXT_YIELD_FLOOR:
                blocks = self._blocks_from_pages(text_pages)
                structured, docling_gap = self._structure_with_docling(blob, hints)
                if structured is not None:
                    return self._document(
                        doc_id, structured, ParsePath.PDF_LAYOUT, yield_per_page, None, ()
                    )
                if docling_gap:
                    capability_gaps.append(docling_gap)
                # Rung 1: usable text, no table structure. Recorded honestly.
                return self._document(
                    doc_id,
                    blocks,
                    ParsePath.PDF_TEXT_LAYER,
                    yield_per_page,
                    None,
                    tuple(capability_gaps)
                    + ("table structure not recovered; install the 'pdf' extra",),
                )
        else:
            yield_per_page = None

        # Rungs 3 and 4.
        ocr_blocks, confidence, ocr_gap = self._ocr(blob, hints)
        if ocr_gap:
            capability_gaps.append(ocr_gap)
        if ocr_blocks is not None and confidence is not None:
            if confidence >= OCR_CONFIDENCE_FLOOR:
                return self._document(
                    doc_id, ocr_blocks, ParsePath.PDF_OCR, yield_per_page, confidence, ()
                )
            return self._refuse(
                doc_id,
                f"OCR confidence {confidence:.2f} below floor {OCR_CONFIDENCE_FLOOR}",
                yield_per_page,
                confidence,
            )

        return self._refuse(
            doc_id,
            "no usable text layer and no OCR available: " + "; ".join(capability_gaps),
            yield_per_page,
            None,
        )

    # -- rung 1: pypdfium2 -------------------------------------------------

    def _extract_text_layer(
        self, blob: bytes, hints: ParseHints
    ) -> tuple[list[str] | None, str | None]:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None, "pypdfium2 not installed (extra: pdf)"

        try:
            document = pdfium.PdfDocument(blob)
            pages = []
            for index in range(min(len(document), hints.max_pages)):
                page = document[index]
                pages.append(page.get_textpage().get_text_bounded() or "")
            return pages, None
        except Exception as exc:  # noqa: BLE001 - a bad PDF fails itself only
            return None, f"pypdfium2 failed: {exc}"

    # -- rung 2: Docling ---------------------------------------------------

    def _structure_with_docling(
        self, blob: bytes, hints: ParseHints
    ) -> tuple[list[Block] | None, str | None]:
        """Docling gives layout order plus TableFormer table structure — the
        reason it is the primary rather than a text extractor: CNT-CHK-03
        requires that a table survive as a table."""
        try:
            from docling.datamodel.base_models import DocumentStream
            from docling.document_converter import DocumentConverter
        except ImportError:
            return None, "docling not installed (extra: pdf)"

        try:
            import io

            converter = DocumentConverter()
            result = converter.convert(
                DocumentStream(name="document.pdf", stream=io.BytesIO(blob))
            )
            return _blocks_from_docling(result.document), None
        except Exception as exc:  # noqa: BLE001
            return None, f"docling failed: {exc}"

    # -- rung 3: OCR -------------------------------------------------------

    def _ocr(
        self, blob: bytes, hints: ParseHints
    ) -> tuple[list[Block] | None, float | None, str | None]:
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        except ImportError:
            return None, None, "rapidocr not installed (extra: ocr)"
        # Rasterisation requires pypdfium2; wiring is left explicit rather than
        # approximated, because a half-working OCR path is worse than none.
        return None, None, "OCR rasterisation pipeline not wired in phase 1"

    # -- shared ------------------------------------------------------------

    def _blocks_from_pages(self, pages: list[str]) -> list[Block]:
        """Recover paragraphs and headings from a flat text layer.

        A text layer has no structure — pypdfium2 returns lines, not blocks, so
        splitting on a blank line yields one paragraph per page and every chunk
        loses its heading path. Since CNT-CHK-02 makes that path load-bearing,
        headings are recovered heuristically here: a numbered clause
        ("3.2 Durability"), an all-caps short line, or a short title-case line
        followed by prose.

        This is a **heuristic and is labelled as one**. Docling's layout model
        replaces it and does the job properly with font size and position;
        rung 1 exists for speed on clean digital PDFs, and this is the cost of
        that speed. The parse path recorded on the document says which ran.
        """
        blocks: list[Block] = []
        heading_path: list[str] = []
        ordinal = 0

        for page_number, text in enumerate(pages, start=1):
            buffer: list[str] = []

            def flush() -> None:
                nonlocal buffer, ordinal
                if not buffer:
                    return
                blocks.append(
                    Block(
                        kind=BlockKind.PARAGRAPH,
                        text=" ".join(" ".join(buffer).split()),
                        heading_path=tuple(heading_path),
                        page=page_number,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
                buffer = []

            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    flush()
                    continue
                # The page footer carries no content and would otherwise land
                # in the last chunk of every page.
                if _FOOTER.search(line):
                    continue

                level = _heading_level(line)
                if level:
                    flush()
                    heading_path = heading_path[: level - 1]
                    heading_path.append(line)
                    blocks.append(
                        Block(
                            kind=BlockKind.HEADING,
                            text=line,
                            heading_path=tuple(heading_path),
                            level=level,
                            page=page_number,
                            ordinal=ordinal,
                        )
                    )
                    ordinal += 1
                    continue

                buffer.append(line)
                # A line ending a sentence and shorter than a full measure is a
                # paragraph end; a full-width line is a wrap.
                if line.endswith((".", ":", ";")) and len(line) < 72:
                    flush()

            flush()

        return blocks

    def _document(
        self,
        doc_id: str,
        blocks: list[Block],
        path: ParsePath,
        yield_per_page: float | None,
        confidence: float | None,
        warnings: tuple[str, ...],
    ) -> ParsedDocument:
        return ParsedDocument(
            doc_id=doc_id,
            blocks=tuple(blocks),
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            parse_path=path,
            quality=ParseQuality(
                text_yield_per_page=yield_per_page,
                ocr_confidence=confidence,
                block_count=len(blocks),
                table_count=sum(1 for b in blocks if b.kind is BlockKind.TABLE),
                char_count=sum(len(b.text) for b in blocks),
                warnings=warnings,
            ),
        )

    def _refuse(
        self,
        doc_id: str,
        reason: str,
        yield_per_page: float | None,
        confidence: float | None,
    ) -> ParsedDocument:
        return ParsedDocument(
            doc_id=doc_id,
            blocks=(),
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            parse_path=ParsePath.REFUSED,
            refusal_reason=reason,
            quality=ParseQuality(
                text_yield_per_page=yield_per_page,
                ocr_confidence=confidence,
                warnings=(reason,),
            ),
        )


def _blocks_from_docling(document: object) -> list[Block]:
    """Map Docling's document model onto ours.

    Kept small and defensive: Docling's item taxonomy evolves, and an unknown
    item type must degrade to a paragraph rather than raise.
    """
    blocks: list[Block] = []
    heading_path: list[str] = []
    ordinal = 0

    for item, _level in getattr(document, "iterate_items", lambda: [])():
        label = str(getattr(item, "label", "") or "").lower()
        text = " ".join(str(getattr(item, "text", "") or "").split())

        if "table" in label:
            table = _docling_table(item)
            if table is not None:
                blocks.append(
                    Block(
                        kind=BlockKind.TABLE,
                        text=table.render(),
                        heading_path=tuple(heading_path),
                        table=table,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
            continue

        if not text:
            continue

        if "title" in label or "section_header" in label or "heading" in label:
            level = int(getattr(item, "level", 1) or 1)
            heading_path = heading_path[: level - 1]
            heading_path.append(text)
            kind, level_value = BlockKind.HEADING, level
        elif "list" in label:
            kind, level_value = BlockKind.LIST_ITEM, None
        elif "caption" in label:
            kind, level_value = BlockKind.CAPTION, None
        elif "code" in label:
            kind, level_value = BlockKind.CODE, None
        else:
            kind, level_value = BlockKind.PARAGRAPH, None

        blocks.append(
            Block(
                kind=kind,
                text=text,
                heading_path=tuple(heading_path),
                level=level_value,
                ordinal=ordinal,
            )
        )
        ordinal += 1

    return blocks


def _docling_table(item: object) -> TableData | None:
    try:
        grid = item.data.grid  # type: ignore[attr-defined]
        rows = [tuple(str(cell.text).strip() for cell in row) for row in grid]
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    return TableData(header=rows[0], rows=tuple(rows[1:]))
