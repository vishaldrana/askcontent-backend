"""The port for reading live values from a system that is not ours.

Narrow on purpose. The service layer decides *whether* to call and what to do
when the call fails; the adapter knows only how to make one HTTP request and
hand back a body. Splitting it here is what keeps the interesting decisions —
routing, caching, failure reporting, attribution — testable without a network,
and it is the same split as every other port in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Fetched:
    """One response, or the reason there is not one.

    `error` is a value rather than an exception for the same reason it is on
    `DatapointSet`: the caller's job when this fails is to answer from the
    passages and say the live data was unavailable, not to stop.
    """

    ok: bool
    payload: object = None
    error: str | None = None
    status: int = 0
    elapsed_ms: int = 0


class ContextFetcher(Protocol):
    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 3.0,
    ) -> Fetched:
        """One request. Never raises — a failure comes back as `Fetched.ok`."""
        ...
