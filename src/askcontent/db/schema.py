"""Pinning our tables to one Postgres schema.

Why this is not just `?options=-csearch_path=...` on the DSN: connection
poolers routinely drop the `options` startup parameter — Supabase's Supavisor
does — and the failure is **silent**. You get a working connection whose
search_path is the server default, so every table is created in `public`
instead. On a shared database that means quietly scattering 27 tables into
someone else's schema.

This database is genuinely shared: `askdb` and its sample-data schemas live
here, alongside `intelligence`, `langgraph` and `memories`. There is no version
of "it landed in public" that is harmless.

Issuing `SET search_path` on each new connection cannot be dropped by a pooler
in session mode, and `pool_pre_ping` plus this listener means a recycled
connection is re-pinned before it is handed out.

The `extensions` entry is not optional here either: Supabase installs pgvector
into `extensions`, so `VECTOR(...)` in a CREATE TABLE only resolves when that
schema is on the path.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine

from ..config import settings

#: Schemas appended after ours. `extensions` is where Supabase installs
#: pgvector; on a plain Postgres it simply does not exist and is ignored.
_TRAILING = ("extensions", "public")


def search_path() -> str | None:
    """The search_path to pin, or None to accept the server default."""
    schema = (settings.db_schema or "").strip()
    if not schema:
        return None
    parts = [schema, *(s for s in _TRAILING if s != schema)]
    return ", ".join(parts)


def pin_schema(engine: Engine) -> None:
    """Register a connect listener that sets search_path on every connection.

    Idempotent: calling it twice on the same engine registers one listener.
    """
    path = search_path()
    if path is None:
        return
    if getattr(engine, "_askcontent_schema_pinned", False):
        return
    engine._askcontent_schema_pinned = True  # noqa: SLF001

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, _record) -> None:
        # Runs on the raw DBAPI connection, before SQLAlchemy issues anything,
        # so even the first statement on the connection is correctly scoped.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET search_path TO {path}")
            # statement_timeout belongs here for the same reason search_path
            # does: passed as an `options` connect argument it is silently
            # dropped by the pooler, and a runaway query then has no ceiling.
            cursor.execute(f"SET statement_timeout = {settings.statement_timeout_ms}")
        finally:
            cursor.close()


def ensure_schema_sql() -> str | None:
    """DDL to create the schema, for migrations to run before anything else."""
    schema = (settings.db_schema or "").strip()
    return f'CREATE SCHEMA IF NOT EXISTS "{schema}"' if schema else None
