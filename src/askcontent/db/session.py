"""Engine, session factory and the tenant binding.

The tenant binding is the single load-bearing property of the tenancy model,
which is why this module is small, obvious, and separately tested (PLT-TEN-07).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings

_engine = create_engine(
    settings.database_url,
    pool_size=settings.pool_size,
    max_overflow=settings.pool_max_overflow,
    pool_pre_ping=True,
    connect_args={
        # A statement that runs away must be killed by the database, not by a
        # request timeout that leaves the query running.
        "options": f"-c statement_timeout={settings.statement_timeout_ms}",
    },
    future=True,
)

SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


@contextlib.contextmanager
def tenant_session(org_id: uuid.UUID | str) -> Iterator[Session]:
    """A session bound to one organisation for the life of one transaction.

    ARC-TEC-06 — the setting is **transaction-local**: the third argument to
    `set_config` is `true`. Behind a transaction-mode pooler a session-scoped
    setting belongs to whichever request last used that backend, so a
    session-scoped tenant id is a cross-tenant read waiting to happen. It will
    also pass every test that uses a single connection.

    Cross-tenant leakage is therefore not "filtered out" — it would require a
    defect in tenant resolution *and* a failure of the row-level security
    policy created in revision 0001.
    """
    session = SessionFactory()
    try:
        session.execute(
            text("SELECT set_config('askcontent.org_id', :org, true)"),
            {"org": str(org_id)},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextlib.contextmanager
def unscoped_session() -> Iterator[Session]:
    """For authentication, which happens *before* tenant scoping.

    The only tables legitimately reachable here are `auth_session` and
    `app_user`. Everything else is protected by a policy that will return zero
    rows, which is the intended outcome rather than an inconvenience.
    """
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def healthcheck() -> dict[str, object]:
    with _engine.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()
        has_vector = connection.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
        revision = connection.execute(
            text(f'SELECT version_num FROM "{settings.db_schema}".alembic_version')
        ).scalar()
    return {
        "server": version.split(" on ")[0],
        "schema": settings.db_schema,
        "pgvector": bool(has_vector),
        "revision": revision,
    }
