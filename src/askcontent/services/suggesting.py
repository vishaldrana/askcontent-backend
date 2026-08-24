"""Follow-up questions: written by a model, kept only if the corpus says so.

The service owns the part that matters — what a suggestion is checked against
— and knows nothing about how it was written. Both paths end in the same gate,
so a constructed suggestion and a written one are held to one standard.
"""

from __future__ import annotations

from ..domain.suggestions import keep


def follow_ups(suggester, citations, *, question: str = "", limit: int = 4) -> list[str]:
    """Questions to offer after an answer.

    The source is the passages that answered it, so every suggestion is about
    something already retrieved, parsed and shown — which is as close to a
    guarantee of answerability as this can get without asking twice.
    """
    if not citations:
        return []

    source = "\n\n".join(
        f"{getattr(c, 'title', '') or ''}\n{getattr(c, 'span', '') or ''}"
        for c in citations
    )

    written = suggester.suggest(source=source, asked=question, limit=limit) if suggester else []
    return keep(written, source=source, asked=question, limit=limit)


def openers(suggester, documents, *, limit: int = 6) -> list[str]:
    """Questions to offer somebody who has not asked anything yet.

    Built from the corpus rather than from one answer, and cached by the
    caller: this is the same set for every visitor until the corpus changes,
    and paying for it on each page load would be paying for the same six
    sentences over and over.
    """
    if not documents:
        return []

    source = "\n\n".join(
        f"{getattr(d, 'title', '') or ''} — {getattr(d, 'path', '') or ''}"
        for d in documents[:120]
    )
    written = suggester.suggest(source=source, limit=limit) if suggester else []
    return keep(written, source=source, limit=limit)
