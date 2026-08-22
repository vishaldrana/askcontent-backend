"""Mark code chunks.

Revision ID: 0010
Revises: 0009
Created: 2026-08-22

Glossary discovery scanned code blocks and proposed `POST` as a term of art. It
is an HTTP verb in a curl example — and a term list that offers it teaches the
reviewer to skim rather than read.

Filtering by a keyword list would have been a guess that needs extending for
every language. The chunker already knows which chunks are code; it simply had
nowhere to record it.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {S}.document_chunk "
        f"ADD COLUMN is_code boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.document_chunk DROP COLUMN IF EXISTS is_code")
