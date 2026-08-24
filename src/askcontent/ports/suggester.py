"""The port for writing questions a reader might ask.

Separate from the answerer because the job is different and the failure is
different. An answerer must not go beyond its passages; a suggester must
produce something a person would type, and whether it can be answered is
decided afterwards, by a gate that reads the passages rather than by trusting
the model.
"""

from __future__ import annotations

from typing import Protocol


class Suggester(Protocol):
    def suggest(self, *, source: str, asked: str = "", limit: int = 4) -> list[str]:
        """Questions a reader of `source` might ask next. Never raises."""
        ...
