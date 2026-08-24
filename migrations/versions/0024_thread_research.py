"""Deep research, per thread.

Revision ID: 0024
Revises: 0023
Created: 2026-08-24

The connector's setting is where a conversation starts; this is where it
actually lives. Depth is a judgement about the question in front of the
reader, and the reader is the one waiting for it — so the choice belongs
beside the question, and it has to survive a reload to be worth making.

Null means "whatever the connector says", which is every thread that existed
before this column did.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"ALTER TABLE {S}.chat_thread ADD COLUMN research jsonb")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.chat_thread DROP COLUMN research")
