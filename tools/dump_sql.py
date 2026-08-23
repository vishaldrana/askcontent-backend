"""Write the database out as two files: one of shape, one of content.

    PYTHONPATH=src python tools/dump_sql.py

The split is the point. `sql/schema.sql` is what the database *is* — tables,
indexes, constraints, row-level security. `sql/data.sql` is what is *in* it.
They change for different reasons and at different rates: schema changes come
from a migration and are reviewed line by line, while the data is regenerated
whenever the demo corpus is re-crawled. Held in one file, every re-crawl
produces a diff nobody can read, and the schema change hiding in it goes
through unexamined.

Restoring is the two files in order, then one command to rebuild the vectors:

    psql "$ASKCONTENT_DATABASE_URL" -f sql/schema.sql
    psql "$ASKCONTENT_DATABASE_URL" -f sql/data.sql
    python -m askcontent.cli reembed

The third line is not optional. Until it has run the vector channel returns
nothing and every answer comes from the lexical channel alone, which does not
fail — it just quietly gets worse, which is the harder thing to notice.

**What data.sql leaves out, and why it is safe to.**

*Embeddings.* 3,000 vectors of 1,536 floats is about 59 MB of text — a file
nobody will ever read, in a repository where every other file is meant to be
read. They are derived from the chunks, which are here, so the restore path is
`python -m askcontent.cli reembed` rather than a download.

*Operational history.* Jobs, retrieval runs, eval results, sessions. These
describe things that happened to a particular database on a particular
afternoon. Restoring them into a new one asserts a history that never
occurred, which is worse than starting with none.

*Conversations.* Chat threads and turns are somebody's questions. They are not
seed data, and a demo database that opens with a stranger's conversation in
the sidebar is a demo of the wrong thing.

Everything else is configuration and corpus: the connectors, what they are
scoped to, who may see them, the field maps, the glossary, the eval cases, the
embeds, and the documents with their chunks.
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import pathlib
import subprocess
import sys
import uuid

sys.path.insert(0, "src")

from sqlalchemy import create_engine, text  # noqa: E402

OUT = pathlib.Path("sql")

#: Ordered so a straight replay satisfies every foreign key. This is written
#: out by hand rather than derived from the metadata because the order is a
#: decision — `document` before `document_chunk` is a fact about the schema,
#: but leaving `embedding` out entirely is a judgement, and the two belong in
#: the same list where a reader can see both.
TABLES = [
    "org",
    "workspace",
    "knowledgebase",
    "connector",
    "field_rule",
    "authority_rule",
    "rbac_role",
    "rbac_role_member",
    "rbac_label_rule",
    "collection",
    "collection_rule",
    "collection_member",
    "document",
    "document_chunk",
    "glossary_term",
    "eval_case",
    "embed",
    "upload",
    "url_paste",
    "document_pin",
]

#: Present in the schema and deliberately not dumped. Named here so the next
#: person can see that the omission was decided rather than overlooked.
SKIPPED = {
    "embedding": "derived from document_chunk; ~59 MB of floats. Run: askcontent reembed",
    "job": "operational history of one database on one afternoon",
    "retrieval_run": "operational history",
    "retrieval_plan": "operational history",
    "eval_run": "operational history",
    "eval_result": "operational history",
    "answer_feedback": "operational history",
    "scope_change": "operational history",
    "auth_session": "credentials",
    "embed_session": "operational history",
    "app_user": "identities, which belong to a deployment and not to a fixture",
    "membership": "identities",
    "chat_thread": "somebody's questions; not seed data",
    "chat_turn": "somebody's questions; not seed data",
    "thread": "superseded by chat_thread",
    "message": "superseded by chat_turn",
    "quarantine_item": "operational history",
    "rbac_policy_version": "operational history",
    "askcontent_alembic_version": "written by alembic, never by a data load",
}


def literal(value: object, udt: str) -> str:
    """One value, as SQL, rendered by the column's own type.

    Rendered by declared type rather than by Python type, because the two do
    not line up. A `jsonb[]` column arrives as a list of dicts, and guessing
    from the list alone produces a Postgres array literal — which is how
    `eval_case.expectations` came out as `{"{'kind': 'answers'}"}` and the
    whole load rolled back on "invalid input syntax for type json". The
    database already knows what each column is; asking it is cheaper than
    being clever.
    """
    if value is None:
        return "NULL"

    # An array type is the element type with a leading underscore.
    if udt.startswith("_"):
        element = udt[1:]
        items = ", ".join(literal(v, element) for v in (value or ()))
        return f"ARRAY[{items}]::{element}[]"

    if udt in ("json", "jsonb"):
        import json

        return quote(json.dumps(value, default=str)) + f"::{udt}"

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time, uuid.UUID)):
        return quote(str(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(value).hex() + "'"
    if isinstance(value, (list, tuple, dict)):
        import json

        return quote(json.dumps(value, default=str))
    return quote(str(value))


def quote(text_value: str) -> str:
    return "'" + text_value.replace("'", "''") + "'"


def main() -> None:
    url = os.environ.get("ASKCONTENT_DATABASE_URL")
    if not url:
        raise SystemExit("ASKCONTENT_DATABASE_URL is not set")
    schema = os.environ.get("ASKCONTENT_DB_SCHEMA", "askcontent")
    OUT.mkdir(exist_ok=True)

    dump_schema(url, schema)
    dump_data(url, schema)


def dump_schema(url: str, schema: str) -> None:
    """Shape only, from the live database rather than from the ORM.

    The ORM renders what the models say; this renders what seventeen migrations
    actually produced. Where those two disagree the second one is the one that
    answers queries, and the disagreement is the thing worth being able to see.
    """
    target = OUT / "schema.sql"
    # psycopg's URL scheme is not libpq's.
    libpq = url.replace("postgresql+psycopg://", "postgresql://")
    result = subprocess.run(
        [
            "pg_dump",
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            f"--schema={schema}",
            libpq,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"pg_dump failed:\n{result.stderr.strip()}")

    header = (
        "-- The shape of an askcontent tenant database.\n"
        "--\n"
        "-- Generated by tools/dump_sql.py from a migrated database, not from\n"
        "-- the ORM: this is what the migrations actually produced, and where\n"
        "-- that differs from what the models say, this is the one answering\n"
        "-- queries.\n"
        "--\n"
        "-- Load first, then sql/data.sql.\n\n"
    )
    target.write_text(header + result.stdout)
    print(f"{target}  {len(result.stdout.splitlines())} lines")


def dump_data(url: str, schema: str) -> None:
    target = OUT / "data.sql"
    engine = create_engine(url)
    lines: list[str] = [
        "-- The contents of an askcontent tenant database.\n",
        "--\n",
        "-- Generated by tools/dump_sql.py. Load sql/schema.sql first.\n",
        "--\n",
        "-- Not included, on purpose:\n",
    ]
    for name, why in sorted(SKIPPED.items()):
        lines.append(f"--   {name:26} {why}\n")
    lines.append(
        "--\n"
        "-- Embeddings are the one omission that changes behaviour: without them\n"
        "-- the vector channel returns nothing and answers come from the lexical\n"
        "-- channel alone. Re-embed after loading.\n"
        "--\n"
        "-- Row-level security is on, and these inserts are written as one\n"
        "-- transaction with it disabled for the session, because a load that\n"
        "-- half-applies leaves a corpus with documents and no chunks.\n\n"
        "BEGIN;\n"
        "SET LOCAL session_replication_role = replica;\n\n"
    )

    with engine.connect() as connection:
        present = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s"
                ),
                {"s": schema},
            ).scalars()
        )
        total = 0
        for table in TABLES:
            if table not in present:
                lines.append(f"-- {table}: not in this database\n\n")
                continue
            rows = connection.execute(
                text(f'SELECT * FROM {schema}."{table}"')
            ).mappings().all()
            lines.append(f"-- {table} · {len(rows)} row{'' if len(rows) == 1 else 's'}\n")
            if not rows:
                lines.append("\n")
                continue
            types = {
                r[0]: r[1]
                for r in connection.execute(
                    text(
                        "SELECT column_name, udt_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": schema, "t": table},
                )
            }
            columns = list(rows[0].keys())
            column_list = ", ".join(f'"{c}"' for c in columns)
            for row in rows:
                values = ", ".join(literal(row[c], types.get(c, "text")) for c in columns)
                lines.append(
                    f'INSERT INTO {schema}."{table}" ({column_list}) VALUES ({values});\n'
                )
            lines.append("\n")
            total += len(rows)

    lines.append("COMMIT;\n")
    target.write_text("".join(lines))
    size = target.stat().st_size
    print(f"{target}  {total} rows  {size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
