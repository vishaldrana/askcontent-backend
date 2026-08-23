"""Composition root.

The only module that knows which adapters exist. Everything else depends on
ports (CNT-FED-05) — a test asserts the services layer imports no adapter.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .adapters.embedders.hashing import HashingEmbedder
from .adapters.index.mock_pgp import MockPgpIndex
from .adapters.repository.mock_ecm import MockEcmRepository
from .adapters.rerankers.lexical import LexicalReranker
from .domain.catalog import AuthorityRule
from .domain.documents import AuthorityTier, Sensitivity
from .domain.scope import KnowledgeScope, SourceRoot
from .services.mapping import Coercion, FieldRule, suggest_map
from .services.passages import PassageService
from .services.registry import (
    AccessBinding,
    Connector,
    ConnectorState,
    Registry,
    RetrievalConfig,
)
from .services.retrieval import RetrievalService

logger = logging.getLogger(__name__)


def build_reranker():
    """Cross-encoder when the runtime is installed and enabled, deterministic
    otherwise (CNT-RNK-03).

    ASKCONTENT_RERANKER=cross-encoder switches it on; the model is expected to
    be present in the image rather than downloaded at boot.
    """
    choice = os.environ.get("ASKCONTENT_RERANKER", "auto")
    if choice in ("auto", "cross-encoder"):
        try:
            import sentence_transformers  # noqa: F401

            from .adapters.rerankers.cross_encoder import CrossEncoderReranker

            model = os.environ.get("ASKCONTENT_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
            logger.info("reranker: cross-encoder %s", model)
            return CrossEncoderReranker(model)
        except ImportError:
            if choice == "cross-encoder":
                raise
            logger.info(
                "reranker: sentence-transformers absent, using the deterministic "
                "lexical reranker (install the 'rerank' extra for the real one)"
            )
    return LexicalReranker()


def build_postgres(org_slug: str = "demo") -> "Platform":
    """The real thing: Postgres-backed index, repository and registry.

    Same ports, same services, same pipeline. The only difference from `build()`
    is which adapters are constructed — which is the property the port split
    exists to give us, and it is worth checking that it actually held.
    """
    from .adapters.index.pg_pgp import PgPgpIndex
    from .adapters.repository.pg_ecm import PgEcmRepository
    from .db.session import get_engine, get_session_factory
    from .services.pg_registry import PgRegistry

    engine = get_engine()
    sessions = get_session_factory()

    embedder = HashingEmbedder()
    index = PgPgpIndex(engine, embedder)
    repository = PgEcmRepository(engine)
    reranker = build_reranker()
    passages = PassageService(repository, embedder, sandbox=False)
    retrieval = RetrievalService(index, repository, embedder, reranker, passages)

    org_id = _ensure_org(sessions, org_slug)
    registry = PgRegistry(sessions, org_id)

    # Passage recovery reads our own indexed chunks first. Without this the
    # index is a store nothing reads: every question would still fetch, parse
    # and chunk each candidate from scratch.
    from .services.passages import StoredPassages

    passages.stored = _StoredPassagesRouter(engine, sessions, org_id)

    return Platform(
        index=index, repository=repository, embedder=embedder, reranker=reranker,
        passages=passages, retrieval=retrieval, registry=registry,
    )


class _StoredPassagesRouter:
    """Resolves a document to the chunks of whichever connector indexed it.

    Passage recovery is per question and the connector is known then, but the
    service is built once — so the lookup is by document, scoped to the
    organisation, and the connector filter is applied in the query.
    """

    def __init__(self, engine, sessions, org_id) -> None:
        self._engine = engine
        self._sessions = sessions
        self._org = org_id

    def load(self, doc_id: str):
        from sqlalchemy import text

        from .config import settings
        from .domain.chunks import Chunk

        schema = settings.db_schema
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT c.chunk_id, c.ordinal, c.text, c.heading_path,
                           c.parent_text, c.page, c.is_table
                    FROM {schema}.document_chunk c
                    JOIN {schema}.document d ON d.id = c.document_id
                    WHERE c.org_id = :org AND d.doc_id = :doc
                    ORDER BY c.ordinal
                    """
                ),
                {"org": self._org, "doc": doc_id},
            ).mappings().all()
        if not rows:
            return None
        return tuple(
            Chunk(
                chunk_id=r["chunk_id"], doc_id=doc_id, text=r["text"],
                heading_path=tuple(r["heading_path"] or ()), ordinal=r["ordinal"],
                page=r["page"], is_table=r["is_table"],
                parent_text=r["parent_text"] or "",
            )
            for r in rows
        )


