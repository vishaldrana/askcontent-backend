"""Rebuild the vector index without an impossible cast.

Revision ID: 0003
Revises: 0002
Created: 2026-08-22

Revision 0002 built the HNSW index on `(vector::vector(2000))`, reasoning that
HNSW caps width at 2000 dimensions. The stored column is `vector(1536)`, and
pgvector does not treat that cast as a widening no-op — it rejects it with
"expected 2000 dimensions, not 1536".

The index nonetheless **built successfully**, because an empty table has no row
to reject. The error was waiting for the first insert, which is the worst place
for it: the migration reports success, the schema looks right, and ingestion
fails later somewhere that looks unrelated.

The cast is only needed when the column is *wider* than the cap. That condition
now lives in `askcontent.db.vector_ops`, which both this migration and the query
builder read, so the two cannot disagree again.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings
from askcontent.db.vector_ops import indexed_expression  # noqa: E402

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SCHEMA = settings.db_schema


def upgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_embedding_vector_cosine")
    op.execute(
        f"""
        CREATE INDEX ix_embedding_vector_cosine
        ON {SCHEMA}.embedding
        USING hnsw ({indexed_expression()} vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_embedding_vector_cosine")
    op.execute(
        f"""
        CREATE INDEX ix_embedding_vector_cosine
        ON {SCHEMA}.embedding
        USING hnsw ((vector::vector(2000)) vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )
