"""Deciding whether to read live values, reading them, and caching the result.

Everything interesting about design 09 step 3 is here rather than in the
adapter: whether the call is worth making, what identity it carries, what to
keep from the response, and what to say when it fails.
"""

from __future__ import annotations

import datetime as dt
import re
import threading

from ..domain.datapoint import (
    ContextSource,
    DatapointSet,
    extract,
    should_fetch,
)
from ..ports.context_source import ContextFetcher


class _Cache:
    """Keyed by (connector, key), bounded, and expired by the source's ttl.

    In process, deliberately. A shared cache would need an invalidation story
    the first time a host's figures changed, and the window this is protecting
    is a visitor asking three questions in a row about the same screen — which
    happens inside one process and inside a minute.
    """

    #: A widget left open on a dashboard for a week must not become a leak, and
    #: this is the same reasoning as the widget's own message cap.
    LIMIT = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], tuple[dt.datetime, DatapointSet]] = {}

    def get(self, key: tuple[str, str], ttl: int) -> DatapointSet | None:
        now = dt.datetime.now(dt.timezone.utc)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            at, value = entry
            if (now - at).total_seconds() > ttl:
                self._entries.pop(key, None)
                return None
            return value.model_copy(update={"cached": True})

    def put(self, key: tuple[str, str], value: DatapointSet) -> None:
        with self._lock:
            if len(self._entries) >= self.LIMIT:
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (dt.datetime.now(dt.timezone.utc), value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


CACHE = _Cache()


def parse(config: object) -> ContextSource | None:
    """A stored configuration, or nothing.

    Nothing is the answer for a connector that has none *and* for one whose
    stored configuration no longer validates. A malformed source that silently
    became "call this URL with no timeout" is worse than one that is off.
    """
    if not isinstance(config, dict) or not config:
        return None
    try:
        source = ContextSource.model_validate(config)
    except Exception:  # noqa: BLE001
        return None
    return source if source.enabled and source.url and source.fields else None


def key_allowed(source: ContextSource, key: str) -> bool:
    """The key the page supplied, checked against what the embed permits.

    Checked before any call, so a crafted request is a refusal rather than an
    outbound HTTP request to somebody else's API with an attacker's id in it.
    An unset pattern permits anything, which is the right default only because
    the key already cannot come from the model.
    """
    if not source.key_pattern:
        return True
    try:
        return re.fullmatch(source.key_pattern, key) is not None
    except re.error:
        # A pattern that does not compile is a configuration mistake, and the
        # safe reading of a broken gate is "closed".
        return False


def read(
    source: ContextSource | None,
    *,
    connector_id: str,
    question: str,
    key: str,
    corpus_covers: bool,
    visitor_token: str = "",
    fetcher: ContextFetcher,
) -> DatapointSet | None:
    """Live values for this question, or `None` if none were called for.

    `None` and an empty-but-present set mean different things and both happen:
    `None` is "no call was made", which is silent; a set carrying an `error` is
    "a call was made and failed", which the reader is told about.
    """
    if source is None:
        return None

    verdict = should_fetch(question, has_key=bool(key), corpus_covers=corpus_covers)
    if not verdict.fetch:
        return None

    if not key_allowed(source, key):
        # Not reported to the reader. A visitor cannot fix this and telling
        # them which key shapes are accepted is an invitation; it belongs in
        # the trace, where the administrator who configured it will look.
        return None

    cached = CACHE.get((connector_id, key), source.ttl_seconds)
    if cached is not None:
        return cached

    headers = {"accept": "application/json"}
    if source.auth == "forward_visitor" and visitor_token:
        # The visitor's own credential, so the host applies the host's rules.
        # Without a token we still call — the host may not need one — but we
        # never substitute a service credential for a missing visitor one,
        # because that turns "this reader may not see it" into "we saw it".
        headers["authorization"] = visitor_token

    fetched = fetcher.fetch(
        source.target(key),
        method=source.method,
        headers=headers,
        timeout_seconds=source.timeout_seconds,
    )

    if not fetched.ok:
        # Not cached. A failure that stuck for the ttl would turn one timeout
        # into a minute of them.
        return DatapointSet(
            source=source.name, key=key, error=fetched.error or "unavailable",
            elapsed_ms=fetched.elapsed_ms,
        )

    points = extract(
        fetched.payload, source.fields,
        source=source.name, key=key,
        fetched_at=dt.datetime.now(dt.timezone.utc),
        ttl_seconds=source.ttl_seconds,
    )
    result = DatapointSet(
        source=source.name, key=key, points=points, elapsed_ms=fetched.elapsed_ms,
    )
    if points:
        CACHE.put((connector_id, key), result)
    return result
