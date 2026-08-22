"""Canonical document model.

Two systems supply this: PGP (the index) knows identifiers and its own copy of
metadata; the ECM (the store) knows the bytes and the authoritative metadata.
Where they disagree the ECM wins by default (CNT-MAP-05), and the disagreement
is recorded rather than smoothed over.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Sensitivity(StrEnum):
    """Ordered. A connector declares the highest class it may ingest."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return _SENSITIVITY_ORDER.index(self)


_SENSITIVITY_ORDER = [
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.RESTRICTED,
]


class DocType(StrEnum):
    POLICY = "policy"
    PROCEDURE = "procedure"
    DECISION = "decision"
    SPECIFICATION = "specification"
    REFERENCE = "reference"
    FAQ = "faq"
    NOTES = "notes"
    REPORT = "report"
    PAGE = "page"


class AuthorityTier(StrEnum):
    AUTHORITATIVE = "authoritative"
    SUPPORTING = "supporting"
    ARCHIVE = "archive"


class Staleness(StrEnum):
    FRESH = "fresh"
    AGEING = "ageing"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN_AGE = "unknown_age"


class DocRef(BaseModel):
    """What the index hands back. Not a document — an address for one."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    kb_id: str
    repository: str = "ecm"


class DocMetadata(BaseModel):
    """Canonical fields, produced by the field map (CNT-MAP-01).

    `extras` retains every unmapped source field verbatim (CNT-MAP-06): the
    field nobody mapped is routinely the one carrying the authority signal, and
    discarding it means re-ingesting to get it back.
    """

    doc_id: str
    kb_id: str
    title: str
    url: str
    updated_at: dt.datetime | None = None
    space: str | None = None
    owner: str | None = None
    labels: tuple[str, ...] = ()
    doc_type: DocType | None = None
    doc_type_source: str | None = None  # "mapped" | "ladder"
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    acl_principals: tuple[str, ...] = ()
    authority: AuthorityTier = AuthorityTier.SUPPORTING
    version: str | None = None
    mime: str | None = None
    size_bytes: int | None = None
    path: str | None = None
    extras: dict[str, str] = Field(default_factory=dict)

    # Recorded, never silently reconciled. Surfaces in the trace and the probe.
    disagreements: tuple[str, ...] = ()


class RawDocument(BaseModel):
    """Bytes plus authoritative metadata, straight from the ECM."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ref: DocRef
    blob: bytes
    mime: str
    metadata: DocMetadata


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"
    CODE = "code"
    FIGURE = "figure"


class TableData(BaseModel):
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    def render(self) -> str:
        """Markdown. Tables are never flattened to prose (CNT-CHK-03)."""
        lines = []
        if self.header:
            lines.append("| " + " | ".join(self.header) + " |")
            lines.append("| " + " | ".join("---" for _ in self.header) + " |")
        for row in self.rows:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)


class Block(BaseModel):
    kind: BlockKind
    text: str
    heading_path: tuple[str, ...] = ()
    level: int | None = None
    table: TableData | None = None
    page: int | None = None
    ordinal: int = 0


class ParsePath(StrEnum):
    """Which rung of the ladder produced this (CNT-PAR-10, CNT-PAR-12)."""

    HTML_TRAFILATURA = "html_trafilatura"
    PDF_TEXT_LAYER = "pdf_text_layer"
    PDF_LAYOUT = "pdf_layout"
    PDF_OCR = "pdf_ocr"
    REFUSED = "refused"


class ParseQuality(BaseModel):
    text_yield_per_page: float | None = None
    ocr_confidence: float | None = None
    block_count: int = 0
    table_count: int = 0
    char_count: int = 0
    warnings: tuple[str, ...] = ()


class ParsedDocument(BaseModel):
    """The artifact of record (CNT-PAR-15): stored, versioned, never re-derived."""

    doc_id: str
    blocks: tuple[Block, ...]
    parser_id: str
    parser_version: str
    parse_path: ParsePath
    quality: ParseQuality
    #: The title the *document* declares — a PDF's Info dictionary, an HTML
    #: `<title>`. Distinct from the first block of body text, which is what you
    #: get by guessing, and which for a PDF is the title run together with the
    #: opening paragraph. Duplicate detection depends on the difference: the
    #: real title matches an indexed document, the merged paragraph does not.
    title: str | None = None
    refusal_reason: str | None = None

    @property
    def refused(self) -> bool:
        return self.parse_path is ParsePath.REFUSED

    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


class ParseHints(BaseModel):
    max_pages: int = 500
    max_bytes: int = 64 * 1024 * 1024
    timeout_seconds: float = 30.0
    base_url: str | None = None
