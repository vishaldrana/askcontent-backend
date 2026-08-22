"""Control-plane schema.

Revision ID: 0001
Revises:
Created: 2026-08-22

Four tables and nothing else. PLT-TEN-02 — the control plane must never hold
customer data, catalog data, threads, or anything derived from a source. If a
future revision adds a table here that is not routing or identity, that is the
requirement being broken, not extended.
"""

from __future__ import annotations

import pathlib

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = pathlib.Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS "askcontent_control"')
    op.execute(SQL.joinpath("0001_schema.sql").read_text())


def downgrade() -> None:
    op.execute('DROP SCHEMA IF EXISTS "askcontent_control" CASCADE')
