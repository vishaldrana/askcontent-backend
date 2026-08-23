"""The crawler port.

A service says *what* to fetch; an adapter decides *how*. The data types live
here rather than in the adapter so that `services/` can name them without
importing HTTP, sockets or robots.txt handling — the same separation the index,
repository, parser and reranker ports already draw.

The point is not ceremony. It is that a second implementation — a Confluence
API walker, a SharePoint enumerator, a filesystem loader, a fake that returns
canned pages in a test — can be substituted without touching the planner that
turns pages into a corpus. The planner is the part with the interesting logic;
it should not be reachable only through a live network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CrawlPolicy:
    """The limits a crawl runs under.

    Every field is a bound on someone else's server. Defaults are deliberately
    timid: a crawler that has to be tuned *down* after it has already hit a
    site has already done the damage.
    """

    max_pages: int = 500
    max_depth: int = 4
    max_bytes_per_page: int = 8 * 1024 * 1024
    #: Seconds between requests to one host. robots.txt Crawl-delay wins if it
    #: asks for more; it never lowers this.
    delay_seconds: float = 0.4
    timeout_seconds: float = 20.0
    same_host_only: bool = True
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    max_total_seconds: float = 900.0


@dataclass
class Fetched:
    """One page, or one failure. Both are outcomes, and both are recorded."""

    url: str
    final_url: str
    status: int
    body: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.body)

    @property
    def unchanged(self) -> bool:
        return self.status == 304


@dataclass
class Discovery:
    """What a source says it holds, before anything is fetched."""

    urls: list[str] = field(default_factory=list)
    #: url -> the site's own last-modified claim, where the sitemap gave one.
    lastmod: dict[str, str] = field(default_factory=dict)
    source: str = "sitemap"
    robots_allowed: bool = True
    notes: list[str] = field(default_factory=list)
    capped: bool = False


class Crawler(Protocol):
    """Two operations, in the order a crawl uses them."""

    def discover(self, root: str) -> Discovery:
        """Ask the source what it has. Fetches only what discovery needs."""
        ...

    def fetch(self, url: str, *, etag: str | None = None) -> Fetched:
        """Fetch one page. Never raises for an HTTP-level failure — a failure
        is a `Fetched` with a status and an error, because a crawl that dies on
        one bad page is a crawl nobody can finish."""
        ...
