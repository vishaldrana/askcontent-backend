"""Deep research, per connector.

Revision ID: 0023
Revises: 0022
Created: 2026-08-24

Off by default and off by construction: a null column is a connector with no
research, so nothing changes until somebody turns it on. It costs minutes and
several model calls per run, which is not a thing to enable for everybody
because one person wanted it once.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN research jsonb")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN research")
