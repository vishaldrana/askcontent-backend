"""One HTTP request to somebody else's API, with the guards that need to be here.

The guards are the reason this is not four lines of `httpx.get`.

**It never raises.** A live source that is down must not take the answer down
with it: the caller answers from the passages and says the figures were
unavailable, and it can only do that if a failure arrives as a value.

**It refuses to leave the web.** `file://`, `ftp://` and a scheme nobody has
thought of are not fetched. The URL is configuration and configuration is
trusted, but "trusted" and "unbounded" are different words, and this is the one
place in the system that turns a stored string into an outbound request.

**It caps what it reads.** A source answering with a hundred megabytes of JSON
is a source that would otherwise hold a worker thread until it finished.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from ...ports.context_source import Fetched

#: Enough for a page's worth of figures; far too little for a data export. A
#: source that needs more is being asked the wrong question.
MAX_BYTES = 256 * 1024

_ALLOWED_SCHEMES = ("https", "http")


class HttpContextFetcher:
    """The default fetcher. No dependency beyond the standard library."""

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 3.0,
    ) -> Fetched:
        parts = urlsplit(url)
        if parts.scheme not in _ALLOWED_SCHEMES:
            return Fetched(ok=False, error=f"unsupported scheme: {parts.scheme or 'none'}")
        if not parts.netloc:
            return Fetched(ok=False, error="no host in the configured URL")

        started = time.monotonic()
        request = urllib.request.Request(url, method=method.upper())
        for key, value in (headers or {}).items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(MAX_BYTES + 1)
                elapsed = int((time.monotonic() - started) * 1000)
                if len(body) > MAX_BYTES:
                    return Fetched(
                        ok=False, status=response.status, elapsed_ms=elapsed,
                        error=f"the response exceeded {MAX_BYTES // 1024} KB",
                    )
                try:
                    payload = json.loads(body.decode("utf-8", "replace") or "null")
                except json.JSONDecodeError:
                    return Fetched(
                        ok=False, status=response.status, elapsed_ms=elapsed,
                        error="the response was not JSON",
                    )
                return Fetched(
                    ok=True, payload=payload, status=response.status, elapsed_ms=elapsed,
                )
        except urllib.error.HTTPError as exc:
            # A 403 from the host's API is the *expected* outcome when a visitor
            # asks about something they cannot see, and it is not our error to
            # explain — it is reported as "unavailable" like any other, because
            # the alternative is telling a visitor which ids exist.
            return Fetched(
                ok=False, status=exc.code, error=f"the source answered {exc.code}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except TimeoutError:
            return Fetched(
                ok=False, error=f"the source did not answer within {timeout_seconds:g}s",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return Fetched(
                ok=False, error=str(exc) or exc.__class__.__name__,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
