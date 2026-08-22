"""Parser port (CNT-PAR-04)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.documents import ParsedDocument, ParseHints


class ParserUnavailable(RuntimeError):
    """The component for this format is not installed.

    Surfaced as a *reported* capability gap, never as a silently worse parse
    (CNT-PAR-11): a document the platform cannot read reliably is reported, not
    indexed at low confidence.
    """


@runtime_checkable
class Parser(Protocol):
    parser_id: str
    parser_version: str

    def supports(self, mime: str) -> bool: ...

    def parse(self, doc_id: str, blob: bytes, mime: str, hints: ParseHints) -> ParsedDocument: ...
