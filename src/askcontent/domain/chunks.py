"""Chunk model and the structure-aware chunker (CNT-CHK-*).

Deterministic: the same ParsedDocument and the same chunker version produce
byte-identical chunks with identical ids.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .documents import Block, BlockKind, ParsedDocument
from .ids import chunk_id

CHUNKER_VERSION = "1.0.0"


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    text: str
    heading_path: tuple[str, ...]
    ordinal: int
    page: int | None = None
    is_table: bool = False
    # Parent-child (CNT-CHK-04): the child is embedded for precision, the
    # parent is what enters the answer context for coherence.
    parent_text: str = ""

    @property
    def embed_text(self) -> str:
        """Heading path is prepended before embedding (CNT-CHK-02).

        'Rate limits' under API > v2 and under Support > Escalation are
        different subjects; without the path they embed almost identically.
        """
        if not self.heading_path:
            return self.text
        return " > ".join(self.heading_path) + "\n" + self.text


def _estimate_tokens(text: str) -> int:
    # Deliberately crude and deterministic. A real tokenizer is a model
    # dependency, and chunking must stay a pure function (CNT-CHK-05).
    return max(1, len(text) // 4)


def chunk_document(
    parsed: ParsedDocument,
    *,
    target_tokens: int = 220,
    max_tokens: int = 420,
    parent_tokens: int = 900,
) -> list[Chunk]:
    if parsed.refused:
        return []

    sections = _group_by_heading_path(parsed.blocks)
    chunks: list[Chunk] = []
    ordinal = 0

    for heading_path, blocks in sections:
        parent_text = _render(blocks)[:parent_tokens * 4]
        buffer: list[Block] = []
        buffer_tokens = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens, ordinal
            if not buffer:
                return
            text = _render(buffer)
            chunks.append(
                Chunk(
                    chunk_id=chunk_id(parsed.doc_id, heading_path, ordinal, text),
                    doc_id=parsed.doc_id,
                    text=text,
                    heading_path=heading_path,
                    ordinal=ordinal,
                    page=buffer[0].page,
                    is_table=any(b.kind is BlockKind.TABLE for b in buffer),
                    parent_text=parent_text,
                )
            )
            ordinal += 1
            buffer = []
            buffer_tokens = 0

        for block in blocks:
            block_tokens = _estimate_tokens(block.text)

            # A table is never split, and an oversized one is emitted whole as
            # its own chunk with its heading path (CNT-CHK-03).
            if block.kind is BlockKind.TABLE:
                flush()
                buffer = [block]
                buffer_tokens = block_tokens
                flush()
                continue

            if buffer_tokens + block_tokens > max_tokens and buffer:
                flush()
            buffer.append(block)
            buffer_tokens += block_tokens
            if buffer_tokens >= target_tokens:
                flush()

        flush()

    return chunks


def _group_by_heading_path(
    blocks: tuple[Block, ...],
) -> list[tuple[tuple[str, ...], list[Block]]]:
    sections: list[tuple[tuple[str, ...], list[Block]]] = []
    for block in blocks:
        if block.kind is BlockKind.HEADING:
            # The heading itself belongs to the section it opens, so its text
            # is carried into that section's chunks rather than orphaned.
            path = block.heading_path
            sections.append((path, [block]))
            continue
        if sections and sections[-1][0] == block.heading_path:
            sections[-1][1].append(block)
        else:
            sections.append((block.heading_path, [block]))
    return sections


def _render(blocks: list[Block]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.kind is BlockKind.TABLE and block.table is not None:
            parts.append(block.table.render())
        elif block.kind is BlockKind.HEADING:
            parts.append(("#" * (block.level or 1)) + " " + block.text)
        elif block.kind is BlockKind.LIST_ITEM:
            parts.append("- " + block.text)
        else:
            parts.append(block.text)
    return "\n\n".join(p for p in parts if p.strip())
