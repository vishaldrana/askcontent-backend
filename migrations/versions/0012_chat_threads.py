"""Conversations, kept.

Revision ID: 0012
Revises: 0011
Created: 2026-08-22

A chat that forgets is a search box with a slower interface. The value of a
conversation is the second question — "and in Texas?", "what about the
enterprise plan?" — and that only works if the first one is still there.

Two tables, matching askdb's shape so the two products are one thing to learn:

    chat_thread   a conversation, titled by its first question
    chat_turn     one question and the answer it received

Why turns rather than askdb's `message` rows: an answer here is inseparable
from the evidence that supports it. Storing the prose without the citations
would leave a transcript whose claims cannot be checked tomorrow — which is
the failure this whole product is built against. So a turn holds both, and
`evidence` keeps the citations exactly as they were served, not as they would
be recomputed today against a corpus that has since changed.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {S}.chat_thread (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            connector_id uuid REFERENCES {S}.connector(id) ON DELETE SET NULL,
            -- The first question asked, which is what people recognise a
            -- conversation by. Nullable because a thread exists before it.
            title varchar(300),
            -- Which role the conversation is being held as. On the thread
            -- rather than the turn: a transcript where the asker changed
            -- halfway through cannot be read.
            role varchar(120),
            created_by varchar(200),
            archived_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"""
        CREATE INDEX ix_chat_thread_recent
            ON {S}.chat_thread (org_id, updated_at DESC)
         WHERE archived_at IS NULL
    """)

    op.execute(f"""
        CREATE TABLE {S}.chat_turn (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            thread_id uuid NOT NULL
                REFERENCES {S}.chat_thread(id) ON DELETE CASCADE,
            ordinal integer NOT NULL,
            question text NOT NULL,
            answer text NOT NULL DEFAULT '',
            -- The citations as they were served. Deliberately a snapshot: a
            -- transcript that re-resolves its evidence against today's corpus
            -- is a transcript that quietly rewrites what was said.
            evidence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            steps jsonb NOT NULL DEFAULT '[]'::jsonb,
            grounded boolean NOT NULL DEFAULT false,
            unsupported_reason text,
            answered_by jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            elapsed_ms integer,
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (thread_id, ordinal)
        )
    """)

    for table in ("chat_thread", "chat_turn"):
        op.execute(f"ALTER TABLE {S}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {S}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {S}.{table}
                USING (org_id = {S}.current_org())
                WITH CHECK (org_id = {S}.current_org())
        """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {S}.chat_turn")
    op.execute(f"DROP TABLE IF EXISTS {S}.chat_thread")
