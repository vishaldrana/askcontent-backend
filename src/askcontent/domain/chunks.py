"""Chunk model and the structure-aware chunker (CNT-CHK-*).

Deterministic: the same ParsedDocument and the same chunker version produce
byte-identical chunks with identical ids.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .documents import Block, BlockKind, ParsedDocument
from .ids import chunk_id

#: Bumped for overlap and code-block handling. The version participates in the
#: embedding hash, so an upgrade re-embeds exactly the documents it changes.
CHUNKER_VERSION = "1.1.0"


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    text: str
    heading_path: tuple[str, ...]
    ordinal: int
    page: int | None = None
    is_table: bool = False
    is_code: bool = False
    # Parent-child (CNT-CHK-04): the child is embedded for precision, the
    # parent is what enters the answer context for coherence.
    parent_text: str = ""
    #: The tail of the preceding chunk.
    #:
    #: Held **separately from `text`** on purpose. Overlap exists so that a fact
    #: straddling a boundary is not lost from both chunks — that is a
    #: *retrieval* problem, so the overlap belongs in what gets embedded. It is
    #: deliberately not part of the citation: a span repeating the previous
    #: paragraph reads as a bug, and two adjacent citations would show the same
    #: sentence twice.
    overlap: str = ""

    @property
    def embed_text(self) -> str:
        """What is embedded: heading path, overlap, then the chunk's own text.

        The path is prepended because 'Rate limits' under API > v2 and under
        Support > Escalation are different subjects; without it they embed
        almost identically (CNT-CHK-02).
        """
        parts: list[str] = []
        if self.heading_path:
            parts.append(" > ".join(self.heading_path))
        if self.overlap:
            parts.append(self.overlap)
        parts.append(self.text)
        return "\n".join(parts)


def _estimate_tokens(text: str) -> int:
    # Deliberately crude and deterministic. A real tokenizer is a model
    # dependency, and chunking must stay a pure function (CNT-CHK-05).
    return max(1, len(text) // 4)


def _tail(text: str, tokens: int) -> str:
    """The last whole sentences of a chunk, up to a token budget.

    Cut on a sentence boundary rather than a character count: an overlap that
    begins mid-clause adds noise to the embedding instead of context.
    """
    if tokens <= 0 or not text:
        return ""
    budget = tokens * 4
    if len(text) <= budget:
        return text
    window = text[-budget:]
    for separator in (". ", "? ", "! ", "\n"):
        cut = window.find(separator)
        if 0 <= cut < len(window) - 20:
            return window[cut + len(separator):].strip()
    # No sentence boundary in range. Fall back to a word boundary rather than a
    # character count: an overlap beginning "urns a 429" contributes a token the
    # embedder has never seen and helps nothing.
    space = window.find(" ")
    return window[space + 1:].strip() if space >= 0 else window.strip()


def chunk_document(
    parsed: ParsedDocument,
    *,
    target_tokens: int = 220,
    max_tokens: int = 420,
    parent_tokens: int = 900,
    overlap_tokens: int = 40,
) -> list[Chunk]:
    """Structure-aware chunking with overlap.

    A section is split on block boundaries, never mid-block; tables and code
    blocks are atomic; each chunk carries the tail of its predecessor for
    embedding but not for citation.
    """
    if parsed.refused:
        return []

    sections = _group_by_heading_path(parsed.blocks)
    chunks: list[Chunk] = []
    ordinal = 0

    for heading_path, blocks in sections:
        parent_text = _render(blocks)[:parent_tokens * 4]
        buffer: list[Block] = []
        buffer_tokens = 0
        carry = ""

        def flush() -> None:
            nonlocal buffer, buffer_tokens, ordinal, carry
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
                    is_code=any(b.kind is BlockKind.CODE for b in buffer),
                    parent_text=parent_text,
                    overlap=carry,
                )
            )
            ordinal += 1
            # The next chunk in this section carries this one's tail. Overlap
            # does not cross a heading boundary: two sections are two subjects,
            # and bleeding one into the other is exactly what the heading path
            # exists to prevent.
            carry = _tail(text, overlap_tokens)
            buffer = []
            buffer_tokens = 0

        for block in blocks:
            block_tokens = _estimate_tokens(block.text)

            # A table is never split, and an oversized one is emitted whole as
            # its own chunk with its heading path (CNT-CHK-03). A code block is
            # atomic for the same reason and a sharper one: half a shell command
            # is not a shorter shell command, it is a wrong one — and a snippet
            # broken across chunks retrieves as neither.
            if block.kind in (BlockKind.TABLE, BlockKind.CODE):
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
