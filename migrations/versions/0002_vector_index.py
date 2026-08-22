"""The approximate-nearest-neighbour index.

Revision ID: 0002
Revises: 0001
Created: 2026-08-22

Separate from 0001 because building it is expensive on a populated table and a
deployment may want to load first and index after — and because this is where
the trap lives, and it deserves its own place to be read.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA = settings.db_schema


def upgrade() -> None:
    # PLT-VEC-08 — the index and the query must use the **same distance
    # expression** and the **same width cast**, produced by one shared helper.
    #
    # Trap: the common index type caps vector width below the widest embedding
    # models, so the index is built on a narrowed cast. If the query expression
    # and the index expression differ, the database silently falls back to a
    # sequential scan. It is not an error; it is a hundredfold latency
    # regression that presents as "search feels slow".
    #
    # The single source of truth for both is
    # `askcontent.db.vector_ops.distance_expression`. Change it there, not here.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_embedding_vector_cosine
        ON {SCHEMA}.embedding
        USING hnsw ((vector::vector(2000)) vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    # Lexical channel. Retrieval combines vector similarity with a match on the
    # raw title and path, so somebody who knows the exact document code can
    # still find it — the failure users notice is an exact identifier returning
    # thematically similar documents that do not contain it.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_document_fts
        ON {SCHEMA}.document
        USING gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(path, '')))
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_chunk_fts
        ON {SCHEMA}.document_chunk
        USING gin (to_tsvector('english', text))
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_chunk_fts")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_document_fts")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_embedding_vector_cosine")
