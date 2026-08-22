"""Richer membership, and a job queue the workers can actually claim.

Revision ID: 0008
Revises: 0007
Created: 2026-08-22

Two changes, both about being able to see what happened.

**Membership detail.** A collection's members carried an identifier and a title.
Reviewing a knowledgebase needs more: what the page is about, when it was
created, when it last changed, and — because many sources supply none of that —
**where each date came from**. A date read out of prose is weaker evidence than
one the system of record supplied, and a reviewer deciding whether a policy is
current has to be able to tell them apart.

**Job claiming.** The existing `job` table had a status but no way for a worker
to take exclusive ownership of a row. Two workers would run the same job.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.collection_member
            ADD COLUMN description text,
            ADD COLUMN space varchar(200),
            ADD COLUMN path text,
            ADD COLUMN doc_type varchar(32),
            ADD COLUMN owner varchar(320),
            ADD COLUMN source_created_at timestamptz,
            ADD COLUMN source_updated_at timestamptz,
            -- metadata | content | none. Without this a recovered date is
            -- indistinguishable from an authoritative one.
            ADD COLUMN created_source varchar(16) NOT NULL DEFAULT 'none',
            ADD COLUMN updated_source varchar(16) NOT NULL DEFAULT 'none',
            ADD COLUMN date_evidence text,
            ADD COLUMN content_hash varchar(64),
            ADD COLUMN last_checked_at timestamptz,
            ADD COLUMN last_changed_at timestamptz
    """)
    op.execute(
        f"CREATE INDEX ix_member_checked ON {S}.collection_member "
        f"(collection_id, last_checked_at NULLS FIRST)"
    )

    op.execute(f"""
        ALTER TABLE {S}.job
            ADD COLUMN payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN attempts integer NOT NULL DEFAULT 0,
            ADD COLUMN max_attempts integer NOT NULL DEFAULT 3,
            ADD COLUMN locked_at timestamptz,
            ADD COLUMN locked_by varchar(120),
            ADD COLUMN run_after timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN collection_id uuid REFERENCES {S}.collection(id) ON DELETE CASCADE
    """)
    # Claiming uses SKIP LOCKED on this ordering, so the index is what keeps a
    # busy queue from turning into a table scan per poll.
    op.execute(
        f"CREATE INDEX ix_job_claimable ON {S}.job (status, run_after) "
        f"WHERE status IN ('queued', 'retry')"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_job_claimable")
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_member_checked")
    op.execute(f"""
        ALTER TABLE {S}.job
            DROP COLUMN IF EXISTS payload, DROP COLUMN IF EXISTS attempts,
            DROP COLUMN IF EXISTS max_attempts, DROP COLUMN IF EXISTS locked_at,
            DROP COLUMN IF EXISTS locked_by, DROP COLUMN IF EXISTS run_after,
            DROP COLUMN IF EXISTS collection_id
    """)
    op.execute(f"""
        ALTER TABLE {S}.collection_member
            DROP COLUMN IF EXISTS description, DROP COLUMN IF EXISTS space,
            DROP COLUMN IF EXISTS path, DROP COLUMN IF EXISTS doc_type,
            DROP COLUMN IF EXISTS owner,
            DROP COLUMN IF EXISTS source_created_at,
            DROP COLUMN IF EXISTS source_updated_at,
            DROP COLUMN IF EXISTS created_source,
            DROP COLUMN IF EXISTS updated_source,
            DROP COLUMN IF EXISTS date_evidence,
            DROP COLUMN IF EXISTS content_hash,
            DROP COLUMN IF EXISTS last_checked_at,
            DROP COLUMN IF EXISTS last_changed_at
    """)
