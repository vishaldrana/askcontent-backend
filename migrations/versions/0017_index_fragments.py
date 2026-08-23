"""The index keeps its own fragment text.

Revision ID: 0017
Revises: 0016
Created: 2026-08-23

`passage_hint` has been on the port since the beginning and has never been
populated, because the stub index had nowhere to hold text. That was tolerable
while the index only returned ids — and stopped being tolerable the moment it
was asked to rerank, because a cross-encoder given only titles is ranking
"Zapier" against "Introduction" with no idea what either page says.

A real fragment index holds fragment text; that is what makes it a fragment
index rather than a list of document ids. So the stub holds it too.

It is emphatically **not** the document. It is the index's own extract, it may
lag the store, and it is never cited — the passage a reader sees is still
recovered from the system of record. Its two uses are seeding passage
selection and giving an index-side reranker something to read.
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ecm_stub.pgp_index_entry ADD COLUMN fragment text")


def downgrade() -> None:
    op.execute("ALTER TABLE ecm_stub.pgp_index_entry DROP COLUMN fragment")
