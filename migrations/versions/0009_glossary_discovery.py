"""Discovered glossary terms.

Revision ID: 0009
Revises: 0008
Created: 2026-08-22

A glossary you must hand-write is a glossary nobody writes, so terms are now
proposed from the corpus. Proposals are stored beside confirmed terms rather
than in a separate table: they are the same thing at different stages of review,
and splitting them would mean two queries and two screens to answer "what does
this word mean here".

`status` is what keeps a proposal out of retrieval until a human has looked at
it — the platform proposes, a person decides.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.glossary_term
            ADD COLUMN status varchar(16) NOT NULL DEFAULT 'confirmed',
            ADD COLUMN method varchar(16) NOT NULL DEFAULT 'human',
            ADD COLUMN confidence double precision,
            ADD COLUMN occurrences integer NOT NULL DEFAULT 0,
            ADD COLUMN documents integer NOT NULL DEFAULT 0,
            ADD COLUMN evidence text[] NOT NULL DEFAULT '{{}}',
            ADD COLUMN reviewed_by varchar(320),
            ADD COLUMN reviewed_at timestamptz
    """)
    # Terms entered by a person before this revision were, by definition,
    # already confirmed.
    op.execute(f"UPDATE {S}.glossary_term SET status = 'confirmed' WHERE source = 'human'")
    op.execute(f"CREATE INDEX ix_glossary_status ON {S}.glossary_term (connector_id, status)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_glossary_status")
    op.execute(f"""
        ALTER TABLE {S}.glossary_term
            DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS method,
            DROP COLUMN IF EXISTS confidence, DROP COLUMN IF EXISTS occurrences,
            DROP COLUMN IF EXISTS documents, DROP COLUMN IF EXISTS evidence,
            DROP COLUMN IF EXISTS reviewed_by, DROP COLUMN IF EXISTS reviewed_at
    """)