def _ensure_org(sessions, slug: str):
    from sqlalchemy import select

    from .db import models as m

    with sessions() as session:
        org = session.scalars(select(m.Org).where(m.Org.slug == slug)).one_or_none()
        if org is None:
            org = m.Org(slug=slug, name=slug.title())
            session.add(org)
            session.commit()
        return org.id


@dataclass
class Platform:
    index: object
    repository: object
    embedder: HashingEmbedder
    reranker: object
    passages: PassageService
    retrieval: RetrievalService
    registry: Registry


def build(*, simulate_latency: bool = True, failure_rate: float = 0.0) -> Platform:
    index = MockPgpIndex(simulate_latency=simulate_latency, failure_rate=failure_rate)
    repository = MockEcmRepository(simulate_latency=simulate_latency, failure_rate=failure_rate)
    embedder = HashingEmbedder()
    reranker = build_reranker()
    passages = PassageService(repository, embedder, sandbox=False)
    retrieval = RetrievalService(index, repository, embedder, reranker, passages)
    registry = Registry()

    platform = Platform(
        index=index,
        repository=repository,
        embedder=embedder,
        reranker=reranker,
        passages=passages,
        retrieval=retrieval,
        registry=registry,
    )
    _seed_connectors(platform)
    return platform


def _seed_connectors(platform: Platform) -> None:
    """Five connectors over one index, owned by five business groups.

    None of them shares a field map, and that is the point: the same concept is
    spelled differently in every knowledgebase, and the platform never learns
    those names.
    """
    reranker_id = getattr(platform.reranker, "reranker_id", "lexical-deterministic")
    floor = getattr(platform.reranker, "score_floor", 0.08)

    def config(**overrides) -> RetrievalConfig:
        return RetrievalConfig(reranker_id=reranker_id, rerank_floor=floor, **overrides)

    def field_map(kb_id: str, **coercions):
        descriptor = platform.index.describe(kb_id)
        mapped = suggest_map(kb_id, [f.name for f in descriptor.fields])
        rules = []
        for rule in mapped.rules:
            if rule.target in coercions:
                rule = rule.model_copy(update={"coercion": coercions[rule.target]})
            if rule.target == "sensitivity":
                rule = rule.model_copy(update={"value_map": _SENSITIVITY_MAP})
            rules.append(rule)
        return mapped.model_copy(update={"rules": tuple(rules)})

    seeds = [
        dict(
            connector_id="cn-consumer-banking",
            name="Consumer Banking — deposit policy and disclosures",
            business_group="Consumer Banking",
            kb_id="kb-consumer-policy", space="CONSUMER",
            exclude=("/consumer/archive/*",),
            groups=("group:all-staff",),
            ceiling=Sensitivity.CONFIDENTIAL,
            authority=(
                AuthorityRule(label="approved", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(path_prefix="/consumer/archive", tier=AuthorityTier.ARCHIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        dict(
            connector_id="cn-payments-ops",
            name="Payments Operations — runbooks and decisions",
            business_group="Payments Operations",
            kb_id="kb-ops-runbooks", space="OPS",
            exclude=("/ops/archive/*",),
            groups=("group:payments-ops",),
            ceiling=Sensitivity.INTERNAL,
            authority=(
                AuthorityRule(label="runbook", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(label="adr", tier=AuthorityTier.AUTHORITATIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        dict(
            connector_id="cn-risk-compliance",
            name="Risk and Compliance — controls and customer policy",
            business_group="Risk and Compliance",
            kb_id="kb-risk-controls", space="RISK",
            exclude=(),
            groups=("group:financial-crimes", "group:audit"),
            ceiling=Sensitivity.CONFIDENTIAL,
            authority=(
                AuthorityRule(label="approved", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(label="sox", tier=AuthorityTier.AUTHORITATIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        dict(
            # The demonstration connector: two document classes on one subject,
            # a planted disagreement between them, and a scanned specimen that
            # must be refused rather than half-read.
            connector_id="cn-legal-poa",
            name="Power of Attorney — state guidelines and internal procedure",
            business_group="Fiduciary Services",
            kb_id="kb-poa", space="POA",
            exclude=(),
            groups=("group:all-staff",),
            ceiling=Sensitivity.INTERNAL,
            authority=(
                AuthorityRule(label="state-guideline", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(label="internal-procedure", tier=AuthorityTier.AUTHORITATIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        dict(
            connector_id="cn-engineering",
            name="Engineering — platform documentation",
            business_group="Platform Engineering",
            kb_id="kb-techdocs", space="ENG",
            exclude=(),
            groups=("group:engineering",),
            ceiling=Sensitivity.INTERNAL,
            authority=(
                AuthorityRule(label="approved", tier=AuthorityTier.AUTHORITATIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        dict(
            # Built by crawling a real public help centre rather than seeded
            # from a fixture: the pages, their titles and their dates all came
            # off help.qwary.com. It is here to prove the whole path — crawl,
            # publish, index, answer — against content nobody wrote for us.
            connector_id="cn-qwary-help",
            name="Qwary Help Centre — product documentation",
            business_group="Customer Support",
            kb_id="kb-qwary-help", space="QWARY_HELP",
            exclude=(),
            groups=("group:all-staff",),
            ceiling=Sensitivity.PUBLIC,
            authority=(),
            state=ConnectorState.ACTIVE,
        ),
        dict(
            connector_id="cn-public-web",
            name="Public Web — customer-facing help pages",
            business_group="Digital Content",
            kb_id="kb-public-web", space="WEB",
            exclude=(),
            groups=("group:all-staff",),
            ceiling=Sensitivity.PUBLIC,
            authority=(),
            state=ConnectorState.DRAFT,
        ),
    ]

    coercion_by_kb = {
        "kb-ops-runbooks": {"updated_at": Coercion.DATE_EPOCH, "labels": Coercion.STRING_LIST},
        "kb-risk-controls": {"updated_at": Coercion.DATE_DMY},
    }

    available = {d.kb_id for d in platform.index.list_knowledgebases()}
    for seed in seeds:
        if seed["kb_id"] not in available:
            # A connector over a knowledgebase this index does not have cannot
            # be given a field map, because there are no fields to map. Skip it
            # rather than fail: the seeds describe several corpora and not every
            # deployment carries all of them — the crawled ones exist only once
            # something has actually been crawled.
            continue
        platform.registry.put(
            Connector(
                connector_id=seed["connector_id"],
                name=seed["name"],
                business_group=seed["business_group"],
                kb_id=seed["kb_id"],
                field_map=field_map(seed["kb_id"], **coercion_by_kb.get(seed["kb_id"], {})),
                scope=KnowledgeScope(
                    roots=(SourceRoot(kind="space", value=seed["space"]),),
                    exclude=seed["exclude"],
                    sensitivity_ceiling=seed["ceiling"],
                ),
                access=AccessBinding(groups=seed["groups"]),
                retrieval=config(),
                authority_rules=seed["authority"],
                state=seed["state"],
            ),
            actor="seed",
        )


_SENSITIVITY_MAP = {
    "Public": "public",
    "Internal Use Only": "internal",
    "Confidential": "confidential",
    "Restricted": "restricted",
}
