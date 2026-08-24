"""A value that was true when we asked.

Deliberately not a `Citation`, and the distinction is the whole of design 09.

A citation points at a document. It has an owner, a date, an access rule and a
URL, and a reader who doubts the answer can open it and check. That property is
the entire basis on which anyone trusts anything this product says.

A datapoint points at a number that has already changed. It has a source name,
the key it was fetched with, and the instant it was fetched — and nothing a
reader can open. The day these two share a type is the day an answer says
"according to the Q3 report" about a figure that came from an API call, and
every guarantee behind the citation model quietly stops holding.

So they are separate types, marked separately in the prose (`[d1]` rather than
`[1]`), rendered separately, and expired separately.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class Datapoint(BaseModel):
    """One named value from a configured source."""

    #: Position in the answer's markers: `[d1]` is `number == 1`. Stable for
    #: the life of one answer, exactly like a passage number.
    number: int
    #: The source's display name — "Survey analytics", not a URL. What the
    #: reader is told the number came from.
    source: str
    #: The identifier it was fetched with. The page's, never the model's.
    key: str
    label: str
    value: str
    fetched_at: dt.datetime
    #: How long this may be reused before it has to be fetched again.
    ttl_seconds: int = 60

    def render(self) -> str:
        return f"[d{self.number}] {self.label}: {self.value}"

    def is_stale(self, now: dt.datetime | None = None) -> bool:
        """Past its ttl.

        Used when a stored conversation is reloaded. The answer stays; the
        numbers in it are marked as what they are — a reading from a moment
        that has passed. Re-asking re-fetches.
        """
        now = now or dt.datetime.now(dt.timezone.utc)
        fetched = self.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=dt.timezone.utc)
        return (now - fetched).total_seconds() > self.ttl_seconds


class DatapointSet(BaseModel):
    """What one fetch produced, and whether it worked.

    Failure is a value here rather than an exception, because the caller's
    response to it is not to stop: it is to answer from the passages and *say*
    the live data was unavailable. An answer that silently omits the data it
    was supposed to include looks complete and is wrong about the one thing
    the visitor asked.
    """

    source: str = ""
    key: str = ""
    points: tuple[Datapoint, ...] = ()
    error: str | None = None
    #: Served from cache rather than fetched. Reported so a slow source and a
    #: warm one are distinguishable in the trace.
    cached: bool = False
    elapsed_ms: int = 0

    @property
    def usable(self) -> bool:
        return bool(self.points) and self.error is None

    def notice(self) -> str | None:
        """What to tell the reader when it did not work."""
        if self.error is None:
            return None
        return (
            f"Answered from the knowledgebase only — {self.source or 'the live source'} "
            f"did not respond."
        )

    def render(self) -> str:
        head = f"{self.source} (fetched for {self.key})" if self.key else self.source
        return "\n".join([head, *(p.render() for p in self.points)])


class FieldMap(BaseModel):
    """One value to keep from a response, and what to call it.

    The mapping is what stops a raw JSON blob reaching the prompt. Without it
    the model receives whatever the host's API happens to return — including
    fields nobody meant to expose — and gets to guess what `m3_ret_pct_wow`
    means. With it, an answer can only speak about values somebody named.
    """

    #: Dotted path into the response: "summary.nps". A list index is a number:
    #: "segments.0.name".
    path: str
    label: str


def extract(payload: object, fields: tuple[FieldMap, ...], *, source: str, key: str,
            fetched_at: dt.datetime, ttl_seconds: int) -> tuple[Datapoint, ...]:
    """Pull the mapped values out of a response.

    A path that is not present is skipped rather than rendered as "None". The
    host's API omitting a field is normal — a survey with no responses has no
    average — and an answer that says "NPS: None" is worse than one that does
    not mention NPS.
    """
    points: list[Datapoint] = []
    for field in fields:
        value = _dig(payload, field.path)
        if value is None or value == "":
            continue
        points.append(
            Datapoint(
                number=len(points) + 1,
                source=source,
                key=key,
                label=field.label,
                value=_stringify(value),
                fetched_at=fetched_at,
                ttl_seconds=ttl_seconds,
            )
        )
    return tuple(points)


def _dig(payload: object, path: str) -> object:
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


#: Long enough for a name or a short list, short enough that no single value
#: can crowd the passages out of the prompt.
MAX_VALUE_CHARS = 400


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:,}" if isinstance(value, int) else f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value)[:MAX_VALUE_CHARS]
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_stringify(v)}" for k, v in value.items())[:MAX_VALUE_CHARS]
    return str(value)[:MAX_VALUE_CHARS]


class ContextSource(BaseModel):
    """Where live values come from, per connector.

    Zero or one per connector, not a list. The first version of this that
    supports three sources spends its life deciding which to call, and that
    decision is a routing problem nobody asked for.
    """

    name: str
    url: str
    method: str = "GET"
    #: `forward_visitor` sends the asker's own token, so the host applies its
    #: own access rules to its own data — the only place those rules are known.
    #: `service_header` has no per-user notion and is the dangerous one; see
    #: design 09.
    auth: str = "forward_visitor"
    timeout_seconds: float = 3.0
    ttl_seconds: int = 60
    fields: tuple[FieldMap, ...] = ()
    #: A key that does not match is refused before any call is made. Cheap, and
    #: it turns "somebody crafted a request" into a 400.
    key_pattern: str = ""
    enabled: bool = True

    def target(self, key: str) -> str:
        return self.url.replace("{key}", key)


class Verdict(BaseModel):
    """Whether to spend a network call on this question."""

    fetch: bool
    why: str


#: Words that make a question about *this* reader's situation rather than about
#: how the product works. Deliberately small and boring: the cost of a false
#: positive is one HTTP call, and the cost of a false negative is the feature
#: not working on the question it exists for.
_DEICTIC = (
    "this", "these", "my", "mine", "our", "ours", "here", "current",
    "currently", "above", "below", "on screen", "shown", "showing",
)


def should_fetch(question: str, *, has_key: bool, corpus_covers: bool) -> Verdict:
    """The routing decision, as a pure function.

    Retrieval runs first and the live call is second, because the corpus
    answers most questions and costs nothing extra — so this only says yes when
    there is a reason.

    Two reasons. The question points at the reader's own situation, or the
    corpus did not cover it and a source might. Both need a key: without one
    there is nothing to fetch *with*, and calling anyway would be asking the
    host's API a question about nobody.
    """
    if not has_key:
        return Verdict(fetch=False, why="the page supplied no key")

    lowered = f" {question.lower()} "
    if any(f" {word} " in lowered for word in _DEICTIC):
        return Verdict(fetch=True, why="the question is about the reader's own view")
    if not corpus_covers:
        return Verdict(fetch=True, why="the knowledgebase does not cover it")
    return Verdict(fetch=False, why="the knowledgebase covers it")


__all__ = [
    "ContextSource",
    "Datapoint",
    "DatapointSet",
    "FieldMap",
    "Verdict",
    "extract",
    "should_fetch",
]
