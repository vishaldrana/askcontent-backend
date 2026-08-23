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
    # 1.2.2 — an empty fingerprint no longer poisons the keep-set.
    # 1.2.1 — a short extraction no longer disables boilerplate removal.
    # 1.2.0 — boilerplate removal now applies to short blocks. Before this the
    # length exemption skipped exactly the navigation it was meant to strip, so
    # every page in a documentation site carried the same 200-entry menu and
    # embedded almost identically. Bumping the version is what makes already
    # indexed documents re-parse: the bytes did not change, our reading of
    # them did, and the incremental skip has no other way to know that.
    parser_version = "1.2.2"

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
            # Any non-empty extraction is trusted. A length floor here fails
            # *open* — it disables boilerplate removal for short pages, which
            # are exactly the pages where a 200-entry menu outweighs the
            # content. On this corpus 9 pages in 114 are genuinely shorter than
            # 200 characters, and every one of them was being indexed as
            # navigation with a sentence attached.
            if main_text and main_text.strip():
                # Every line, with no length floor. A floor here is what makes
                # the whole filter miss navigation: menu entries are *short*
                # ("Introduction", "Mobile App SDK"), so excluding short lines
                # from the keep-set and short blocks from the check exempts
                # precisely the furniture the filter exists to remove.
                # Empty fingerprints must never enter the keep-set. A line of
                # "1." or a bullet normalises to "", and `"" in anything` is
                # True — one such line silently retains every block on the
                # page and disables the filter completely.
                keep = {
                    fingerprint
                    for line in main_text.splitlines()
                    if (fingerprint := _fingerprint(line))
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
            kind = {
                "li": BlockKind.LIST_ITEM,
                "pre": BlockKind.CODE,
                "figcaption": BlockKind.CAPTION,
            }.get(tag, BlockKind.PARAGRAPH)
            if keep is not None and not _retained(text, keep):
                # Present in the markup, absent from the extracted main
                # content: navigation, footer or related-links furniture.
                #
                # A short *paragraph* gets the benefit of the doubt, because
                # extractors split and rejoin prose unpredictably and a lost
                # sentence is worse than a kept one. A short *list item* does
                # not: that is what a menu is made of.
                if kind is BlockKind.PARAGRAPH and len(text) <= 24:
                    pass
                else:
                    continue
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


#: Below this many normalised characters a containment match means nothing:
#: "introduction" is a substring of half the prose on a documentation site, so
#: every nav entry would find a sponsor and survive.
_CONTAINMENT_MIN = 24


def _retained(text: str, keep: set[str]) -> bool:
    """Line-level matching is fragile across whitespace and entity handling, so
    compare on a normalised prefix and accept a containment match either way —
    but only for strings long enough for containment to be evidence."""
    fingerprint = _fingerprint(text)
    if fingerprint in keep:
        return True
    if len(fingerprint) < _CONTAINMENT_MIN:
        return False
    # Both sides must be long enough for containment to be evidence. A short
    # kept line contained in a long block says nothing about the block.
    return any(
        len(k) >= _CONTAINMENT_MIN and (fingerprint in k or k in fingerprint)
        for k in keep
    )


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
