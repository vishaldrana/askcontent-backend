"""What the page the widget sits on is already showing.

The widget is embedded on a page that is displaying something — an analysis, a
dashboard, a survey's results. A visitor reading it has two kinds of question:
one the corpus answers, and one only the page answers. Until now the second got
a refusal, which is correct and is also the moment the visitor decides this is a
help-article search box.

This is the cheap half of that problem (design 09, step 1): the host passes what
it is *already rendering*, so the assistant can answer about it without a
network hop, a credential, a timeout or a new class of access bug.

Three properties are load-bearing.

**It is data, not documentation.** A page summary has no URL, no owner, no
access rule and no shelf life. It is attributed as `[page]` and never as a
numbered passage, because a reader must be able to tell "your documentation
says" from "the screen in front of you says". Conflating them is how an answer
comes to cite a document for a number that came from a chart.

**It is bounded.** Host text reaches the prompt, so it is capped, stripped of
control characters and delimited. The cap is not politeness: a page that pastes
its entire result set would push the passages out of the model's attention, and
the answer would drift off the corpus without anything appearing to fail.

**It is the host's, not the visitor's.** It is set by the page in `init`, not
typed into the composer. That distinction is the access argument: a visitor
cannot use it to describe content they cannot see, because they never write it.
A host that lets its own users control it has widened their own trust boundary,
which is theirs to widen — the widget cannot tell the difference and does not
pretend to.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

#: Roughly a screenful of text. Enough for the summary a page renders; far too
#: little for a result set, which is the point — the REST source in design 09
#: is the answer for anything larger, and truncating loudly is better than
#: quietly crowding out the passages.
MAX_SUMMARY_CHARS = 4000
MAX_TITLE_CHARS = 200
MAX_KEY_CHARS = 128

#: Everything except tab and newline. A page that injects control characters is
#: not formatting; it is trying to break out of the block it was put in.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PageContext(BaseModel):
    """What the host says its page is showing, bounded.

    `key` is carried but unused in step 1. It is the identifier a configured
    REST source would be called with (design 09), and it is recorded now so
    that a host integrating today does not have to change their snippet later.
    """

    title: str = ""
    summary: str = ""
    key: str = ""
    #: True when the summary was longer than the cap. Reported rather than
    #: hidden: an answer built from half a page should say so.
    truncated: bool = False

    @property
    def usable(self) -> bool:
        return bool(self.summary.strip() or self.title.strip())

    def render(self) -> str:
        """The block the answerer sees."""
        head = self.title.strip() or "The page the reader is on"
        body = self.summary.strip()
        if self.truncated:
            body += "\n(This summary was truncated.)"
        return f"{head}\n{body}" if body else head


def from_payload(payload: object) -> PageContext | None:
    """Build one from whatever the widget sent, or nothing.

    Returns `None` rather than an empty object for the common case, so every
    call site downstream reads `if page:` and cannot accidentally render an
    empty block that tells the model a page exists and says nothing about it.
    """
    if not isinstance(payload, dict):
        return None

    title = _clean(payload.get("title"), MAX_TITLE_CHARS)[0]
    key = _clean(payload.get("key"), MAX_KEY_CHARS)[0]
    summary, truncated = _clean(payload.get("summary"), MAX_SUMMARY_CHARS)

    context = PageContext(title=title, summary=summary, key=key, truncated=truncated)
    return context if context.usable or context.key else None


def _clean(value: object, limit: int) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    text = _CONTROL.sub(" ", value).strip()
    if len(text) <= limit:
        return text, False
    # Cut at a boundary where there is one nearby, so the block does not end
    # mid-word and read as corruption.
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit - 200 else cut).rstrip(), True
