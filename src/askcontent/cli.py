"""Operational commands.

    python -m askcontent.cli seed        register the fixture knowledgebases
                                         and connectors in the database
    python -m askcontent.cli index       build chunks and vectors (incremental)
    python -m askcontent.cli status      what is actually deployed
    python -m askcontent.cli ask "..."   one question, end to end
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from .bootstrap import build_postgres
from .db import models as m
from .db.session import get_session_factory, healthcheck
from .domain.retrieval_spec import Intent, RetrievalSpec


def seed() -> int:
    """Register every knowledgebase the index can see, then create one
    connector per business group from the same definitions the mock uses.

    Idempotent: re-running updates rather than duplicating, which is the same
    'create is update' discipline the catalog uses.
    """
    from .bootstrap import _seed_connectors

    platform = build_postgres()
    sessions = get_session_factory()
    org_id = platform.registry.org_id

    with sessions() as session:
        for descriptor in platform.index.list_knowledgebases():
            row = session.scalars(
                select(m.Knowledgebase).where(
                    m.Knowledgebase.org_id == org_id,
                    m.Knowledgebase.kb_id == descriptor.kb_id,
                )
            ).one_or_none()
            if row is None:
                row = m.Knowledgebase(org_id=org_id, kb_id=descriptor.kb_id)
                session.add(row)
            row.name = descriptor.name
            row.description = descriptor.description
            row.document_count = descriptor.document_count
            row.last_indexed_at = descriptor.last_indexed_at
            row.embedding_model = descriptor.embedding_model
            row.embedding_dimension = descriptor.embedding_dimension
            row.exposes_acl = descriptor.exposes_acl
            row.observed_fields = {
                f.name: {"coverage": f.coverage, "samples": list(f.samples)}
                for f in descriptor.fields
            }
        session.commit()

    _seed_connectors(platform)
    print(f"registered {len(platform.index.list_knowledgebases())} knowledgebases")
    for connector in platform.registry.list():
        print(f"  {connector.connector_id:<18} {connector.state:<10} {connector.kb_id}")
    return 0


def index(connector_id: str | None = None, limit: str | None = None) -> int:
    """Build our own chunk and vector index for one connector, or all of them.

    Incremental: a second run over unchanged content does no work.
    """
    from .services.indexing import IndexingService

    platform = build_postgres()
    service = IndexingService(platform, get_session_factory(), platform.registry.org_id)
    targets = (
        [platform.registry.get(connector_id)] if connector_id else platform.registry.list()
    )
    for connector in targets:
        report = service.index_connector(connector, limit=int(limit) if limit else None)
        print(" ", report.line())
        for note in report.notes[:3]:
            print("    note:", note)
    return 0


def status() -> int:
    print("database:", healthcheck())
    platform = build_postgres()
    print("reranker:", getattr(platform.reranker, "reranker_id", "?"))
    print("index:   ", type(platform.index).__name__)
    print("store:   ", type(platform.repository).__name__)
    for connector in platform.registry.list():
        print(f"  {connector.connector_id:<18} {connector.state:<10} {connector.kb_id}")
    return 0


def ask(question: str, connector_id: str = "cn-people-ops", principal: str = "user:asha") -> int:
    platform = build_postgres()
    connector = platform.registry.get(connector_id)
    spec = RetrievalSpec(
        intent=Intent.LOOKUP,
        scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
        question=question,
        channels=connector.retrieval.channels,
        k_per_channel=connector.retrieval.k_per_channel,
    )
    evidence = platform.retrieval.retrieve(connector, spec, principal)

    print(f"\nQ: {question}   [as {principal}]")
    if evidence.refused:
        print(f"REFUSED: {evidence.refusal_reason}")
    for citation in evidence.citations:
        head = " › ".join(citation.heading_path) or "—"
        print(f"  [{citation.rerank_score:.3f}] {citation.doc_id} {citation.title}")
        print(f"          {head}")
        print(f"          {citation.span[:110]!r}")
    for conflict in evidence.conflicts:
        print(f"  CONFLICT on {conflict.subject}")
        for c in conflict.citations:
            print(f"     {c.doc_id} [{c.authority}] {str(c.updated_at)[:10]}")
    for notice in evidence.notices:
        print(f"  NOTICE {notice}")
    trace = evidence.trace
    print(
        f"  -- channels={[(t.channel, t.hits) for t in trace.channels]} "
        f"stale={trace.stale_index_count} forbidden={trace.forbidden_count} "
        f"cache={trace.cache_hit_rate:.0%} {trace.total_ms:.0f}ms"
    )
    return 0


def evals() -> int:
    """Run a connector's eval suite and exit non-zero if anything fails.

    The exit code is the point. A suite that reports into a dashboard is a
    suite that goes red on a Friday and is noticed the following quarter; one
    that fails a build is one somebody fixes. This is what belongs in CI after
    any change to retrieval, chunking, the prompt or a model.

        python -m askcontent.cli evals cn-qwary-help
    """
    import sys

    from .bootstrap import build_postgres
    from .db.session import get_session_factory
    from .services.evaluation import EvaluationService

    slug = sys.argv[2] if len(sys.argv) > 2 else None
    if not slug:
        print("usage: python -m askcontent.cli evals <connector>")
        return 2

    platform = build_postgres()
    report = EvaluationService(
        platform, get_session_factory(), platform.registry.org_id
    ).run(slug)

    for result in report["results"]:
        # An errored case is neither. Printing it as FAIL would send the next
        # person looking for the change that broke it.
        mark = "ERR " if result.get("errored") else ("PASS" if result["passed"] else "FAIL")
        print(f"  {mark}  {result['elapsed_ms']:>6} ms  {result['question'][:60]}")
        for failure in result["failures"]:
            print(f"          {failure}")

    errored = sum(1 for r in report["results"] if r.get("errored"))
    print(f"\n{report['passed']}/{report['total']} passed"
          + (f", {errored} could not be run" if errored else ""))
    # Errors still fail the build. A suite that goes green because half of it
    # did not run is worse than one that goes red.
    return 1 if report["failed"] else 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    command, *rest = argv
    if command == "seed":
        return seed()
    if command == "status":
        return status()
    if command == "index":
        return index(*rest)
    if command == "ask":
        return ask(*rest)
    if command == "evals":
        return evals()
    print(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
