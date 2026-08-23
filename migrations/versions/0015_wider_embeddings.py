"""Room for a real embedding model.

Revision ID: 0015
Revises: 0014
Created: 2026-08-23

The stub index stored `vector(384)`, the width of the hashed n-gram bag it was
built against. A real model is 1536, and a hashed bag cannot answer a question
whose wording differs from the document's — which is every broad question
anybody asks.

The existing vectors are discarded rather than migrated. Vectors from two
models are not comparable: cosine distance between them is a number with no
meaning, and the failure is silent — retrieval keeps working and returns
nonsense. Emptying the column forces one honest re-index instead.
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The ANN index is built on the old width and cannot be cast in place.
    op.execute("DROP INDEX IF EXISTS ecm_stub.ix_pgp_embedding")
    op.execute("ALTER TABLE ecm_stub.pgp_index_entry DROP COLUMN embedding")
    op.execute(
        "ALTER TABLE ecm_stub.pgp_index_entry "
        "ADD COLUMN embedding extensions.vector(1536)"
    )
    op.execute(
        "CREATE INDEX ix_pgp_embedding ON ecm_stub.pgp_index_entry "
        "USING hnsw (embedding extensions.vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ecm_stub.ix_pgp_embedding")
    op.execute("ALTER TABLE ecm_stub.pgp_index_entry DROP COLUMN embedding")
    op.execute(
        "ALTER TABLE ecm_stub.pgp_index_entry "
        "ADD COLUMN embedding extensions.vector(384)"
    )
