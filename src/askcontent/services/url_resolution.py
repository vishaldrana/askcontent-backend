"""Resolving pasted URLs to documents that already exist in the index.

The whole point is that nothing is fetched. A URL is a handle a person happens
to have; the document behind it is already indexed, and the job is to say which
one — or to say honestly that we cannot tell.
"""

from __future__ import annotations

from ..domain.urls import (
    Candidate,
    HostAlias,
    NormalisePolicy,
    Resolution,
    ResolutionSummary,
    Rung,
    decide,
    extract_urls,
    normalise,
)

#: The deployment's URL folding. In production this is configuration; here it
#: encodes the alternate forms the fixture's platform serves — a viewer host, a
#: document-id permalink and a short link — because those are what people paste.
DEFAULT_POLICY = NormalisePolicy(
    host_aliases=(
        HostAlias(
            canonical="ecm.example.com",
            aliases=("docs.example.com", "go.example.com", "intranet.example.com"),
        ),
    ),
    strip_path_prefixes=("/:w:/r", "/:x:/r", "/:b:/r"),
)


class UrlResolutionService:
    def __init__(self, index, policy: NormalisePolicy | None = None) -> None:
        self.index = index
        self.policy = policy or DEFAULT_POLICY

    def resolve_text(self, text: str, kb_ids: tuple[str, ...] = ()) -> ResolutionSummary:
        """Accept a bare list or prose containing links (CNT-URL-08).

        People paste from chat threads and tickets. Requiring a clean list means
        they clean it by hand first, which is work the machine can do.
        """
        urls = extract_urls(text)
        if not urls:
            urls = [line.strip() for line in (text or "").splitlines() if line.strip()]
        return self.resolve(urls, kb_ids)

    def resolve(self, urls: list[str], kb_ids: tuple[str, ...] = ()) -> ResolutionSummary:
        results: list[Resolution] = []
        raw = self.index.resolve_urls(urls, kb_ids)

        for url in urls:
            key = normalise(url, self.policy)
            candidates = [
                Candidate.model_validate(c) if not isinstance(c, Candidate) else c
                for c in raw.get(url, [])
            ]
            results.append(decide(url, key, candidates))

        return ResolutionSummary(
            resolved=sum(1 for r in results if r.outcome == "resolved"),
            ambiguous=sum(1 for r in results if r.outcome == "ambiguous"),
            unresolved=sum(1 for r in results if r.outcome == "unresolved"),
            results=tuple(results),
        )


__all__ = ["UrlResolutionService", "DEFAULT_POLICY", "Rung"]
