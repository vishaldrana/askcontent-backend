"""Planning and running a crawl, one page at a time.

Loading a site is two jobs, not one, and conflating them is what produces a
progress bar that sits at zero and then jumps to done:

    plan    ask the site what it has, write a row per page, commit
    load    fetch, parse, fingerprint and store each page, committing as it goes

Splitting them buys three things that matter to whoever is watching:

  * **A denominator.** "Page 38 of 114" is only possible if the total is known
    before the work starts.
  * **Resumability.** A crawl killed at page 60 resumes at 61, because the first
    60 are committed rows rather than state in a dead process.
  * **A meaningful cancel.** Stopping keeps what was loaded instead of
    discarding it.

Progress is written to the job row after every page, so the stream has something
real to read rather than a percentage interpolated from a timer.
"""

from __future__ import annotations

import datetime as dt
import json
import time

from sqlalchemy import text

from ..ports.crawler import CrawlPolicy, Fetched
from ..adapters.parsers.registry import parse_document
from ..config import settings
from ..domain.dates import resolve_dates, summarise
from ..domain.documents import ParseHints
from ..domain.fingerprint import compare, content_fingerprint, structure_fingerprint
from ..domain.ids import file_hash

S = settings.db_schema


class CrawlPlanner:
    def __init__(self, sessions, org_id, crawler, progress=None,
                 publisher=None) -> None:
        self.sessions = sessions
        self.org_id = org_id
        #: Required, and deliberately has no default. A default would put an
        #: `import` of the HTTP adapter in this module, and then "swap the
        #: adapter" would mean editing a service. A test hands in a fake that
        #: returns canned pages; the worker hands in the polite HTTP crawler.
        self._crawler = crawler
        #: Where a fetched page goes so that it becomes *answerable* rather than
        #: merely listed. Optional: a planner without one still produces a
        #: correct plan and correct membership, it just leaves a corpus nobody
        #: can query — which is a useful thing to be able to test in isolation,
        #: and a useless thing to ship.
        self.publisher = publisher
        #: Called after every page with the current snapshot. The worker uses it
        #: to write `job.progress`; a CLI run uses it to print a line.
        self.progress = progress or (lambda snapshot: None)

    # -- phase one ---------------------------------------------------------

    def plan(self, slug: str, root: str, *, policy: CrawlPolicy | None = None) -> dict:
        crawler = self._crawler
        self.progress({"phase": "plan", "message": f"asking {root} what it has", "done": 0, "total": 0})

        discovery = crawler.discover(root)
        if not discovery.robots_allowed:
            return {"phase": "plan", "error": "robots.txt disallows this site", "total": 0}

        with self.sessions() as session:
            collection = session.execute(text(
                f"SELECT id FROM {S}.collection WHERE org_id = :o AND slug = :s"
            ), {"o": self.org_id, "s": slug}).mappings().one()

            # The crawl root is a *source*, recorded like any other. Without
            # this the knowledgebase can show what it holds but not what was
            # asked for — and "why is this page in here" becomes unanswerable
            # the moment the person who typed the root has moved on.
            rule_id = session.execute(text(f"""
                SELECT id FROM {S}.collection_rule
                WHERE collection_id = :c AND kind = 'crawl'
                  AND CAST(config ->> 'root' AS text) = :root
            """), {"c": collection["id"], "root": root}).scalar_one_or_none()

            if rule_id is None:
                rule_id = session.execute(text(f"""
                    INSERT INTO {S}.collection_rule
                        (org_id, collection_id, ordinal, kind, effect, config,
                         enumerable, last_run_at, last_candidate_count, capped)
                    VALUES (:o, :c,
                            (SELECT coalesce(max(ordinal), -1) + 1
                               FROM {S}.collection_rule WHERE collection_id = :c),
                            'crawl', 'include', CAST(:config AS jsonb),
                            true, now(), :found, :capped)
                    RETURNING id
                """), {
                    "o": self.org_id, "c": collection["id"],
                    "config": json.dumps({"root": root, "discovered_via": discovery.source}),
                    "found": len(discovery.urls), "capped": discovery.capped,
                }).scalar_one()
            else:
                session.execute(text(f"""
                    UPDATE {S}.collection_rule
                       SET last_run_at = now(), last_candidate_count = :found,
                           capped = :capped, config = CAST(:config AS jsonb),
                           updated_at = now()
                     WHERE id = :id
                """), {
                    "id": rule_id, "found": len(discovery.urls), "capped": discovery.capped,
                    "config": json.dumps({"root": root, "discovered_via": discovery.source}),
                })

            for index, url in enumerate(discovery.urls):
                # One row per page, before a single page is fetched. This row is
                # the plan; everything after it is progress against the plan.
                modified = _parse_iso(discovery.lastmod.get(url.rstrip("/")))
                session.execute(text(f"""
                    INSERT INTO {S}.collection_member
                        (org_id, collection_id, doc_id, url, title, state, resolved_via,
                         source_updated_at, updated_source, contributed_by)
                    VALUES (:o, :c, :doc, :url, :title, 'planned', 'crawl',
                            :modified, :usource, ARRAY[:rule])
                    ON CONFLICT (collection_id, doc_id) DO UPDATE
                       SET url = EXCLUDED.url,
                           contributed_by = (
                               SELECT array_agg(DISTINCT x)
                               FROM unnest({S}.collection_member.contributed_by
                                           || EXCLUDED.contributed_by) AS x
                           ),
                           source_updated_at = coalesce(EXCLUDED.source_updated_at,
                                                        {S}.collection_member.source_updated_at),
                           updated_source = CASE WHEN EXCLUDED.source_updated_at IS NOT NULL
                                                 THEN 'metadata'
                                                 ELSE {S}.collection_member.updated_source END,
                           state = CASE WHEN {S}.collection_member.state = 'member'
                                        THEN 'member' ELSE 'planned' END
                """), {
                    "o": self.org_id, "c": collection["id"], "doc": url,
                    "url": url, "title": _title_from_url(url),
                    "modified": modified,
                    "usource": "metadata" if modified else "none",
                    "rule": str(rule_id),
                })
                if index % 25 == 0:
                    self.progress({
                        "phase": "plan", "done": index, "total": len(discovery.urls),
                        "message": "writing the plan",
                    })
            session.commit()

        return {
            "phase": "plan", "total": len(discovery.urls), "source": discovery.source,
            "capped": discovery.capped, "notes": discovery.notes,
            "summary": f"{len(discovery.urls)} pages planned from the {discovery.source}",
        }

    # -- phase two ---------------------------------------------------------

    def load(self, slug: str, *, policy: CrawlPolicy | None = None,
             batch: int = 0, should_stop=None, kb_id: str | None = None,
             space: str | None = None) -> dict:
        crawler = self._crawler
        started = time.monotonic()

        with self.sessions() as session:
            collection = session.execute(text(
                f"SELECT id FROM {S}.collection WHERE org_id = :o AND slug = :s"
            ), {"o": self.org_id, "s": slug}).mappings().one()
            collection_id = collection["id"]
            # A crawled site publishes into a knowledgebase of its own, named
            # after the collection unless the caller says otherwise. Mixing
            # crawled pages into an existing kb would make "what is in this
            # knowledgebase" un-answerable the moment two crawls overlap.
            kb_id = kb_id or f"kb-{slug}"
            space = space or slug.upper().replace("-", "_")[:32]

            total = session.execute(text(f"""
                SELECT count(*) FROM {S}.collection_member
                WHERE collection_id = :c AND resolved_via = 'crawl'
            """), {"c": collection_id}).scalar_one()

            pending = session.execute(text(f"""
                SELECT doc_id, url, content_hash, structure_hash, source_updated_at
                FROM {S}.collection_member
                WHERE collection_id = :c AND resolved_via = 'crawl'
                  AND state IN ('planned', 'failed')
                ORDER BY length(doc_id), doc_id
                {"LIMIT :n" if batch else ""}
            """), {"c": collection_id, **({"n": batch} if batch else {})}).mappings().all()

        already = total - len(pending)
        counts = {"loaded": 0, "unchanged": 0, "failed": 0, "skipped": 0, "bytes": 0}

        for index, member in enumerate(pending, start=1):
            if should_stop and should_stop():
                break
            if time.monotonic() - started > (policy or CrawlPolicy()).max_total_seconds:
                counts["skipped"] = len(pending) - index + 1
                break

            done = already + index
            # Emitted *before* the fetch, so the page currently being loaded is
            # named while it is being loaded rather than after it finishes.
            self.progress({
                "phase": "load", "done": done - 1, "total": total,
                "current": member["url"], "message": "fetching",
                "counts": dict(counts),
                "elapsed_s": round(time.monotonic() - started, 1),
                "rate_per_min": _rate(index - 1, started),
                "eta_s": _eta(index - 1, len(pending), started),
            })

            fetched = crawler.fetch(member["url"])
            outcome = self._store(collection_id, member, fetched,
                                  kb_id=kb_id, space=space)
            counts[outcome] = counts.get(outcome, 0) + 1
            counts["bytes"] += len(fetched.body)

            self.progress({
                "phase": "load", "done": done, "total": total,
                "current": member["url"], "message": outcome,
                "counts": dict(counts),
                "elapsed_s": round(time.monotonic() - started, 1),
                "rate_per_min": _rate(index, started),
                "eta_s": _eta(index, len(pending), started),
                "last": {"url": member["url"], "outcome": outcome,
                         "ms": round(fetched.elapsed_ms)},
            })

        self._tidy_titles(collection_id, kb_id)

        return {
            "phase": "load", "total": total, "processed": len(pending),
            **counts,
            "elapsed_s": round(time.monotonic() - started, 1),
            "summary": (
                f"{counts['loaded']} loaded, {counts['unchanged']} unchanged, "
                f"{counts['failed']} failed of {total} planned"
            ),
        }

    def _store(self, collection_id, member, fetched: Fetched, *,
               kb_id: str | None = None, space: str | None = None) -> str:
        if fetched.unchanged:
            self._touch(collection_id, member["doc_id"], "member", None)
            return "unchanged"
        if not fetched.ok:
            self._touch(collection_id, member["doc_id"], "failed",
                        fetched.error or f"HTTP {fetched.status}")
            return "failed"

        parsed = parse_document(
            member["doc_id"], fetched.body,
            declared_mime=fetched.content_type.split(";")[0].strip() or None,
            hints=ParseHints(base_url=member["url"]), sandbox=False,
        )
        if parsed.refused:
            self._touch(collection_id, member["doc_id"], "failed", parsed.refusal_reason)
            return "failed"

        body = parsed.full_text()
        chash = content_fingerprint(parsed.blocks)
        verdict = compare(
            old_file=None, new_file=file_hash(fetched.body),
            old_content=member["content_hash"], new_content=chash,
            old_structure=member["structure_hash"],
            new_structure=structure_fingerprint(parsed.blocks),
        )
        # A date the *source* asserts beats one read out of prose: a help
        # centre rarely prints a date in its body, and guessing from the text
        # is how a page dated "January 2024" in an example gets filed as
        # current. Order of preference is `Last-Modified`, then the sitemap's
        # `<lastmod>` captured at plan time, then the prose. Both of the first
        # two are absent often enough to need the third — this site sends no
        # `Last-Modified` at all, and its sitemap is the only date there is.
        header_date = _parse_http_date(fetched.last_modified) or member["source_updated_at"]
        created, updated = resolve_dates(header_date, body)
        title = _page_title(parsed) or _title_from_url(member["url"])
        path = "/" + member["url"].split("//", 1)[-1].split("/", 1)[-1]

        with self.sessions() as session:
            session.execute(text(f"""
                UPDATE {S}.collection_member SET
                    state = 'member', title = :title, description = :description,
                    path = :path, content_hash = :chash, structure_hash = :shash,
                    source_created_at = :created, source_updated_at = :updated,
                    created_source = :csource, updated_source = :usource,
                    date_evidence = :evidence, last_verdict = :verdict,
                    last_checked_at = now(),
                    last_changed_at = CASE WHEN :moved THEN now() ELSE last_changed_at END,
                    missing_since = NULL
                WHERE collection_id = :c AND doc_id = :d
            """), {
                "c": collection_id, "d": member["doc_id"], "title": title,
                "description": summarise(body),
                "path": path,
                "chash": chash,
                "shash": structure_fingerprint(parsed.blocks),
                "created": created.value, "updated": updated.value,
                "csource": str(created.source), "usource": str(updated.source),
                "evidence": updated.evidence or created.evidence,
                "verdict": str(verdict.verdict),
                "moved": verdict.needs_reindex,
            })
            session.commit()

        # Published *after* the member row is committed. If publishing fails,
        # the page is still a known member and the next run retries it; the
        # reverse order would leave a document in the index that nothing in the
        # collection accounts for.
        if self.publisher is not None and kb_id:
            self.publisher.publish(
                doc_id=member["doc_id"], kb_id=kb_id, space=space or kb_id,
                title=title, path=path, url=member["url"],
                version=chash, body=fetched.body, text_body=body,
                mime=(fetched.content_type.split(";")[0].strip() or "text/html"),
                updated_at=updated.value,
                labels=["crawled"],
                sensitivity="public",
                acl_principals=["group:all-staff"],
            )
        return "loaded"

    def _tidy_titles(self, collection_id, kb_id: str | None = None) -> None:
        """Drop the site-wide title suffix, once, across the whole collection.

        Done after the load rather than per page, because the suffix can only be
        recognised by looking at the set: one title ending "| Product Guide"
        might be a page about the product guide.
        """
        with self.sessions() as session:
            titles = session.execute(text(f"""
                SELECT doc_id, title FROM {S}.collection_member
                WHERE collection_id = :c AND title IS NOT NULL
            """), {"c": collection_id}).mappings().all()

            suffix = strip_common_suffix([row["title"] for row in titles])
            if not suffix:
                return
            session.execute(text(f"""
                UPDATE {S}.collection_member
                SET title = left(title, length(title) - :n)
                WHERE collection_id = :c AND title LIKE :pattern
            """), {"c": collection_id, "n": len(suffix), "pattern": f"%{suffix}"})
            session.commit()

        # The published copies carry the same suffix, and a citation showing
        # "Terminate | Product Guide" while the console shows "Terminate" is
        # the corpus disagreeing with itself.
        if self.publisher is not None and kb_id:
            self.publisher.strip_title_suffix(kb_id, suffix)

    def _touch(self, collection_id, doc_id: str, state: str, error: str | None) -> None:
        with self.sessions() as session:
            session.execute(text(f"""
                UPDATE {S}.collection_member
                SET state = :state, last_checked_at = now(),
                    date_evidence = coalesce(:error, date_evidence)
                WHERE collection_id = :c AND doc_id = :d
            """), {"c": collection_id, "d": doc_id, "state": state, "error": error})
            session.commit()


