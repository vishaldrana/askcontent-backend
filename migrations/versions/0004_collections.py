"""Collections and their materialised membership.

Revision ID: 0004
Revises: 0003
Created: 2026-08-22

A collection is composed from source rules; membership is the enumerated result.
Rules propose, membership decides (CNT-COL-06) — so membership is a table, not a
saved query, because a corpus you cannot enumerate cannot be diffed, audited, or
explained when it fails to answer.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {S}.collection (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            slug varchar(64) NOT NULL,
            name varchar(300) NOT NULL,
            description text,
            business_group varchar(200),
            state varchar(16) NOT NULL DEFAULT 'draft',
            auto_accept_enumerable boolean NOT NULL DEFAULT true,
            materialised_at timestamptz,
            version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, slug)
        )
    """)
    op.execute(f"""
        CREATE TABLE {S}.collection_rule (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            collection_id uuid NOT NULL REFERENCES {S}.collection(id) ON DELETE CASCADE,
            ordinal integer NOT NULL DEFAULT 0,
            kind varchar(32) NOT NULL,
            effect varchar(8) NOT NULL DEFAULT 'include',
            config jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            -- An enumerable rule states a place; a proposing rule states a
            -- guess. Additions from the second are never auto-accepted
            -- (CNT-COL-10), so the distinction is stored, not inferred.
            enumerable boolean NOT NULL DEFAULT true,
            last_run_at timestamptz,
            last_candidate_count integer,
            capped boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"""
        CREATE TABLE {S}.collection_member (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            collection_id uuid NOT NULL REFERENCES {S}.collection(id) ON DELETE CASCADE,
            doc_id varchar(300) NOT NULL,
            kb_id varchar(200),
            title text,
            url text,
            -- All contributing rules, not one. Removing a rule must not
            -- silently drop a document another rule also claims (CNT-COL-08).
            contributed_by text[] NOT NULL DEFAULT '{{}}',
            resolved_via varchar(16),
            resolve_score double precision,
            state varchar(16) NOT NULL DEFAULT 'member',
            -- A human decision, which survives every future re-materialisation
            -- whatever the rules then say (CNT-COL-12).
            pinned varchar(8),
            pinned_by varchar(320),
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            missing_since timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (collection_id, doc_id)
        )
    """)
    op.execute(f"CREATE INDEX ix_member_state ON {S}.collection_member (collection_id, state)")
    op.execute(f"""
        CREATE TABLE {S}.url_paste (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            collection_id uuid REFERENCES {S}.collection(id) ON DELETE CASCADE,
            url text NOT NULL,
            normalised text NOT NULL,
            outcome varchar(16) NOT NULL,
            matched_doc_id varchar(300),
            rung varchar(16),
            score double precision,
            detail text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    # Connector gains an optional collection. A connector still works without
    # one (a whole knowledgebase), which keeps the tidy case a two-click path.
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN collection_id uuid REFERENCES {S}.collection(id)")

    for table in ("collection", "collection_rule", "collection_member", "url_paste"):
        op.execute(f"ALTER TABLE {S}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {S}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {S}.{table} "
            f"USING (org_id = {S}.current_org()) "
            f"WITH CHECK (org_id = {S}.current_org())"
        )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN IF EXISTS collection_id")
    for table in ("url_paste", "collection_member", "collection_rule", "collection"):
        op.execute(f"DROP TABLE IF EXISTS {S}.{table} CASCADE")
