"""A polite HTTP crawler.

The only adapter in this codebase that touches a network we do not own, which
is why the manners are in the code rather than in a runbook:

  * **robots.txt is read first and obeyed.** Not as a courtesy — a crawler that
    ignores it is the reason sites start blocking crawlers.
  * **One request at a time, with a delay.** A help centre is sized for readers,
    not for us. Finishing a 114-page crawl thirty seconds sooner is worth
    nothing; being the reason someone's support site fell over is worth a great
    deal of negative.
  * **A real User-Agent naming the tool.** An operator looking at their access
    log should be able to tell what we are and switch us off.
  * **Sitemap first, links second.** A sitemap is the site telling us what it
    has. Crawling links to rediscover that is slower, less complete, and
    ruder.
  * **Conditional requests.** `If-None-Match` and `If-Modified-Since` turn a
    re-crawl into a series of 304s, which costs the site almost nothing.

Everything is bounded: pages, depth, bytes per page, total time.
"""

from __future__ import annotations

import gzip
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urljoin, urlsplit

from ...ports.crawler import CrawlPolicy, Discovery, Fetched

__all__ = ["CrawlPolicy", "Discovery", "Fetched", "HttpCrawler", "USER_AGENT"]

USER_AGENT = (
    "askcontent-crawler/0.1 "
    "(+https://example.com/askcontent; knowledgebase indexing; contact your administrator)"
)

_LOC = re.compile(rb"<loc>\s*([^<\s]+)\s*</loc>", re.I)
#: `<url>` entries pair a location with an optional `<lastmod>`. Captured
#: together, because the site stating when a page changed is better evidence
#: than anything we can infer — and this site sends no `Last-Modified` header,
#: so the sitemap is the only date there is.
_URL_ENTRY = re.compile(rb"<url>(.*?)</url>", re.I | re.S)
_LASTMOD = re.compile(rb"<lastmod>\s*([^<\s]+)\s*</lastmod>", re.I)
_LINK = re.compile(rb'href=["\']([^"\'#]+)', re.I)


