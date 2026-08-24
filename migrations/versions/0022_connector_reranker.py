"""Which reranker a connector uses, and with which model.

Revision ID: 0022
Revises: 0021
Created: 2026-08-24

Reranking is the one stage whose right implementation depends on where the
content came from, and a deployment routinely has both kinds at once.

Content the enterprise platform already indexes is reranked *by the platform* —
the fragment search takes a parameter and returns ranked fragments, and doing
it again locally is paying twice to be worse, because the platform's reranker
reads the fragment text and ours reads an extract of it.

Content we crawled and indexed ourselves has no such option: nobody else holds
those fragments. There, a local reranker is the only reranker, and on a hosted
deployment with no GPU that means an LLM reading question-and-passage pairs.

Both connectors live in one process, so the choice cannot be an environment
variable. `null` keeps the deployment default, so nothing changes until
somebody chooses.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN reranker text")
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN rerank_model text")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN rerank_model")
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN reranker")
