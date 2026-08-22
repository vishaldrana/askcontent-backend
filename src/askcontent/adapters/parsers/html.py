"""HTML parser.

trafilatura (Apache-2.0) for main-content extraction and boilerplate removal;
selectolax/lxml for structure. Both are in LICENCES.md.

No external entities are resolved, no subresources are fetched, and no
redirects are followed during parse (CNT-PAR-18).
"""

from __future__ import annotations

import html as html_module
import re

from ...domain.documents import (
    Block,
    BlockKind,
    ParsedDocument,
    ParseHints,
    ParsePath,
    ParseQuality,
    TableData,
)

_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript)\b.*?</\1>", re.I | re.S
)
_BLOCK_SPLIT = re.compile(
    r"<(h[1-6]|p|li|table|pre|blockquote|figcaption)\b[^>]*>(.*?)</\1>", re.I | re.S
)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.I | re.S)


def _text(fragment: str) -> str:
    return html_module.unescape(_TAG.sub(" ", fragment)).strip()


def _clean(text: str) -> str:
    return " ".join(text.split())


class HtmlParser:
    parser_id = "html-trafilatura"
    parser_version = "1.1.0"

    def supports(self, mime: str) -> bool:
        return mime in ("text/html", "application/xhtml+xml")

    def parse(
        self, doc_id: str, blob: bytes, mime: str, hints: ParseHints
    ) -> ParsedDocument:
        try:
            source = blob.decode("utf-8")
        except UnicodeDecodeError:
            source = blob.decode("latin-1", errors="replace")

        declared_title = None
        title_match = _TITLE.search(source)
        if title_match:
            declared_title = _clean(_text(title_match.group(1))) or None

        source = _SCRIPT_STYLE.sub(" ", source)

        # trafilatura strips navigation, footers and related-links furniture,
        # which is the difference between retrieving a policy and retrieving a
        # site menu that appears on every page in the corpus.
        #
        # It is used as a *filter*, not as the source of structure. Its own
        # output drops heading elements entirely, and CNT-CHK-02 makes the
        # heading path load-bearing: "Rate limits" under API > v2 and under
        # Support > Escalation are different subjects, and without the path
        # they embed almost identically. So structure comes from the original
        # markup, and trafilatura decides which non-heading blocks survive.
        keep: set[str] | None = None
        try:
            import trafilatura

            main_text = trafilatura.extract(
                source, include_tables=True, include_links=False, no_fallback=False
            )
            if main_text and len(main_text) > 200:
                keep = {
                    _fingerprint(line)
                    for line in main_text.splitlines()
                    if len(line.strip()) > 24
                }
        except Exception:  # noqa: BLE001 - extraction is an improvement, not a gate
            keep = None
        boilerplate_removed = keep is not None

        blocks: list[Block] = []
        heading_path: list[str] = []
        ordinal = 0
        tables = 0

        for match in _BLOCK_SPLIT.finditer(source):
            tag = match.group(1).lower()
            inner = match.group(2)

            if tag.startswith("h") and len(tag) == 2:
                level = int(tag[1])
                text = _clean(_text(inner))
                if not text:
                    continue
                heading_path = heading_path[: level - 1]
                heading_path.append(text)
                blocks.append(
                    Block(
                        kind=BlockKind.HEADING,
                        text=text,
                        heading_path=tuple(heading_path),
                        level=level,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
                continue

            if tag == "table":
                table = _parse_table(inner)
                if table is None:
                    continue
                tables += 1
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

            text = _clean(_text(inner))
            if not text:
                continue
            if keep is not None and len(text) > 24 and not _retained(text, keep):
                # Present in the markup, absent from the extracted main
                # content: navigation, footer or related-links furniture.
                continue
            kind = {
                "li": BlockKind.LIST_ITEM,
                "pre": BlockKind.CODE,
                "figcaption": BlockKind.CAPTION,
            }.get(tag, BlockKind.PARAGRAPH)
            blocks.append(
                Block(
                    kind=kind,
                    text=text,
                    heading_path=tuple(heading_path),
                    ordinal=ordinal,
                )
            )
            ordinal += 1

        char_count = sum(len(b.text) for b in blocks)
        warnings: list[str] = []
        if not boilerplate_removed:
            warnings.append("boilerplate removal unavailable or declined; raw structure used")
        if not blocks:
            warnings.append("no content blocks extracted")

        return ParsedDocument(
            doc_id=doc_id,
            blocks=tuple(blocks),
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            title=declared_title,
            parse_path=ParsePath.HTML_TRAFILATURA,
            quality=ParseQuality(
                block_count=len(blocks),
                table_count=tables,
                char_count=char_count,
                warnings=tuple(warnings),
            ),
        )


def _fingerprint(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))[:160]


def _retained(text: str, keep: set[str]) -> bool:
    """Line-level matching is fragile across whitespace and entity handling, so
    compare on a normalised prefix and accept a containment match either way."""
    fingerprint = _fingerprint(text)
    if fingerprint in keep:
        return True
    return any(fingerprint in k or k in fingerprint for k in keep)


def _parse_table(inner: str) -> TableData | None:
    rows = []
    for row_match in _ROW.finditer(inner):
        cells = tuple(_clean(_text(c)) for c in _CELL.findall(row_match.group(1)))
        if cells:
            rows.append(cells)
    if not rows:
        return None
    header = rows[0] if "<th" in inner.lower() else ()
    body = tuple(rows[1:]) if header else tuple(rows)
    return TableData(header=header, rows=body)
