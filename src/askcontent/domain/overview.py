"""Describing a corpus from its own contents.

The answer to "what can you tell me" is not in any document — it is the shape
of the collection: how big it is, what its sections are called, what its terms
mean. All of that is real, and describing it is the same discipline the rest of
this system holds to: **constructed from what exists, never generated**.

A model asked to describe a knowledgebase writes something plausible about
subjects it assumes are there. That is the one failure mode this product exists
to avoid, and it is worse here than anywhere else, because an overview is the
first thing somebody reads and sets their expectation of everything after it.

So this is pure and deterministic. It is handed real document metadata and
returns sentences containing only names that occur in it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Overview:
    text: str
    #: Section names, in the corpus's own words. Offered as starting points
    #: because "what can I ask" is the question behind "what can you tell me".
    sections: list[str] = field(default_factory=list)


def describe(
    name: str,
    description: str,
    documents: list,
    *,
    terms: list[str] | None = None,
    max_sections: int = 6,
) -> Overview:
    """One paragraph about a corpus, from the corpus.

    `documents` are metadata objects with `title` and `path`. Sections come
    from the first path segment, which is how documentation sites are almost
    always arranged — and where they are not, the fallback is document titles,
    which are always present.
    """
    total = len(documents)
    if not total:
        return Overview(
            text=(
                f"{name} has no indexed documents yet, so there is nothing I "
                f"can answer from."
            )
        )

    sections = _sections(documents, max_sections)

    parts = [f"{name} holds {total} document{'s' if total != 1 else ''}."]
    if description.strip():
        parts.append(description.strip().rstrip(".") + ".")
    if sections:
        parts.append("It covers " + _list(sections) + ".")
    if terms:
        parts.append(
            "It also defines terms such as " + _list(terms[:4], conjunction="and") + "."
        )
    parts.append("Ask about any of those and I will answer from these documents.")

    return Overview(text=" ".join(parts), sections=sections)


def _sections(documents: list, limit: int) -> list[str]:
    """The corpus's own section names, commonest first.

    Taken from the first path segment. A section that appears once is not a
    section — it is a page — so single-document groups are dropped rather than
    presented as though the corpus were organised around them.
    """
    counts: Counter[str] = Counter()
    for document in documents:
        path = getattr(document, "path", None) or ""
        segments = [s for s in path.strip("/").split("/") if s]
        # The first segment is often the corpus root repeated on every page
        # ("product-guide"), which describes nothing. Prefer the second.
        segment = segments[1] if len(segments) > 1 else (segments[0] if segments else "")
        if segment:
            counts[_humanise(segment)] += 1

    named = [name for name, count in counts.most_common(limit * 2) if count > 1]
    if named:
        return named[:limit]

    # No usable paths. Titles are always there, and a few real ones say more
    # about a corpus than a made-up category would.
    return [
        getattr(d, "title", "") for d in documents[:limit] if getattr(d, "title", "")
    ]


def _humanise(segment: str) -> str:
    return re.sub(r"[-_]+", " ", segment).strip().lower()


def _list(items: list[str], conjunction: str = "and") -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"
