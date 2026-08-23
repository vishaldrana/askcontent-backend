"""Semantic fingerprints beside the byte hash.

Revision ID: 0011
Revises: 0010
Created: 2026-08-22

`file_hash` answers "are these the same bytes", which is not the question worth
asking. A re-save, a reflowed paragraph, a wiki that smartens quotes on save —
each changes every byte and none changes what the document says. Detecting those
as changes re-parses and re-embeds a corpus for nothing, and it makes the word
"changed" on a review screen mean nothing.

Three hashes, three questions:

    file_hash        same bytes?            → skip the parse
    content_hash     same words?            → skip the re-embed
    structure_hash   same layout?           → tell reordered from rewritten

`content_hash` is computed from the *parsed, normalised* text, so it is also
stable across a change of source format: the same policy exported to HTML and
to PDF fingerprints identically.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.document
            ADD COLUMN content_hash varchar(64),
            ADD COLUMN structure_hash varchar(64),
            ADD COLUMN last_content_change_at timestamptz,
            ADD COLUMN last_cosmetic_change_at timestamptz
    """)
    op.execute(f"""
        ALTER TABLE {S}.collection_member
            ADD COLUMN structure_hash varchar(64),
            ADD COLUMN last_verdict varchar(16),
            ADD COLUMN last_similarity double precision
    """)
    # `collection_member.content_hash` already exists from 0008 but held the
    # *byte* hash. Clearing it forces one honest recomputation rather than
    # comparing a byte hash against a content hash and calling everything
    # changed exactly once.
    op.execute(f"UPDATE {S}.collection_member SET content_hash = NULL")


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.document
            DROP COLUMN IF EXISTS content_hash,
            DROP COLUMN IF EXISTS structure_hash,
            DROP COLUMN IF EXISTS last_content_change_at,
            DROP COLUMN IF EXISTS last_cosmetic_change_at
    """)
    op.execute(f"""
        ALTER TABLE {S}.collection_member
            DROP COLUMN IF EXISTS structure_hash,
            DROP COLUMN IF EXISTS last_verdict,
            DROP COLUMN IF EXISTS last_similarity
    """)
