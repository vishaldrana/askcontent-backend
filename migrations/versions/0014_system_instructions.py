"""Per-connector answering instructions.

Revision ID: 0014
Revises: 0013
Created: 2026-08-23

A knowledgebase has a voice and a vocabulary. A support help centre wants
step-by-step answers that name the screen a button is on; a policy library
wants the clause quoted and the effective date stated. One prompt cannot serve
both, and forcing it to produces answers that are correct and unusable.

So the owner of a connector can add instructions. What they *cannot* do is
remove the grounding rules — cite every claim, use only the passages, refuse
rather than answer a near-miss. Those are the product, not a preference, and
the prompt is assembled with them last so nothing added here can override them.

`description` lands here too: the connector had a name and no room to say what
it is for, which is the first question anybody asks about a list of eight.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.connector
            ADD COLUMN description text NOT NULL DEFAULT '',
            ADD COLUMN system_instructions text NOT NULL DEFAULT ''
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE {S}.connector
            DROP COLUMN description,
            DROP COLUMN system_instructions
    """)
