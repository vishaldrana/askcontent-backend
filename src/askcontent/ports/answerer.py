"""The answerer port.

WHY THIS EXISTS
===============
Retrieval finds passages. Something still has to turn passages into an answer,
and that step is where a content assistant is either trustworthy or worthless.

The failure this port is designed against is not "the model hallucinated". It
is the quieter one: an answer that is *fluent and unattributed*, so the reader
cannot tell which sentence came from which document, and therefore cannot check
any of it. A content system whose answers cannot be checked is a search engine
with extra confidence.

So the contract is narrow on purpose:

  * the answerer is given numbered passages and may use nothing else;
  * every claim it makes must carry the number of the passage supporting it;
  * if the passages do not answer the question it must say so, in those words,
    rather than assembling something adjacent.

That last rule is the one that makes "Who are you?" return "the corpus does not
cover this" instead of a paragraph stitched from whatever ranked highest.

REPLACING THE ADAPTER
=====================
`AnthropicAnswerer` is one implementation. The internal company SDK is expected
to replace it, and the only thing that has to match is this protocol — the
service layer never imports a vendor SDK, which is asserted by a test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Passage:
    """One numbered piece of evidence, as the answerer sees it.

    `number` is what appears in the answer as `[3]`, and it is stable for the
    life of one answer so the console can link a marker to the passage that
    justifies it.
    """

    number: int
    title: str
    url: str
    text: str
    heading_path: tuple[str, ...] = ()
    updated: str | None = None
    authority: str | None = None


@dataclass
class AnswerChunk:
    """A streamed fragment. Either prose or a signal about the whole answer."""

    text: str = ""
    #: Set on the final chunk. `supported` is false when the model reported
    #: that the passages do not answer the question — which is a successful
    #: outcome, not an error.
    done: bool = False
    supported: bool = True
    #: Passage numbers the finished answer actually cited.
    cited: tuple[int, ...] = ()
    #: Whether the answer attributed anything to the page the reader is on.
    #: Separate from `cited` because it is a different kind of support: a
    #: passage can be opened and checked, a page cannot.
    used_page: bool = False
    #: Datapoint numbers the answer used — the `1` in `[d1]`. Separate from
    #: `cited` for the same reason, and numbered separately so a marker can
    #: never be ambiguous about which kind of evidence it names.
    used_data: tuple[int, ...] = ()
    usage: dict = field(default_factory=dict)


class Answerer(Protocol):
    name: str
    model_id: str

    def stream(
        self,
        *,
        question: str,
        passages: Sequence[Passage],
        history: Sequence[tuple[str, str]] = (),
        instructions: str = "",
        page: object | None = None,
        data: object | None = None,
        detail: str | None = None,
    ) -> AsyncIterator[AnswerChunk]:
        """Stream a grounded answer.

        `instructions` is what the knowledgebase's owner added. It shapes tone,
        vocabulary and format; it cannot switch off attribution, because the
        grounding rules are assembled after it.

        `history` is prior (question, answer) turns in this thread, provided so
        that "and what about Texas?" resolves. It is context for understanding
        the *question* — it is never a source of facts, because a claim whose
        support has scrolled out of the evidence panel is unattributable.

        `page` is a `PageContext` when the host told us what its page is
        showing. It is a source, unlike history — but a different kind from a
        passage, and it is attributed `[page]` so a reader can tell "your
        documentation says" from "the screen in front of you says".

        `data` is a `DatapointSet` when a configured source was called. Same
        argument, one step further: these are values that were true at a named
        instant and are attributed `[d1]`, `[d2]`, because a reader cannot open
        them and the interface must not imply otherwise.
        """
        ...
