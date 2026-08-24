"""Prefix every application table with `askcontent_`.

Revision ID: 0025
Revises: 0024
Created: 2026-08-24

The schema already scopes us, but the database is genuinely shared — askdb,
`intelligence`, `langgraph` and others live beside us — and plenty of tooling
(dashboards, backups, pg_stat views, log lines) shows a bare table name with
no schema. `job` or `document` in a shared project's slow-query log answers
nothing; `askcontent_job` answers everything. So the ownership moves into the
name itself: every application table becomes `askcontent_<name>`.

Constraints and indexes are renamed to match, because their names embed the
table name — the ORM's naming convention builds `pk_<table>`, `fk_<table>_…`,
`ix_<table>_…`, and the hand-written migrations left Postgres defaults like
`<table>_pkey`. A fresh database migrated 0001→0025 and the regenerated DDL
must produce the same object names, so the rename is a *rule applied at
runtime*, not a list captured from one database's catalog:

  * `pk_/fk_/uq_/ck_/ix_<rest>`  →  same prefix + `askcontent_<rest>`
  * `<old_table>_<rest>` (Postgres default names)  →  `askcontent_` + name

Renaming a constraint renames its backing index with it, so indexes that back
a constraint are skipped in the index pass — renaming them twice is an error.

Row-level security policies keep their names: they are attached by OID and
their USING clauses follow the rename automatically. The sample-data schema
`ecm_stub` is someone else's stand-in system of record and is not touched.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from askcontent.config import settings

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

S = settings.db_schema

#: Every application table this plane owns, ORM-mapped or migration-created.
TABLES = (
    # identity and tenancy
    "org", "app_user", "membership", "workspace", "auth_session",
    # sources
    "knowledgebase", "connector", "field_rule",
    # the catalog
    "document", "document_chunk", "document_pin", "authority_rule",
    # vectors
    "embedding",
    # plans and terms
    "retrieval_plan", "glossary_term",
    # conversations
    "thread", "message", "chat_thread", "chat_turn",
    # audit
    "retrieval_run", "scope_change",
    # rbac
    "rbac_role", "rbac_role_member", "rbac_label_rule", "rbac_policy_version",
    # operations
    "job", "quarantine_item", "embed", "embed_session",
    # collections and uploads (0004, 0005)
    "collection", "collection_rule", "collection_member", "url_paste", "upload",
    # feedback and evals (0016), model catalog (0020)
    "answer_feedback", "eval_case", "eval_run", "eval_result", "model_catalog",
)

_PREFIXES = ("pk_", "fk_", "uq_", "ck_", "ix_")


def _renamed(name: str, old_table: str) -> str | None:
    """The prefixed form of an object name, or None to leave it alone."""
    if name.startswith(("askcontent_",) + tuple(f"{p}askcontent_" for p in _PREFIXES)):
        return None  # already prefixed — reruns and partial states stay safe
    for p in _PREFIXES:
        if name.startswith(p):
            return f"{p}askcontent_{name[len(p):]}"
    if name.startswith(f"{old_table}_"):
        return f"askcontent_{name}"
    return None


def _restored(name: str, old_table: str) -> str | None:
    for p in _PREFIXES:
        if name.startswith(f"{p}askcontent_"):
            return p + name[len(p) + len("askcontent_"):]
    if name.startswith(f"askcontent_{old_table}_"):
        return name[len("askcontent_"):]
    return None


def _rename_members(conn, table: str, old_table: str, transform) -> None:
    """Rename the constraints and free-standing indexes of one table."""
    constraints = conn.execute(
        text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
            "WHERE nsp.nspname = :schema AND rel.relname = :table"
        ),
        {"schema": S, "table": table},
    ).scalars().all()
    for name in constraints:
        new = transform(name, old_table)
        if new:
            op.execute(f'ALTER TABLE {S}.{table} RENAME CONSTRAINT "{name}" TO "{new}"')

    # Indexes backing a constraint were just renamed with it — only the rest.
    indexes = conn.execute(
        text(
            "SELECT ic.relname FROM pg_index i "
            "JOIN pg_class ic ON ic.oid = i.indexrelid "
            "JOIN pg_class rel ON rel.oid = i.indrelid "
            "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
            "WHERE nsp.nspname = :schema AND rel.relname = :table "
            "AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid)"
        ),
        {"schema": S, "table": table},
    ).scalars().all()
    for name in indexes:
        new = transform(name, old_table)
        if new:
            op.execute(f'ALTER INDEX {S}."{name}" RENAME TO "{new}"')


def upgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        op.execute(f"ALTER TABLE {S}.{table} RENAME TO askcontent_{table}")
        _rename_members(conn, f"askcontent_{table}", table, _renamed)


def downgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        _rename_members(conn, f"askcontent_{table}", table, _restored)
        op.execute(f"ALTER TABLE {S}.askcontent_{table} RENAME TO {table}")
