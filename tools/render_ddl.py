"""Render CREATE statements from the ORM metadata.

Used to author revision 0001. Committed so the revision is reproducible and a
reviewer can diff regenerated output against what is in the migration, rather
than taking a wall of SQL on trust.

    PYTHONPATH=src python tools/render_ddl.py > /tmp/schema.sql
"""

from __future__ import annotations

import sys

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

sys.path.insert(0, "src")

from askcontent.db.models import Base, ControlBase  # noqa: E402

DIALECT = postgresql.dialect()


def render(metadata, label: str) -> None:
    print(f"-- ===== {label} =====")
    for table in metadata.sorted_tables:
        statement = str(CreateTable(table).compile(dialect=DIALECT)).strip()
        print(statement.replace("\n\n", "\n") + ";")
        for index in table.indexes:
            print(str(CreateIndex(index).compile(dialect=DIALECT)).strip() + ";")
        print()


if __name__ == "__main__":
    render(Base.metadata, "TENANT PLANE")
    render(ControlBase.metadata, "CONTROL PLANE")
