"""Uploaded content.

Revision ID: 0005
Revises: 0004
Created: 2026-08-22

Upload is the last resort, and the table records **why** each one was accepted
(CNT-COL-22). A growing pile of "not in PGP" is the evidence that a
knowledgebase belongs in PGP, and it is the argument that gets it there — but
only if the reason is captured at the moment of the decision.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {S}.upload (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            collection_id uuid REFERENCES {S}.collection(id) ON DELETE CASCADE,
            filename text NOT NULL,
            mime varchar(128) NOT NULL,
            size_bytes bigint NOT NULL,
            -- Two hashes, for the two things they skip: the file hash skips a
            -- re-parse, the text hash (which includes the parser version)
            -- skips a re-embed.
            file_hash varchar(64) NOT NULL,
            text_hash varchar(64),
            parser_id varchar(64),
            parser_version varchar(32),
            parse_path varchar(32),
            parse_quality jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            refusal_reason text,
            title text,
            blob bytea NOT NULL,
            -- not_in_index | restricted | unresolvable
            accepted_reason varchar(32),
            accepted_by varchar(320),
            duplicate_of varchar(300),
            status varchar(16) NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, file_hash)
        )
    """)
    op.execute(f"ALTER TABLE {S}.upload ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {S}.upload FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY upload_tenant_isolation ON {S}.upload "
        f"USING (org_id = {S}.current_org()) WITH CHECK (org_id = {S}.current_org())"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {S}.upload CASCADE")
