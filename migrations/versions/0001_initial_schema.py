"""Initial schema, extensions and row-level security.

Revision ID: 0001
Revises:
Created: 2026-08-22

PLT-DM-18 — the initial revision creates the schema **and** the row-level
security policies together. Splitting them leaves a window in which the tables
exist and the policies do not, and that window is exactly when someone runs the
first data load.

The DDL is held in `migrations/sql/`, generated from the ORM metadata by
`tools/render_ddl.py` and `tools/render_rls.py`. Regenerate and diff rather than
hand-editing: a table added without a policy is the kind of omission that stays
invisible until it is a breach.
"""

from __future__ import annotations

import pathlib

from alembic import op

from askcontent.config import settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = pathlib.Path(__file__).resolve().parents[1] / "sql"


def _run(name: str) -> None:
    op.execute(SQL.joinpath(name).read_text())


def upgrade() -> None:
    # pgvector must exist before any table declaring a VECTOR column.
    # On Supabase this succeeds through the direct connection (5432) and fails
    # through the transaction pooler (6543) — see migrations/env.py.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"')

    _run("0001_schema.sql")
    _run("0001_rls.sql")

    # ARC-TEC-04 — the application role must not own the tables and must not
    # hold BYPASSRLS, or every policy created above is silently inert. This
    # grants the role its privileges without ownership; creating the role and
    # ensuring it is not a superuser is a deployment step, documented in the
    # README, because a migration cannot safely create roles on a managed
    # platform.
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'askcontent_app') THEN
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO askcontent_app', '{settings.db_schema}');
            EXECUTE format(
              'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO askcontent_app',
              '{settings.db_schema}');
            EXECUTE format(
              'ALTER DEFAULT PRIVILEGES IN SCHEMA %I '
              'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO askcontent_app',
              '{settings.db_schema}');
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(f'DROP SCHEMA IF EXISTS "{settings.db_schema}" CASCADE')
