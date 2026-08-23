"""Deciding whether a document actually changed.

A byte hash answers "are these the same bytes", which is not the question. A
re-save, a reflowed paragraph, a converted quote character, an extra blank line
between sections — all change every byte and none of them change what the
document says. Treating them as changes means re-parsing and re-embedding a
corpus for nothing, and it means the word "changed" on a review screen stops
meaning anything.

So there are three fingerprints, each answering a different question:

    file        Are these the same bytes?              → skip the parse
    content     Does it say the same thing?            → skip the re-embed
    structure   Is it laid out the same way?           → explain what moved

`content` is the one that matters, and it is computed from the *parsed* text
after normalisation, so it is also immune to a change of source format: the same
policy exported to HTML and to PDF has the same content fingerprint.

Pure: no I/O, no model call.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel

#: Characters that carry no meaning and vary freely between exports.
_ZERO_WIDTH = dict.fromkeys(
    map(ord, "​‌‍⁠﻿­"), None
)

#: Typographic variants that mean the same thing. A wiki that "smartens" quotes
#: on save would otherwise rewrite every document in the corpus.
_EQUIVALENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "‒": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "…": "...", "•": "-", "·": "-",
}
_EQUIVALENTS_TABLE = {ord(k): v for k, v in _EQUIVALENTS.items()}

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z0-9$%.,:/-]+")


def normalise(text: str) -> str:
    """Fold away everything that varies without meaning.

    Case is **kept**. In policy and legal text "MUST" and "must" are not the
    same word, and a normaliser that lowercases would hide the one edit most
    worth noticing.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_ZERO_WIDTH).translate(_EQUIVALENTS_TABLE)
    return _WHITESPACE.sub(" ", folded).strip()


def _digest(*parts: str) -> str:
    hasher = hashlib.blake2b(digest_size=16)
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def content_fingerprint(blocks) -> str:
    """What the document says, independent of layout, encoding **and order**.

    Sorted before hashing, so moving a paragraph does not read as a rewrite.
    Order is not lost — `structure_fingerprint` carries it — and separating the
    two is what lets the comparison say *reordered* rather than *changed*, which
    are different problems with different costs: reordering needs a re-chunk
    because heading paths and adjacency move, but the words are already known
    to be correct.

    Blocks that normalise to nothing are dropped rather than hashed as empty
    strings: an added blank paragraph is not a change, and hashing its presence
    would make it one.
    """
    parts = sorted(p for p in (normalise(getattr(b, "text", "")) for b in blocks) if p)
    return _digest(*parts)


def structure_fingerprint(blocks) -> str:
    """How the document is laid out: block kinds and heading paths.

    Separate from content so a section that moved can be distinguished from a
    sentence that was rewritten — the same words under a different heading is a
    real change, and one a content hash alone cannot see.
    """
    parts = []
    for block in blocks:
        text = normalise(getattr(block, "text", ""))
        if not text:
            continue
        kind = str(getattr(block, "kind", ""))
        path = "/".join(getattr(block, "heading_path", ()) or ())
        parts.append(f"{kind}|{path}|{len(text)}")
    return _digest(*parts)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(normalise(text))


class Verdict(StrEnum):
    IDENTICAL = "identical"          # same bytes
    COSMETIC = "cosmetic"            # different bytes, same words
    REORDERED = "reordered"          # same words, different layout
    CHANGED = "changed"              # the words moved
    NEW = "new"                      # nothing to compare against


class Comparison(BaseModel):
    verdict: Verdict
    #: 0.0–1.0, how much of the text is shared. Only computed when the content
    #: differs, because it is the expensive part and the answer is 1.0 otherwise.
    similarity: float | None = None
    reason: str = ""

    @property
    def needs_reindex(self) -> bool:
        """Cosmetic and reordered changes do not move a single embedding.

        Reordering changes the heading path a chunk carries, so it does need a
        re-chunk — but not a re-fetch, and the distinction is worth having.
        """
        return self.verdict in (Verdict.CHANGED, Verdict.REORDERED, Verdict.NEW)


def compare(
    *,
    old_file: str | None,
    new_file: str,
    old_content: str | None,
    new_content: str,
    old_structure: str | None = None,
    new_structure: str | None = None,
    old_text: str | None = None,
    new_text: str | None = None,
) -> Comparison:
    """Say what kind of change this is, and why."""
    if old_content is None and old_file is None:
        return Comparison(verdict=Verdict.NEW, reason="not seen before")

    if old_file is not None and old_file == new_file:
        return Comparison(verdict=Verdict.IDENTICAL, similarity=1.0,
                          reason="byte-identical")

    if old_content is not None and old_content == new_content:
        if old_structure and new_structure and old_structure != new_structure:
            return Comparison(
                verdict=Verdict.REORDERED, similarity=1.0,
                reason="same words, different layout or headings",
            )
        return Comparison(
            verdict=Verdict.COSMETIC, similarity=1.0,
            reason="whitespace, encoding or formatting only",
        )

    similarity = None
    if old_text is not None and new_text is not None:
        similarity = _similarity(old_text, new_text)

    detail = (
        f"content differs ({similarity:.0%} of the text is shared)"
        if similarity is not None else "content differs"
    )
    return Comparison(verdict=Verdict.CHANGED, similarity=similarity, reason=detail)


def _similarity(old: str, new: str, shingle: int = 5) -> float:
    """Shingled Jaccard over normalised tokens.

    Shingles rather than a bag of words, so moving a paragraph registers as a
    change while a synonym swap in one sentence does not swamp the score. Cheap
    enough to run on every refresh, which matters because the point is to tell a
    typo fix from a rewrite without a human reading both.
    """
    a, b = tokens(old), tokens(new)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    # Short documents have few shingles, so a one-word edit moves the score a
    # long way — 57% shared for a single figure change reads as a rewrite. Scale
    # the window down rather than reporting a number that misleads.
    shortest = min(len(a), len(b))
    if shortest < 40:
        shingle = 3 if shortest >= 12 else 2

    if len(a) < shingle or len(b) < shingle:
        set_a, set_b = set(a), set(b)
    else:
        set_a = {" ".join(a[i : i + shingle]) for i in range(len(a) - shingle + 1)}
        set_b = {" ".join(b[i : i + shingle]) for i in range(len(b) - shingle + 1)}
    union = set_a | set_b
    return round(len(set_a & set_b) / len(union), 4) if union else 1.0
