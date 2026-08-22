"""One shared helper for the vector distance expression.

PLT-VEC-08 — the similarity index and the query must use the same distance
expression and the same width cast. This module is the single source of truth
for both; the migration that builds the index quotes it, and the query builder
calls it.

Trap, restated because it costs a day to find: the HNSW index type caps vector
width below the widest embedding models, so the index is built on a narrowed
cast. A query that omits the cast does not error — it silently falls back to a
sequential scan. The symptom is "search feels slow", not a failure.
"""

from __future__ import annotations

from sqlalchemy import Float, cast, literal_column
from sqlalchemy.sql.elements import ColumnElement

# The width the index is built on. Must match migrations/versions/0002.
INDEX_WIDTH = 2000


def narrowed(column: ColumnElement) -> ColumnElement:
    """The cast the index is built on. Use it on *both* sides, always."""
    return literal_column(f"({column.name}::vector({INDEX_WIDTH}))")


def cosine_distance(column: ColumnElement, query_vector: list[float]) -> ColumnElement:
    """Cosine distance, matching the index expression exactly."""
    return cast(narrowed(column).op("<=>")(query_vector), Float)
