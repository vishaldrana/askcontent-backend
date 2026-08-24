"""Which models a deployment may answer with, and which one a connector uses.

Revision ID: 0020
Revises: 0019
Created: 2026-08-24

The model was an environment variable, so choosing one meant a deploy and
every connector got the same one. Neither is right: a public help centre and a
policy library are different jobs with different budgets, and the person who
should choose is the one reading the answers.

Two pieces. `model_catalog` is what this deployment supports — vendor, model
id, and the name a person sees, because `gpt-4.1-2025-04-14` is an identifier
and "GPT-4.1" is a choice. `connector.answer_model` is which one this
connector uses; null means the deployment default, so an empty column keeps
today's behaviour exactly.

`answer_detail` rides along because it is the same kind of setting and the
same screen: how much of what the passages support an answer should say.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {S}.model_catalog (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL,
            kind text NOT NULL DEFAULT 'answer',
            vendor text NOT NULL,
            model_id text NOT NULL,
            name text NOT NULL,
            note text NOT NULL DEFAULT '',
            enabled boolean NOT NULL DEFAULT true,
            is_default boolean NOT NULL DEFAULT false,
            ordinal integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, kind, vendor, model_id)
        )
    """)
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN answer_model text")
    op.execute(
        f"ALTER TABLE {S}.connector ADD COLUMN answer_detail text NOT NULL DEFAULT 'full'"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN answer_detail")
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN answer_model")
    op.execute(f"DROP TABLE {S}.model_catalog")
