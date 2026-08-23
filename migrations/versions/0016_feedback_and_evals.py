"""Feedback, and the eval suite it feeds.

Revision ID: 0016
Revises: 0015
Created: 2026-08-23

Two tables that only look separate.

`answer_feedback` records what a reader thought of an answer. On its own that
is a satisfaction metric, which is the least useful thing it could be: a number
that goes down tells you something is wrong and not what, and by the time it
moves the answer that caused it is weeks old.

`eval_case` is what makes it useful. A thumbs-down is a *question that was
answered badly*, which is the same thing as a test case nobody has written yet
— so the feedback row carries everything needed to promote it into one, and
the console makes that a single click. Feedback that cannot become a test is a
complaints box.

`eval_run` and `eval_result` keep the history, because the question worth
answering about a retrieval change is never "does it pass" but "what did it
break".

Expectations are a **closed set**, for the same reason the query grammar is:
a free-text expectation is one nobody can evaluate mechanically, and an eval
suite that needs a human to interpret its own results is a suite that stops
being run.
"""

from __future__ import annotations

from alembic import op

from askcontent.config import settings

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

S = settings.db_schema


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {S}.answer_feedback (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            connector_id uuid REFERENCES {S}.connector(id) ON DELETE SET NULL,
            thread_id uuid REFERENCES {S}.chat_thread(id) ON DELETE SET NULL,
            turn_id uuid REFERENCES {S}.chat_turn(id) ON DELETE SET NULL,

            -- The question and answer are copied, not referenced. A thread can
            -- be deleted and the lesson should survive it; and an answer that
            -- is re-resolved later against a changed corpus is no longer the
            -- answer somebody complained about.
            question text NOT NULL,
            answer text NOT NULL DEFAULT '',
            citations jsonb NOT NULL DEFAULT '[]'::jsonb,

            verdict varchar(16) NOT NULL,          -- helpful | unhelpful
            reason varchar(32),                    -- see REASONS in the API
            comment text,

            -- Set once the complaint has been turned into a test, so the
            -- review queue empties instead of growing forever.
            promoted_case_id uuid,
            actor varchar(200),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"""
        CREATE INDEX ix_feedback_open ON {S}.answer_feedback (connector_id, created_at DESC)
         WHERE verdict = 'unhelpful' AND promoted_case_id IS NULL
    """)

    op.execute(f"""
        CREATE TABLE {S}.eval_case (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            connector_id uuid NOT NULL REFERENCES {S}.connector(id) ON DELETE CASCADE,
            question text NOT NULL,
            -- One row per expectation would make a case that asserts two
            -- things into two cases that can disagree about whether they ran.
            expectations jsonb NOT NULL DEFAULT '[]'::jsonb,
            note text NOT NULL DEFAULT '',
            -- 'authored' or 'feedback'. Worth keeping: a suite grown entirely
            -- from complaints tests only what has already gone wrong.
            origin varchar(16) NOT NULL DEFAULT 'authored',
            enabled boolean NOT NULL DEFAULT true,
            role varchar(120),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute(f"""
        CREATE TABLE {S}.eval_run (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            connector_id uuid NOT NULL REFERENCES {S}.connector(id) ON DELETE CASCADE,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            total integer NOT NULL DEFAULT 0,
            passed integer NOT NULL DEFAULT 0,
            failed integer NOT NULL DEFAULT 0,
            -- What was in force when it ran. A pass rate with no record of the
            -- configuration behind it cannot be compared with another one.
            context jsonb NOT NULL DEFAULT '{{}}'::jsonb
        )
    """)

    op.execute(f"""
        CREATE TABLE {S}.eval_result (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES {S}.org(id) ON DELETE CASCADE,
            run_id uuid NOT NULL REFERENCES {S}.eval_run(id) ON DELETE CASCADE,
            case_id uuid REFERENCES {S}.eval_case(id) ON DELETE SET NULL,
            question text NOT NULL,
            passed boolean NOT NULL,
            failures jsonb NOT NULL DEFAULT '[]'::jsonb,
            answer text NOT NULL DEFAULT '',
            cited jsonb NOT NULL DEFAULT '[]'::jsonb,
            grounded boolean NOT NULL DEFAULT false,
            elapsed_ms integer,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"CREATE INDEX ix_eval_result_run ON {S}.eval_result (run_id)")

    for table in ("answer_feedback", "eval_case", "eval_run", "eval_result"):
        op.execute(f"ALTER TABLE {S}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {S}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {S}.{table}
                USING (org_id = {S}.current_org())
                WITH CHECK (org_id = {S}.current_org())
        """)


def downgrade() -> None:
    for table in ("eval_result", "eval_run", "eval_case", "answer_feedback"):
        op.execute(f"DROP TABLE IF EXISTS {S}.{table}")
