"""A chunk keeps the overlap it was embedded with.

Revision ID: 0018
Revises: 0017
Created: 2026-08-23

`Chunk.embed_text` is the heading path, then the overlap carried from the
previous chunk in the same section, then the chunk's own text. Two of those
three were persisted. The overlap was not, because nothing read it back —
until re-embedding from the database became a thing anyone would want to do,
which is the moment the omission turns into "the vectors you rebuild are not
the vectors you had".

It cannot be recomputed from the rows either, which is the part worth
recording: overlap is the tail of the *preceding* chunk, and `_merge_runts`
runs afterwards and joins chunks while keeping the first one's overlap. After
merging, chunk N's overlap is no longer the tail of the row before it.

Existing rows get an empty overlap. That is honest rather than convenient —
their vectors were built with one, and a re-embed will produce vectors that
differ by up to a forty-token prefix. Consistent with each other and with the
query embedding, so retrieval works; identical to the originals, no. Anything
indexed after this migration re-embeds exactly.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {S}.document_chunk "
        f"ADD COLUMN overlap text NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.document_chunk DROP COLUMN overlap")