class HttpCrawler:
    def __init__(self, policy: CrawlPolicy | None = None) -> None:
        self.policy = policy or CrawlPolicy()
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    # -- manners -----------------------------------------------------------

    def _robots_for(self, url: str):
        host = urlsplit(url).netloc
        if host not in self._robots:
            scheme = urlsplit(url).scheme or "https"
            robots_url = f"{scheme}://{host}/robots.txt"
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)

            # Deliberately not `parser.read()`. That fetches with the default
            # `Python-urllib/3.x` agent, which CDNs routinely answer with 403 —
            # and RobotFileParser reads a 403 as *disallow everything*. The
            # symptom is a crawler that politely refuses to crawl a site whose
            # robots.txt says `Allow: /`, which is exactly what happened here.
            #
            # Fetching it ourselves also means we identify ourselves for the one
            # request where it matters most.
            try:
                request = urllib.request.Request(
                    robots_url, headers={"User-Agent": USER_AGENT}
                )
                with urllib.request.urlopen(request, timeout=self.policy.timeout_seconds) as response:
                    body = response.read(256 * 1024).decode("utf-8", "replace")
                parser.parse(body.splitlines())
            except urllib.error.HTTPError as exc:
                # 401/403 mean "you may not read the rules", which the standard
                # treats as full disallow. Anything else — usually 404 — means
                # there are no rules, which is permission.
                if exc.code in (401, 403):
                    parser.disallow_all = True
                else:
                    parser.parse([])
            except Exception:  # noqa: BLE001
                parser.parse([])

            self._robots[host] = parser
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        try:
            return self._robots_for(url).can_fetch(USER_AGENT, url)
        except Exception:  # noqa: BLE001
            return True

    def _wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        delay = self.policy.delay_seconds
        robots = self._robots_for(url)
        try:
            declared = robots.crawl_delay(USER_AGENT)
            if declared:
                # The site asking for more space always wins; it never gets less.
                delay = max(delay, float(declared))
        except Exception:  # noqa: BLE001
            pass
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()

    # -- fetching ----------------------------------------------------------

    def fetch(self, url: str, *, etag: str | None = None,
              last_modified: str | None = None) -> Fetched:
        if not self.allowed(url):
            return Fetched(url=url, final_url=url, status=0,
                           error="disallowed by robots.txt")

        self._wait(url)
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "gzip",
            **({"If-None-Match": etag} if etag else {}),
            **({"If-Modified-Since": last_modified} if last_modified else {}),
        })

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.policy.timeout_seconds) as response:
                raw = response.read(self.policy.max_bytes_per_page + 1)
                if len(raw) > self.policy.max_bytes_per_page:
                    return Fetched(url=url, final_url=response.url, status=0,
                                   error=f"larger than the {self.policy.max_bytes_per_page} byte cap",
                                   elapsed_ms=(time.perf_counter() - started) * 1000)
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return Fetched(
                    url=url, final_url=response.url, status=response.status, body=raw,
                    content_type=response.headers.get("Content-Type", ""),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return Fetched(url=url, final_url=url, status=304,
                               elapsed_ms=(time.perf_counter() - started) * 1000)
            return Fetched(url=url, final_url=url, status=exc.code, error=str(exc.reason),
                           elapsed_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            return Fetched(url=url, final_url=url, status=0, error=str(exc),
                           elapsed_ms=(time.perf_counter() - started) * 1000)

    # -- discovery ---------------------------------------------------------

    def discover(self, root: str) -> Discovery:
        """What pages does this site have?

        Sitemap first, because it is the site telling us. Link crawling is the
        fallback and is slower, less complete and ruder.
        """
        result = Discovery()
        if not self.allowed(root):
            result.robots_allowed = False
            result.notes.append("robots.txt disallows the root URL")
            return result

        urls = self._filter(self._from_sitemaps(root, result), root)
        if not urls:
            # A sitemap that declares nothing under this root is the same
            # situation as no sitemap at all. It used to be treated as an
            # answer — "the site has no pages here" — which is how a help
            # centre with thousands of pages came back empty.
            result.source = "links"
            result.notes.append(
                "the sitemap declared nothing under this address; following links instead"
            )
            urls = self._filter(self._from_links(root, result), root)
        result.urls = urls

        if len(result.urls) > self.policy.max_pages:
            result.capped = True
            result.notes.append(
                f"capped at {self.policy.max_pages} of {len(result.urls)} discovered pages"
            )
            result.urls = result.urls[: self.policy.max_pages]
        return result

    def _sitemap_candidates(self, root: str) -> list[str]:
        parts = urlsplit(root)
        base = f"{parts.scheme}://{parts.netloc}"
        candidates = [urljoin(root.rstrip("/") + "/", "sitemap.xml"), f"{base}/sitemap.xml"]
        try:
            declared = self._robots_for(root).site_maps() or []
            candidates = list(declared) + candidates
        except Exception:  # noqa: BLE001
            pass
        seen, ordered = set(), []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        return ordered

    def _from_sitemaps(self, root: str, result: Discovery) -> list[str]:  # noqa: C901
        """Every page the site's own sitemaps declare, under this root.

        Two rules that look like details and are not, both learned from
        wellsfargo.com/help/: its sitemap index points at
        `locations.wellsfargo.com`, whose sitemap lists 10,001 branch
        locations. Collected wholesale and filtered at the end, those 10,001
        out-of-scope URLs *were* the discovery — the help pages never got
        looked at, and the crawl reported "0 pages planned from the sitemap"
        over a site with thousands of them.

        So: a sitemap on another host describes another site and is not
        followed, and pages are filtered as they are read rather than at the
        end, so the budget is spent on URLs that are actually in scope.
        """
        found: list[str] = []
        queue = self._sitemap_candidates(root)
        seen: set[str] = set()
        root_host = urlsplit(root).netloc

        while queue and len(found) < self.policy.max_pages * 4:
            candidate = queue.pop(0)
            if candidate in seen:
                continue
            seen.add(candidate)
            if self.policy.same_host_only and urlsplit(candidate).netloc != root_host:
                result.notes.append(f"skipped {candidate}: a different host")
                continue
            fetched = self.fetch(candidate)
            if not fetched.ok:
                continue
            entries = _URL_ENTRY.findall(fetched.body)
            locations: list[str] = []
            for entry in entries:
                loc = _LOC.search(entry)
                if not loc:
                    continue
                url = loc.group(1).decode("utf-8", "replace")
                locations.append(url)
                modified = _LASTMOD.search(entry)
                if modified:
                    result.lastmod[url.rstrip("/")] = modified.group(1).decode("utf-8", "replace")

            if not locations:
                # A sitemap *index* has <sitemap> entries, not <url> entries.
                locations = [m.decode("utf-8", "replace") for m in _LOC.findall(fetched.body)]
            if not locations:
                continue

            kept = 0
            for location in locations:
                # A sitemap index points at sitemaps; a sitemap points at pages.
                if location.endswith(".xml"):
                    queue.append(location)
                    continue
                # Filtered here rather than at the end. One irrelevant sitemap
                # must not be able to spend the whole budget.
                if self._in_scope(location, root):
                    found.append(location)
                    kept += 1
            result.notes.append(
                f"{candidate} listed {len(locations)} entries"
                + (f", {kept} under the root" if kept != len(locations) else "")
            )
        return found

    def _in_scope(self, url: str, root: str) -> bool:
        """Cheap prefix and host test, before anything is fetched.

        Deliberately not the full `_filter`: that one asks robots.txt about
        every URL, and asking ten thousand times to discard ten thousand
        results is the slow way to learn they were out of scope.
        """
        clean = url.split("#")[0].rstrip("/")
        if self.policy.same_host_only and urlsplit(clean).netloc != urlsplit(root).netloc:
            return False
        return clean.startswith(root.rstrip("/"))

    def _from_links(self, root: str, result: Discovery) -> list[str]:
        found: list[str] = [root]
        seen = {root}
        frontier = [(root, 0)]
        while frontier and len(found) < self.policy.max_pages:
            url, depth = frontier.pop(0)
            if depth >= self.policy.max_depth:
                continue
            fetched = self.fetch(url)
            if not fetched.ok or "html" not in fetched.content_type:
                continue
            for match in _LINK.findall(fetched.body):
                link = urljoin(fetched.final_url, match.decode("utf-8", "replace"))
                link = link.split("#")[0].rstrip("/")
                if link in seen or not link.startswith(("http://", "https://")):
                    continue
                seen.add(link)
                found.append(link)
                frontier.append((link, depth + 1))
        return found

    def _filter(self, urls: list[str], root: str) -> list[str]:
        import fnmatch

        host = urlsplit(root).netloc
        prefix = root.rstrip("/")
        out: list[str] = []
        for url in urls:
            clean = url.split("#")[0].rstrip("/")
            if self.policy.same_host_only and urlsplit(clean).netloc != host:
                continue
            if not clean.startswith(prefix):
                continue
            if self.policy.include and not any(
                fnmatch.fnmatch(clean, p) for p in self.policy.include
            ):
                continue
            if any(fnmatch.fnmatch(clean, p) for p in self.policy.exclude):
                continue
            if not self.allowed(clean):
                continue
            out.append(clean)
        # Stable, and shallow pages first: a crawl interrupted halfway should
        # have the overview pages, not an arbitrary slice of the deep ones.
        return sorted(dict.fromkeys(out), key=lambda u: (u.count("/"), u))
