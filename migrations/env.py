"""Alembic environment.

Two things here are load-bearing and easy to get wrong:

  1. **The direct connection.** Supabase exposes 5432 (direct) and 6543
     (pgbouncer, transaction mode). DDL, advisory locks and `CREATE EXTENSION`
     do not survive a transaction pooler, so migrations use
     `ASKCONTENT_MIGRATION_DATABASE_URL` when it is set.

  2. **Schema awareness.** Our tables live in a configurable schema pinned
     through the ORM metadata (ARC-TEC-05), so the version table must live
     there too — otherwise `alembic_version` lands in `public` and two
     deployments sharing a database silently fight over it.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from askcontent.config import settings
from askcontent.db.models import Base  # noqa: F401 — registers every model
from askcontent.db.schema import ensure_schema_sql, pin_schema

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

#: Alembic's bookkeeping table, prefixed like everything else we own.
#:
#: `alembic_version` is the default name for *every* project that uses Alembic,
#: so on a shared database it is the one table guaranteed to collide — and the
#: collision is silent and catastrophic: two services reading each other's
#: revision pointer, each concluding the other's migrations are theirs. askdb
#: uses `askdb_alembic_version` on this same database for exactly this reason.
VERSION_TABLE = "askcontent_alembic_version"

config.set_main_option("sqlalchemy.url", settings.migrations_url)
target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    # The control plane lives in a different database with its own history.
    return getattr(obj, "schema", None) != "askcontent_control"


def run_migrations_offline() -> None:
    context.configure(
        url=settings.migrations_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        version_table=VERSION_TABLE,
        version_table_schema=settings.db_schema,
        include_object=_include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # The same listener the application uses, so a migration connection
    # resolves `VECTOR(...)` out of Supabase's `extensions` schema and cannot
    # be silently unpinned by the pooler.
    pin_schema(connectable)

    with connectable.connect() as connection:
        # The schema must exist before Alembic tries to place its version table
        # in it, which happens before the first revision runs.
        create_schema = ensure_schema_sql()
        if create_schema:
            connection.execute(text(create_schema))
            connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table=VERSION_TABLE,
            version_table_schema=settings.db_schema,
            include_object=_include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
