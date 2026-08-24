"""A connector may name one live source.

Revision ID: 0019
Revises: 0018
Created: 2026-08-23

Design 09, step 3. One column holding one `ContextSource`, not a table holding
many: a connector with three sources spends its life deciding which to call,
and that routing problem is not one anybody asked for. When a second source is
genuinely needed the shape to add is a table, and the migration that adds it
will be able to see from this column exactly what one source looked like in
practice.

Null means the connector has none, which is every connector until somebody
configures one — so the feature is off by construction rather than by a flag
somebody has to remember to leave alone.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN context_source jsonb")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN context_source")
