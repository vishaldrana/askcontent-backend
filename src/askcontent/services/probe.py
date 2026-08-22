"""The registration probe (CNT-ADM-07).

Five ordered checks, each with a specific remediation naming what to change and
who to ask. Never a bare "connection failed" — that screen is where onboarding
succeeds or fails.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..domain.documents import DocRef
from ..ports.content_index import IndexFilters, IndexUnavailable
from ..ports.content_repository import RepositoryUnavailable, ResolutionOutcome
from .mapping import validate_map
from .registry import Connector


class Check(BaseModel):
    number: int
    name: str
    passed: bool
    detail: str
    remediation: str = ""


class ProbeResult(BaseModel):
    checks: tuple[Check, ...]
    resolution_rate: float | None = None
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def probe(index, repository, connector: Connector, principal: str, sample_size: int = 8) -> ProbeResult:
    checks: list[Check] = []
    failures: list[str] = []
    resolution_rate: float | None = None

    # ① reachability -------------------------------------------------------
    try:
        knowledgebases = index.list_knowledgebases()
        checks.append(Check(
            number=1, name="PGP reachable and credential valid", passed=True,
            detail=f"{len(knowledgebases)} knowledgebases visible",
        ))
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(checks=(Check(
            number=1, name="PGP reachable and credential valid", passed=False,
            detail=str(exc),
            remediation="Check PGP_BASE and the platform service credential. If the "
                        "credential is valid elsewhere, ask the PGP owners whether "
                        "this deployment's egress is allowed.",
        ),))

    # ② knowledgebase visible and non-empty --------------------------------
    descriptor = next((k for k in knowledgebases if k.kb_id == connector.kb_id), None)
    if descriptor is None:
        checks.append(Check(
            number=2, name="Knowledgebase visible", passed=False,
            detail=f"{connector.kb_id} is not visible to this credential",
            remediation="Either the identifier is wrong, or the platform credential "
                        "has no grant on it. These look identical from here — ask the "
                        "knowledgebase owner to confirm the grant before changing the id.",
        ))
        return ProbeResult(checks=tuple(checks))

    checks.append(Check(
        number=2, name="Knowledgebase visible and non-empty", passed=descriptor.document_count > 0,
        detail=f"{descriptor.document_count} documents, last indexed "
               f"{descriptor.last_indexed_at:%d %b %Y %H:%M}" if descriptor.last_indexed_at
               else f"{descriptor.document_count} documents, never indexed",
        remediation="" if descriptor.document_count else
                    "The knowledgebase exists but holds nothing. Confirm with its owner "
                    "whether indexing has ever run; an empty KB and a KB we cannot read "
                    "are different problems.",
    ))

    # ③ sample search returns hits ----------------------------------------
    try:
        page = index.search(connector.kb_id, descriptor.name, IndexFilters(), k=sample_size)
        hits = list(page.hits)
        checks.append(Check(
            number=3, name="Sample search returns hits", passed=bool(hits),
            detail=f"{len(hits)} hits for a sample query",
            remediation="" if hits else
                        "Indexed but not queryable as configured. Confirm the query mode "
                        "PGP expects for this KB (text vs. pre-computed vector).",
        ))
    except IndexUnavailable as exc:
        checks.append(Check(
            number=3, name="Sample search returns hits", passed=False, detail=str(exc),
            remediation="Search failed while listing succeeded — usually a per-KB grant, "
                        "not a credential problem.",
        ))
        hits = []

    # ④ sample hits resolve in the ECM ------------------------------------
    # The highest-value check: a KB can pass everything else and be useless
    # because PGP indexed it from a source the ECM no longer serves.
    if hits:
        resolved = 0
        for hit in hits:
            ref = DocRef(doc_id=hit.doc_id, kb_id=connector.kb_id)
            try:
                outcome = repository.fetch_metadata(ref, principal).outcome
            except RepositoryUnavailable as exc:
                outcome = ResolutionOutcome.UNAVAILABLE
                failures.append(f"{hit.doc_id}: unavailable ({exc})")
                continue
            if outcome is ResolutionOutcome.RESOLVED:
                resolved += 1
            else:
                failures.append(f"{hit.doc_id}: {outcome}")

        resolution_rate = resolved / len(hits)
        checks.append(Check(
            number=4, name="Sample hits resolve in the ECM",
            passed=resolution_rate >= 0.8,
            detail=f"{resolved}/{len(hits)} resolved ({resolution_rate:.0%})",
            remediation="" if resolution_rate >= 0.8 else
                        "The index and the store disagree. not_found means PGP's sync is "
                        "stale or it indexed from a different source; forbidden means the "
                        "probing principal lacks grants that most askers will also lack. "
                        "Take this to the PGP and ECM owners together — neither can "
                        "diagnose it alone.",
        ))

    # ⑤ required mapped fields meet coverage -------------------------------
    sample_rows = [hit.metadata for hit in index.list_documents(connector.kb_id).hits]
    validation = validate_map(connector.field_map, sample_rows)
    checks.append(Check(
        number=5, name="Required mapped fields meet coverage",
        passed=validation.can_activate,
        detail="; ".join(f"{f.target} {f.coverage:.0%}" for f in validation.fields) or "no rules",
        remediation="" if validation.can_activate else "; ".join(validation.blocking),
    ))

    return ProbeResult(
        checks=tuple(checks),
        resolution_rate=resolution_rate,
        failures=tuple(failures[:10]),
    )
