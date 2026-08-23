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
from .schema import pin_schema

# Built lazily. Creating it at import time would mean every module in the
# application transitively required a working Postgres driver and a parseable
# DATABASE_URL just to be imported, which breaks unit tests and CLI tools.
_engine = None
_factory = None


def get_engine():
    """The platform engine.

    Note what is *absent*: a `connect_args={"options": ...}`. The pooler drops
    the `options` startup parameter silently, so search_path and
    statement_timeout are set by a connect listener instead — see db/schema.py.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.pool_size,
            max_overflow=settings.pool_max_overflow,
            pool_pre_ping=True,
            # Recycle below the pooler's idle cutoff. `pool_pre_ping` alone is
            # not enough: it only detects a connection the peer closed
            # *cleanly*. A pooler that vanishes mid-crawl leaves a half-open
            # socket, and the ping itself then blocks — which is exactly how a
            # long crawl died with "SSL SYSCALL error: Operation timed out"
            # after 65 seconds of a wedged read.
            pool_recycle=180,
            connect_args={
                # Fail fast instead of hanging on a dead peer. Without
                # keepalives the OS default is ~2 hours, so a broken
                # connection is indistinguishable from a slow query for the
                # rest of the job.
                "connect_timeout": 10,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
            future=True,
        )
        pin_schema(_engine)
    return _engine


def get_session_factory():
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _factory


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
    session = get_session_factory()()
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
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def healthcheck() -> dict[str, object]:
    with get_engine().connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()
        has_vector = connection.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
        revision = connection.execute(
            text(
                f'SELECT version_num FROM "{settings.db_schema}".askcontent_alembic_version'
            )
        ).scalar()
    return {
        "server": version.split(" on ")[0],
        "schema": settings.db_schema,
        "pgvector": bool(has_vector),
        "revision": revision,
    }
