"""How the assistant should sound, in the words of whoever runs it.

Revision ID: 0021
Revises: 0020
Created: 2026-08-24

`answer_detail` was three fixed levels, and the closed grammar was wrong here
in a way it is right almost everywhere else in this schema. A scope or an
expectation kind is closed because the *system* has to reason about the value.
Nothing reasons about this one — it is passed to a model as English — so the
three words were not a grammar, they were three opinions about voice, chosen
by whoever wrote the enum rather than by the person whose product it is.

A help centre wants "answer like a colleague explaining it at a desk". A
policy library wants "quote the clause and state its date". Neither is brief,
standard or full.

So it is free text with presets to start from, and the presets are the three
levels plus the tones people actually asked for. Existing values are carried
across, so nothing changes voice on deploy.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

S = settings.db_schema

#: The old levels, in the words they were shorthand for.
_CARRY = {
    "brief": (
        "Answer in as few sentences as the question needs — usually one or two. "
        "Give the direct answer and the single most important condition on it, "
        "and stop."
    ),
    "standard": (
        "Give the answer and what a reader needs to act on it: the steps, the "
        "preconditions and the limits the passages state."
    ),
}


def upgrade() -> None:
    op.execute(f"ALTER TABLE {S}.connector ADD COLUMN answer_tone text NOT NULL DEFAULT ''")
    for level, words in _CARRY.items():
        op.execute(
            f"UPDATE {S}.connector SET answer_tone = '{words}' WHERE answer_detail = '{level}'"
        )
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN answer_detail")


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {S}.connector ADD COLUMN answer_detail text NOT NULL DEFAULT 'full'"
    )
    op.execute(f"ALTER TABLE {S}.connector DROP COLUMN answer_tone")
