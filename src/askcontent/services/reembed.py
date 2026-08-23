"""Rebuild vectors from the chunks already in the database.

`sql/data.sql` carries the corpus and deliberately not its embeddings — three
thousand vectors of 1,536 floats is 59 MB of text in a repository where every
other file is meant to be read. That trade is only honest if getting them back
is one command, because a corpus whose vector channel returns nothing is a
corpus that answers from BM25 alone and looks, to anyone using it, like a
worse product.

This is that command. It re-embeds from `document_chunk`, never from the
source: the point is to restore a loaded database without re-crawling anybody
else's site.

**Missing by default, everything on request.** The common case after a load is
that no chunk has a vector; the second most common is a model change, where
every chunk needs one whether it has one or not. Neither should be the same
flag, because the first is free to run twice and the second is not.

**Matched to what indexing writes.** The same `embed_text`, the same content
hash, the same padding, the same upsert. A re-embed that produces subtly
different rows to an ingest is a second implementation of the index, and the
two only disagree in production.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import text

from ..config import settings
from ..domain.chunks import CHUNKER_VERSION, Chunk
from ..domain.ids import text_hash

S = settings.db_schema

#: Big enough that the round trip is not the cost, small enough that one
#: failure does not throw away a minute of work.
BATCH = 96


@dataclass
class ReembedReport:
    connectors: int = 0
    chunks: int = 0
    embedded: int = 0
    skipped: int = 0
    pruned_orphan: int = 0
    pruned_model: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        rate = self.embedded / self.seconds if self.seconds else 0
        parts = [
            f"{self.embedded} embedded",
            f"{self.skipped} already current",
            f"{self.connectors} connector{'' if self.connectors == 1 else 's'}",
        ]
        if self.pruned_orphan:
            parts.append(f"{self.pruned_orphan} orphaned removed")
        if self.pruned_model:
            parts.append(f"{self.pruned_model} from retired models removed")
        parts.append(f"{self.seconds:.1f}s ({rate:.0f}/s)")
        return ", ".join(parts)


def _embed_text(row) -> str:
    """What indexing embedded, reconstructed from the row.

    Built through the domain object rather than by re-joining the parts here,
    so the two cannot drift: if `Chunk.embed_text` ever changes what it
    prepends, this changes with it.
    """
    return Chunk(
        chunk_id=row["chunk_id"],
        doc_id=row["doc_id"],
        text=row["text"],
        heading_path=tuple(row["heading_path"] or ()),
        ordinal=row["ordinal"],
        overlap=row["overlap"] or "",
    ).embed_text


def reembed(
    platform,
    sessions,
    org_id,
    *,
    connector_id: str | None = None,
    all_chunks: bool = False,
    progress=None,
) -> ReembedReport:
    """Embed every chunk that needs it.

    `connector_id` is the slug, as everywhere else a human types one.
    """
    from .indexing import _literal, _pad

    embedder = platform.embedder
    report = ReembedReport()
    started = time.monotonic()

    with sessions() as session:
        connectors = session.execute(
            text(
                f"SELECT id, slug FROM {S}.connector"
                + (" WHERE slug = :slug" if connector_id else "")
                + " ORDER BY slug"
            ),
            {"slug": connector_id} if connector_id else {},
        ).mappings().all()

        if connector_id and not connectors:
            raise KeyError(f"unknown connector: {connector_id}")

        for connector in connectors:
            cid = connector["id"]

            # Two kinds of vector that can never be read again, deleted before
            # anything is written so the count at the end describes the table
            # that now exists.
            #
            # Orphans: a re-chunk mints new chunk ids, and the old chunk's
            # vector stays behind pointing at nothing. Harmless to retrieval —
            # every read joins through document_chunk — and pure weight in
            # every backup and every dump.
            #
            # Retired models: vectors from an embedder this deployment no
            # longer runs. Those are worse than weight. They sit in the same
            # column as the current ones, and a query that reaches them is
            # comparing a cosine across two unrelated spaces.
            report.pruned_orphan += session.execute(
                text(f"""
                    DELETE FROM {S}.embedding e
                     WHERE e.connector_id = :c AND e.kind = 'chunk'
                       AND NOT EXISTS (
                             SELECT 1 FROM {S}.document_chunk c
                              WHERE c.connector_id = e.connector_id
                                AND c.chunk_id = e.ref_id)
                """),
                {"c": cid},
            ).rowcount
            report.pruned_model += session.execute(
                text(f"""
                    DELETE FROM {S}.embedding
                     WHERE connector_id = :c AND kind = 'chunk'
                       AND model_id <> :model
                """),
                {"c": cid, "model": embedder.model_id},
            ).rowcount
            session.commit()
            # The join decides what "needs it" means. Left-joined and filtered
            # on the missing side rather than a NOT IN, because the embedding
            # table is the larger of the two and this is the shape the planner
            # can use the unique index for.
            rows = session.execute(
                text(f"""
                    SELECT c.chunk_id, c.ordinal, c.text, c.heading_path,
                           c.overlap, d.doc_id AS doc_id
                      FROM {S}.document_chunk c
                      JOIN {S}.document d ON d.id = c.document_id
                      LEFT JOIN {S}.embedding e
                             ON e.connector_id = c.connector_id
                            AND e.kind = 'chunk'
                            AND e.ref_id = c.chunk_id
                            AND e.model_id = :model
                     WHERE c.connector_id = :c
                       {"" if all_chunks else "AND e.id IS NULL"}
                     ORDER BY d.doc_id, c.ordinal
                """),
                {"c": cid, "model": embedder.model_id},
            ).mappings().all()

            if not rows:
                continue

            report.connectors += 1
            report.chunks += len(rows)

            for start in range(0, len(rows), BATCH):
                batch = rows[start : start + BATCH]
                texts = [_embed_text(row) for row in batch]
                try:
                    vectors = embedder.embed(texts)
                except Exception as exc:  # noqa: BLE001
                    # One failed batch is reported and skipped rather than
                    # ending the run. A restore that got 90% of the way and
                    # said so can be finished by running the command again;
                    # one that aborted at the same point cannot be told apart
                    # from one that never started.
                    report.errors.append(f"{connector['slug']}: {exc}")
                    continue

                for row, vector, embed_text in zip(batch, vectors, texts):
                    session.execute(
                        text(f"""
                            INSERT INTO {S}.embedding (
                                org_id, connector_id, kind, ref_id, parent_ref,
                                content_hash, model_id, dimension, vector
                            ) VALUES (
                                :org, :c, 'chunk', :ref, :parent, :hash,
                                :model, :dim, CAST(:vec AS vector)
                            )
                            ON CONFLICT (connector_id, kind, ref_id, model_id)
                            DO UPDATE SET
                                content_hash = EXCLUDED.content_hash,
                                vector = EXCLUDED.vector,
                                dimension = EXCLUDED.dimension
                        """),
                        {
                            "org": org_id,
                            "c": cid,
                            "ref": row["chunk_id"],
                            "parent": row["doc_id"],
                            "hash": text_hash(embed_text, "chunk", "1", CHUNKER_VERSION),
                            "model": embedder.model_id,
                            # The true dimension, not the padded width.
                            "dim": embedder.dimension,
                            "vec": _literal(_pad(list(vector))),
                        },
                    )
                report.embedded += len(batch)

                # Committed per batch. A long restore interrupted halfway
                # should keep what it paid for.
                session.commit()
                if progress:
                    progress(connector["slug"], report.embedded, report.chunks)

    report.seconds = time.monotonic() - started
    return report
