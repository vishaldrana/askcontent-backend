"""The worker.

Two agents, one loop. Adding a knowledgebase enqueues work rather than doing it
in the request — materialising a collection fetches and parses every member, and
a request that does that times out on the first realistic corpus.

    python -m askcontent.worker            run until stopped
    python -m askcontent.worker --once     drain the queue and exit

Job kinds:

  collection.materialise   evaluate the rules and apply the diff
  collection.enrich        recover title, description and dates for each member
  collection.refresh       re-check members by URL and report what changed
  connector.index          parse, chunk and embed everything in scope

Claiming uses `FOR UPDATE SKIP LOCKED`, so several workers can run against one
queue without coordinating and without two of them doing the same job. A job
that fails is retried with backoff up to its attempt limit and then left
`failed` with its error — never silently dropped, because a job that vanishes
looks exactly like one that succeeded.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import socket
import sys
import time
import traceback
import uuid

from sqlalchemy import text

from .config import settings

S = settings.db_schema
POLL_SECONDS = 2.0
WORKER = f"{socket.gethostname()}:{os.getpid()}"

_stop = False


def _handle_signal(signum, frame) -> None:  # noqa: ANN001
    global _stop
    _stop = True
    print("\nstopping after the current job…", flush=True)


def enqueue(sessions, org_id, kind: str, *, connector_id=None, collection_id=None,
            payload: dict | None = None, run_after: dt.datetime | None = None) -> str:
    """Put work on the queue.

    Idempotent per (kind, target) while a job is still queued: adding three
    rules to a collection should schedule one materialisation, not three.
    """
    with sessions() as session:
        existing = session.execute(text(f"""
            SELECT id FROM {S}.job
            WHERE org_id = :o AND kind = :k AND status IN ('queued', 'retry')
              -- CAST(...) rather than `::text`: SQLAlchemy's text() reads the
              -- second colon of `:c::text` as the start of another bind
              -- parameter, and the statement reaches Postgres malformed.
              AND coalesce(CAST(connector_id AS text), '') = coalesce(CAST(:c AS text), '')
              AND coalesce(CAST(collection_id AS text), '') = coalesce(CAST(:col AS text), '')
        """), {"o": org_id, "k": kind, "c": connector_id, "col": collection_id}).scalar()
        if existing:
            return str(existing)

        job_id = session.execute(text(f"""
            INSERT INTO {S}.job (org_id, connector_id, collection_id, kind, status,
                                 payload, run_after)
            VALUES (:o, :c, :col, :k, 'queued', :p, coalesce(:after, now()))
            RETURNING id
        """), {"o": org_id, "c": connector_id, "col": collection_id, "k": kind,
               "p": json.dumps(payload or {}), "after": run_after}).scalar_one()
        session.commit()
        return str(job_id)


def _claim(sessions):
    """Take exactly one job, or nothing.

    `SKIP LOCKED` is what makes this safe to run several times over: a row
    another worker holds is passed over rather than waited on.
    """
    with sessions() as session:
        row = session.execute(text(f"""
            UPDATE {S}.job SET status = 'running', locked_at = now(),
                               locked_by = :who, attempts = attempts + 1,
                               started_at = coalesce(started_at, now())
            WHERE id = (
                SELECT id FROM {S}.job
                WHERE status IN ('queued', 'retry') AND run_after <= now()
                ORDER BY run_after, created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id, org_id, kind, connector_id, collection_id, payload, attempts, max_attempts
        """), {"who": WORKER}).mappings().one_or_none()
        session.commit()
        return dict(row) if row else None


def _finish(sessions, job_id, *, progress: dict, error: str | None,
            attempts: int, max_attempts: int) -> None:
    with sessions() as session:
        if error is None:
            session.execute(text(f"""
                UPDATE {S}.job SET status = 'done', progress = :p, error = NULL,
                                   finished_at = now(), locked_at = NULL, locked_by = NULL
                WHERE id = :id
            """), {"id": job_id, "p": json.dumps(progress)})
        elif attempts < max_attempts:
            # Exponential backoff. A source that is down stays down for a while,
            # and hammering it turns one outage into two.
            delay = min(300, 5 * (2 ** (attempts - 1)))
            session.execute(text(f"""
                UPDATE {S}.job SET status = 'retry', error = :e,
                                   run_after = now() + make_interval(secs => :d),
                                   locked_at = NULL, locked_by = NULL
                WHERE id = :id
            """), {"id": job_id, "e": error[:2000], "d": delay})
        else:
            session.execute(text(f"""
                UPDATE {S}.job SET status = 'failed', error = :e, finished_at = now(),
                                   locked_at = NULL, locked_by = NULL
                WHERE id = :id
            """), {"id": job_id, "e": error[:2000]})
        session.commit()


def _run_job(platform, sessions, job) -> dict:
    from .services.enrichment import EnrichmentService
    from .services.indexing import IndexingService

    org_id = job["org_id"]
    kind = job["kind"]
    payload = job["payload"] or {}

    if kind == "connector.index":
        connector = platform.registry.get(payload["connector"])
        report = IndexingService(platform, sessions, org_id).index_connector(connector)
        return report.__dict__ | {"summary": report.line()}

    if kind == "collection.materialise":
        from .api.extra import materialise

        result = materialise(payload["collection"], _Apply(True))
        # A freshly materialised collection has members with nothing but an id.
        enqueue(sessions, org_id, "collection.enrich",
                collection_id=job["collection_id"],
                payload={"collection": payload["collection"]})
        return result

    if kind == "collection.enrich":
        return EnrichmentService(platform, sessions, org_id).enrich_collection(
            payload["collection"]
        )

    if kind == "collection.refresh":
        service = EnrichmentService(platform, sessions, org_id)
        report = service.check_for_updates(payload["collection"])
        # Anything whose content moved needs re-chunking and re-embedding, and
        # the connector index is where that happens.
        if report["changed"] and payload.get("connector"):
            enqueue(sessions, org_id, "connector.index",
                    payload={"connector": payload["connector"]})
        return report

    raise ValueError(f"unknown job kind: {kind}")


class _Apply:
    """The shape `materialise` expects, without importing FastAPI's model."""

    def __init__(self, apply: bool) -> None:
        self.apply = apply


def run(once: bool = False) -> int:
    from .bootstrap import build_postgres
    from .db.session import get_session_factory

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    platform = build_postgres()
    sessions = get_session_factory()
    print(f"worker {WORKER} ready", flush=True)

    idle = 0
    while not _stop:
        job = _claim(sessions)
        if job is None:
            if once:
                print(f"queue empty after {idle} idle polls", flush=True)
                return 0
            idle += 1
            time.sleep(POLL_SECONDS)
            continue

        idle = 0
        label = f"{job['kind']}"
        started = time.perf_counter()
        try:
            progress = _run_job(platform, sessions, job)
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  ok    {label:24} {elapsed:7.0f} ms  "
                  f"{progress.get('summary') or json.dumps(progress)[:110]}", flush=True)
            _finish(sessions, job["id"], progress=progress, error=None,
                    attempts=job["attempts"], max_attempts=job["max_attempts"])
        except Exception as exc:  # noqa: BLE001
            elapsed = (time.perf_counter() - started) * 1000
            print(f"  FAIL  {label:24} {elapsed:7.0f} ms  {exc}", flush=True)
            _finish(sessions, job["id"], progress={},
                    error="".join(traceback.format_exception_only(exc)).strip(),
                    attempts=job["attempts"], max_attempts=job["max_attempts"])

    return 0


if __name__ == "__main__":
    raise SystemExit(run(once="--once" in sys.argv))
