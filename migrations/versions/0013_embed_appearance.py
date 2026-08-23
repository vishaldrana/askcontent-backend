"""What an embed looks like, and how much it is used.

Revision ID: 0013
Revises: 0012
Created: 2026-08-23

`appearance` holds the handful of presentational values the widget accepts —
title, placeholder, position, size, theme. They live on the embed rather than
in the snippet because the snippet is pasted once, by somebody who does not
work here, into a page nobody on this team can edit. Anything configurable that
lives in the snippet is effectively frozen the moment it ships.

`session_count` and `last_used_at` answer the only question anybody asks about
an embed nobody remembers creating: is this still in use? Without it the honest
answer is "delete it and find out".
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.embed
            ADD COLUMN appearance jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN session_count integer NOT NULL DEFAULT 0,
            ADD COLUMN last_used_at timestamptz
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.embed
            DROP COLUMN appearance,
            DROP COLUMN session_count,
            DROP COLUMN last_used_at
    """)
