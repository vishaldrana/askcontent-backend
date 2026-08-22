"""Alembic environment for the **control plane**.

A separate database with a separate revision history, because PLT-TEN-01/02 say
the control plane holds only tenant routing and global identity, and must never
hold catalog data, threads, or anything derived from a source.

Keeping it in its own tree is not tidiness. A single tree means one
`alembic upgrade head` applies control-plane tables to every tenant database it
touches, and the separation exists precisely so that cannot happen.

Points at `ASKCONTENT_CONTROL_PLANE_URL`. A single-team deployment runs with
multi-tenancy disabled (PLT-TEN-03) and never runs this at all.
"""

from __future__ import annotations

import pathlib
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from askcontent.config import settings
from askcontent.db.models import ControlBase  # noqa: F401 — registers models

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

VERSION_TABLE = "askcontent_alembic_version"
CONTROL_SCHEMA = "askcontent_control"

target_metadata = ControlBase.metadata


def _url() -> str:
    url = config.attributes.get("target_dsn") or settings.control_plane_url
    if not url:
        raise SystemExit(
            "ASKCONTENT_CONTROL_PLANE_URL is not set. The control plane is a "
            "separate database; a single-tenant deployment does not need it "
            "(PLT-TEN-03)."
        )
    return url


def run() -> None:
    engine = create_engine(_url(), future=True)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{CONTROL_SCHEMA}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table=VERSION_TABLE,
            version_table_schema=CONTROL_SCHEMA,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        version_table=VERSION_TABLE,
        version_table_schema=CONTROL_SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run()
