"""URL normalisation and the resolution ladder.

Pasting a URL is **resolution, not ingestion**: the content already lives in the
index, and the URL is only the handle a person happens to have. Fetching it
instead would manufacture a second copy of a document the system of record
already holds — which is the `stale_duplicate` failure the corpus plants a test
for.

This module is pure. It performs no I/O: the adapter supplies candidates, this
decides what they mean.
"""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field

# Three thresholds, the same discipline as term resolution: above accept it is a
# match; between accept and the floor the candidates are shown and a human
# picks; below the floor nothing is added and nothing is guessed.
ACCEPT_SCORE = 0.80
FLOOR_SCORE = 0.45

#: Query parameters that never identify a document. Stripped before matching,
#: because a link copied from a chat client carries several of them and a
#: resolver that treats them as significant matches nothing.
TRACKING_PARAMS = frozenset(
    """
    utm_source utm_medium utm_campaign utm_term utm_content gclid fbclid msclkid
    mc_cid mc_eid ref referrer source src share sharing_token e web
    _gl igshid si spm scmts from_channel trk trkCampaign originalSubdomain
    """.split()
)

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'\)\]}]+", re.I)


class Rung(StrEnum):
    """Which rung matched. Recorded on the member, because 'resolved by
    full-text search on the slug' and 'resolved by exact URL' are different
    claims and a curator reviewing a collection needs to tell them apart."""

    EXACT = "exact"
    ALIAS = "alias"
    REDIRECT = "redirect"
    PATH = "path"
    TITLE = "title"
    SEARCH = "search"


RUNG_CONFIDENCE: dict[Rung, float] = {
    Rung.EXACT: 1.00,
    Rung.ALIAS: 0.95,
    Rung.REDIRECT: 0.90,
    Rung.PATH: 0.85,
    Rung.TITLE: 0.60,
    Rung.SEARCH: 0.50,
}


class Outcome(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class HostAlias(BaseModel):
    """One deployment's host folding.

    Configured, not inferred (CNT-URL-05). An intranet reached at three
    hostnames, a document platform serving one file under a viewer URL and a
    download URL, a wiki accepting both a numeric page id and a title slug —
    these are per-deployment facts, and a resolver that does not know them
    resolves about half of what a group pastes.
    """

    model_config = ConfigDict(frozen=True)

    canonical: str
    aliases: tuple[str, ...] = ()


class NormalisePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    host_aliases: tuple[HostAlias, ...] = ()
    strip_params: frozenset[str] = Field(default=TRACKING_PARAMS)
    #: Path prefixes a platform inserts and which carry no identity, e.g. a
    #: SharePoint viewer prefix. Removed before matching.
    strip_path_prefixes: tuple[str, ...] = ()
    keep_params: tuple[str, ...] = ()

    def canonical_host(self, host: str) -> str:
        host = host.lower().removeprefix("www.")
        for alias in self.host_aliases:
            if host == alias.canonical or host in alias.aliases:
                return alias.canonical
        return host


def normalise(url: str, policy: NormalisePolicy | None = None) -> str:
    """Fold a URL to the form used for matching.

    Everything removed here is something that varies between two links to the
    same document: scheme, default port, host casing and aliases, tracking
    parameters, the fragment, percent-encoding, and a trailing slash.
    """
    policy = policy or NormalisePolicy()
    raw = url.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw

    parts = urlsplit(raw)
    host = policy.canonical_host(parts.hostname or "")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    path = unquote(parts.path or "/")
    for prefix in policy.strip_path_prefixes:
        if path.lower().startswith(prefix.lower()):
            path = path[len(prefix) :] or "/"
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k in policy.keep_params or k.lower() not in policy.strip_params
    ]
    kept.sort()

    # Scheme is dropped, not normalised: http and https links to the same
    # document are the same document, and treating them as different is the
    # single most common cause of a URL that "cannot be found".
    return urlunsplit(("", host, path, urlencode(kept), ""))


def extract_urls(text: str) -> list[str]:
    """Pull URLs out of pasted text (CNT-URL-08).

    People paste from chat threads and email. Requiring a clean list means they
    clean it by hand first, which is work the machine can do.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_IN_TEXT.finditer(text or ""):
        url = match.group(0).rstrip(".,;:)]}>\"'")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    kb_id: str
    title: str
    url: str
    rung: Rung
    #: 0..1 within the rung — a title match on an exact string scores higher
    #: than one on a fuzzy slug. Multiplied by the rung's ceiling.
    rung_score: float = 1.0

    @property
    def score(self) -> float:
        return round(RUNG_CONFIDENCE[self.rung] * max(0.0, min(1.0, self.rung_score)), 4)


class Resolution(BaseModel):
    """What one pasted URL turned into."""

    url: str
    normalised: str
    outcome: Outcome
    match: Candidate | None = None
    candidates: tuple[Candidate, ...] = ()
    detail: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.outcome is not Outcome.RESOLVED


def decide(url: str, normalised: str, candidates: list[Candidate]) -> Resolution:
    """Turn candidates into an outcome.

    The tempting behaviour is to take the best candidate whatever its score. A
    URL that resolves to the *wrong* document is worse than one that resolves to
    nothing: the collection looks complete, the answer cites a real document,
    and nobody can tell it is the wrong one.
    """
    if not candidates:
        return Resolution(
            url=url,
            normalised=normalised,
            outcome=Outcome.UNRESOLVED,
            detail="no document in the index matches this URL",
        )

    ranked = sorted(candidates, key=lambda c: (-c.score, c.doc_id))
    best = ranked[0]

    if best.score >= ACCEPT_SCORE:
        # A tie at the top is ambiguity, not a match — two documents claiming
        # the same URL is a fact about the corpus worth surfacing.
        rivals = [c for c in ranked[1:] if abs(c.score - best.score) < 1e-6]
        if rivals:
            return Resolution(
                url=url,
                normalised=normalised,
                outcome=Outcome.AMBIGUOUS,
                candidates=tuple(ranked[:5]),
                detail=f"{len(rivals) + 1} documents match this URL equally well",
            )
        return Resolution(
            url=url, normalised=normalised, outcome=Outcome.RESOLVED, match=best,
            candidates=tuple(ranked[:5]),
        )

    if best.score >= FLOOR_SCORE:
        return Resolution(
            url=url,
            normalised=normalised,
            outcome=Outcome.AMBIGUOUS,
            candidates=tuple(ranked[:5]),
            detail=f"best match is {best.rung} at {best.score:.2f}; below the "
            f"{ACCEPT_SCORE:.2f} accept threshold",
        )

    return Resolution(
        url=url,
        normalised=normalised,
        outcome=Outcome.UNRESOLVED,
        candidates=tuple(ranked[:3]),
        detail=f"best match scored {best.score:.2f}, below the {FLOOR_SCORE:.2f} floor",
    )


class ResolutionSummary(BaseModel):
    resolved: int = 0
    ambiguous: int = 0
    unresolved: int = 0
    results: tuple[Resolution, ...] = ()

    @property
    def needs_review(self) -> int:
        return self.ambiguous + self.unresolved

    def line(self) -> str:
        return (
            f"{self.resolved} resolved, {self.ambiguous} need a choice, "
            f"{self.unresolved} not found"
        )