def _rate(done: int, started: float) -> float:
    elapsed = max(0.001, time.monotonic() - started)
    return round(done / elapsed * 60, 1)


def _eta(done: int, total: int, started: float) -> int | None:
    if done <= 0:
        return None
    elapsed = time.monotonic() - started
    return int((total - done) * (elapsed / done))


def _parse_iso(value: str | None):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_http_date(value: str | None):
    """RFC 7231 date, as sent in `Last-Modified`."""
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    except Exception:  # noqa: BLE001
        return None


def strip_common_suffix(titles: list[str]) -> str | None:
    """The " | Site Name" that a CMS appends to every page.

    Detected rather than configured: if most titles end in the same
    separator-delimited segment, it is the site's name and not the page's. A
    list where every row ends in the same four words is a list nobody can scan.
    """
    from collections import Counter

    suffixes = Counter()
    for title in titles:
        for separator in (" | ", " - ", " — ", " :: "):
            if separator in title:
                suffixes[separator + title.rsplit(separator, 1)[1]] += 1
    if not suffixes:
        return None
    suffix, count = suffixes.most_common(1)[0]
    return suffix if count >= max(3, int(0.7 * len(titles))) else None


def _title_from_url(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip().title() or url


def _page_title(parsed) -> str | None:
    if parsed.title:
        return parsed.title.strip()[:200]
    for block in parsed.blocks:
        if str(block.kind) == "heading" and block.text.strip():
            return block.text.strip()[:200]
    return None
