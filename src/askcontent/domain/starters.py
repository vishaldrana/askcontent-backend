"""What to offer somebody who has not asked anything yet.

An empty chat is the hardest screen in the product. The reader does not know
what this knowledgebase contains, does not know how specific they are allowed
to be, and has no way to tell a question that will work from one that will
refuse. A blank box asks them to guess, and the most common guess — a broad
question about the company — is the one most likely to come back empty.

So the empty state offers real entries from the corpus.

Two rules, both about not lying:

*The chips are titles, not invented questions.* It is tempting to turn
"Adding Hyperlink to Survey Text" into "How do I add a hyperlink to survey
text?", and every rule that does it is a rule that mangles the next title:
"Setting" does not de-gerund to "sett", "CSAT/CES Rating" is not a verb
phrase at all. A generated question that reads badly is worse than a topic
that reads plainly, and a topic is a perfectly good retrieval query.

*They span the corpus.* Six chips drawn from the largest documents would come
from whichever section happens to be written at length — the reader learns
that this is a knowledgebase about survey design and never discovers it also
covers installation and billing. One per section first, then fill.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol

from pydantic import BaseModel

# Titles that name the container rather than anything in it. Offering these
# teaches the reader nothing about what the corpus covers, and "Introduction"
# is answered by the overview path anyway.
_GENERIC = frozenset(
    {
        "home",
        "index",
        "introduction",
        "welcome",
        "overview",
        "contents",
        "table of contents",
        "documentation",
        "docs",
        "help",
        "help centre",
        "help center",
    }
)

MAX_LABEL_CHARS = 48


class _Doc(Protocol):
    doc_id: str
    title: str
    space: str | None
    path: str | None
    size_bytes: int | None


class Starter(BaseModel):
    """One suggestion, and where it came from."""

    label: str
    question: str
    section: str | None = None
    #: The document it was chosen from. Carried so a caller that wants to
    #: write a real question — rather than offer the title — can go and read
    #: what the page says.
    doc_id: str = ""


def _section(doc: _Doc) -> str | None:
    """The part of the corpus a document sits in.

    Path before space, which looks backwards until you notice that a space is
    a property of the *connector*: every document in a crawled site carries
    the same one, so it separates nothing and the spread rule collapses to
    "pick the first document". The path is the structure inside the corpus,
    which is the structure the reader would recognise.
    """
    path = (doc.path or "").strip("/")
    parts = [p for p in path.split("/") if p]
    # A one-segment path *is* the page, not a section it belongs to.
    if len(parts) >= 2:
        # The first segment of a documentation site is usually the same word
        # on every page ("product-guide"), so it separates nothing either.
        # Use the second when there is one.
        return parts[1] if len(parts) > 2 else parts[0]
    return doc.space or None


def choose(
    documents: Iterable[_Doc],
    limit: int = 6,
    weights: Mapping[str, int] | None = None,
) -> tuple[Starter, ...]:
    """Suggestions for an empty chat, spread across the corpus.

    `weights` is how much of a document there is, by doc id — the caller's
    measure, because the honest one differs by source. An index that reports
    `size_bytes` has it already; a crawled corpus does not, and there the
    number of indexed chunks is the closest thing to "how much this page
    actually says". Without either, everything ties and the order falls back
    to the title, which is stable but tells the reader nothing.

    Deterministic: same corpus, same chips. A suggestion list that reshuffles
    on every page load looks like it is guessing, and a reader who saw
    something useful a moment ago cannot get back to it.
    """
    if limit <= 0:
        return ()

    eligible = [
        d
        for d in documents
        if d.title
        and d.title.strip()
        and d.title.strip().lower() not in _GENERIC
        and len(d.title.strip()) <= MAX_LABEL_CHARS
    ]
    # Biggest first as a proxy for substance — a stub answers nothing — with
    # the title as the tie-break so the order never depends on how the index
    # happened to page.

    def substance(d: _Doc) -> int:
        if weights is not None and d.doc_id in weights:
            return weights[d.doc_id]
        return d.size_bytes or 0

    eligible.sort(key=lambda d: (-substance(d), d.title.strip().lower()))

    chosen: list[_Doc] = []
    seen_sections: set[str] = set()
    for doc in eligible:
        section = _section(doc)
        if section is not None and section in seen_sections:
            continue
        if section is not None:
            seen_sections.add(section)
        chosen.append(doc)
        if len(chosen) == limit:
            break

    # Every section is represented; fill any remaining slots with the next
    # largest documents regardless of section.
    if len(chosen) < limit:
        already = {id(d) for d in chosen}
        for doc in eligible:
            if id(doc) in already:
                continue
            chosen.append(doc)
            if len(chosen) == limit:
                break

    return tuple(
        Starter(
            label=d.title.strip(),
            question=d.title.strip(),
            section=_section(d),
            doc_id=getattr(d, "doc_id", "") or "",
        )
        for d in chosen
    )
