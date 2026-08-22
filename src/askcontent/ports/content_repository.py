"""ContentRepository — the port the ECM sits behind.

Holds things. Ranks nothing. This is the system of record: where its metadata
disagrees with the index's, this one wins by default (CNT-MAP-05).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ..domain.documents import DocMetadata, DocRef, RawDocument


class ResolutionOutcome(StrEnum):
    """All four occur in production; each is handled explicitly (CNT-RET-06)."""

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"        # index holds an id the store no longer has
    FORBIDDEN = "forbidden"        # indexed, but not readable by this principal
    UNAVAILABLE = "unavailable"    # store error or timeout


class Resolution(BaseModel):
    ref: DocRef
    outcome: ResolutionOutcome
    metadata: DocMetadata | None = None
    detail: str | None = None


class RepositoryUnavailable(RuntimeError):
    pass


@runtime_checkable
class ContentRepository(Protocol):
    def fetch(self, ref: DocRef, principal: str) -> RawDocument: ...

    def fetch_metadata(self, ref: DocRef, principal: str) -> Resolution: ...

    def search(
        self, query: str, principal: str, k: int = 20, spaces: tuple[str, ...] = ()
    ) -> list[DocRef]:
        """Native keyword/metadata search — the second channel (CNT-RET-03).

        This channel exists because vector search misses exact identifiers,
        rare tokens, part numbers and ticket references, and that is the
        failure users notice.
        """
        ...
