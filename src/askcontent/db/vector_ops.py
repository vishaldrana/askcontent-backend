"""One shared helper for the vector distance expression.

PLT-VEC-08 — the similarity index and the query must use the same distance
expression and the same width cast. This module is the single source of truth
for both: the migration that builds the index imports these values, and the
query builder calls `cosine_distance`.

Two traps live here, and the second one bit this build.

**Trap 1, the documented one.** HNSW caps vector width at 2000 dimensions. A
column wider than that must be indexed on a narrowed cast, and a query that
omits the cast does not error — it silently falls back to a sequential scan. The
symptom is "search feels slow", not a failure.

**Trap 2, the one that actually happened.** The cast must only be applied when
the column is *wider* than the cap. Casting a `vector(1536)` to `vector(2000)`
is not a widening no-op — pgvector rejects it with "expected 2000 dimensions,
not 1536". The index still *builds*, because an empty table has no row to
reject, so the error waits for the first insert. Revision 0002 shipped exactly
that bug and revision 0003 fixes it.

The lesson generalises: an index expression that is never evaluated against a
row has not been tested.
"""

from __future__ import annotations

from sqlalchemy.sql.elements import ColumnElement

#: The widest vector HNSW will index.
HNSW_MAX_DIMENSIONS = 2000

#: The stored column width. Must match models.VECTOR_WIDTH.
STORED_WIDTH = 1536

#: The width the index is built on: the stored width, unless that exceeds what
#: HNSW can take, in which case the index narrows.
INDEX_WIDTH = min(STORED_WIDTH, HNSW_MAX_DIMENSIONS)

#: True when a cast is actually required. Both the index DDL and every query
#: read this, so they cannot disagree.
NEEDS_CAST = STORED_WIDTH > HNSW_MAX_DIMENSIONS


def indexed_expression(column_sql: str = "vector") -> str:
    """The expression the index is built on, as SQL text."""
    return f"({column_sql}::vector({INDEX_WIDTH}))" if NEEDS_CAST else column_sql


def cosine_distance(column: ColumnElement, query_vector: list[float]) -> ColumnElement:
    """Cosine distance, matching the index expression exactly."""
    if NEEDS_CAST:
        from sqlalchemy import literal_column

        narrowed = literal_column(f"({column.name}::vector({INDEX_WIDTH}))")
        return narrowed.op("<=>")(query_vector)
    return column.op("<=>")(query_vector)
