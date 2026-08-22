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

    return Platform(
        index=index, repository=repository, embedder=embedder, reranker=reranker,
        passages=passages, retrieval=retrieval, registry=registry,
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
    """Three connectors over the same PGP instance, owned by three business
    groups, with three different scopes (CNT-CON-02, CNT-CON-06).

    Note that none of them shares a field map: that is the point.
    """
    reranker_id = getattr(platform.reranker, "reranker_id", "lexical-deterministic")
    floor = getattr(platform.reranker, "score_floor", 0.08)

    def config(**overrides) -> RetrievalConfig:
        return RetrievalConfig(reranker_id=reranker_id, rerank_floor=floor, **overrides)

    # -- People Operations ------------------------------------------------
    hr_map = suggest_map("kb-hr-policies", [
        f.name for f in platform.index.describe("kb-hr-policies").fields
    ])
    hr_map = hr_map.model_copy(update={
        "rules": tuple(
            r.model_copy(update={"value_map": _SENSITIVITY_MAP})
            if r.target == "sensitivity" else r
            for r in hr_map.rules
        )
    })
    platform.registry.put(
        Connector(
            connector_id="cn-people-ops",
            name="People Operations — HR policy library",
            business_group="People Operations",
            kb_id="kb-hr-policies",
            field_map=hr_map,
            scope=KnowledgeScope(
                roots=(SourceRoot(kind="space", value="HR"),),
                exclude=("/hr/archive/*",),
                sensitivity_ceiling=Sensitivity.CONFIDENTIAL,
            ),
            access=AccessBinding(groups=("group:all-staff",)),
            retrieval=config(),
            authority_rules=(
                AuthorityRule(label="approved", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(path_prefix="/hr/archive", tier=AuthorityTier.ARCHIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        actor="seed",
    )

    # -- Platform engineering ---------------------------------------------
    eng_map = suggest_map("kb-eng-runbooks", [
        f.name for f in platform.index.describe("kb-eng-runbooks").fields
    ])
    eng_map = eng_map.model_copy(update={
        "rules": tuple(
            r.model_copy(update={"coercion": Coercion.DATE_EPOCH})
            if r.target == "updated_at" else
            r.model_copy(update={"coercion": Coercion.STRING_LIST})
            if r.target == "labels" else r
            for r in eng_map.rules
        )
    })
    platform.registry.put(
        Connector(
            connector_id="cn-platform-eng",
            name="Platform Engineering — runbooks and decisions",
            business_group="Platform Engineering",
            kb_id="kb-eng-runbooks",
            field_map=eng_map,
            scope=KnowledgeScope(
                roots=(SourceRoot(kind="space", value="ENG"),),
                exclude=("/eng/archive/*",),
                sensitivity_ceiling=Sensitivity.INTERNAL,
            ),
            access=AccessBinding(groups=("group:engineering",)),
            retrieval=config(),
            authority_rules=(
                AuthorityRule(label="runbook", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(label="adr", tier=AuthorityTier.AUTHORITATIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        actor="seed",
    )

    # -- Finance ----------------------------------------------------------
    fin_map = suggest_map("kb-fin-controls", [
        f.name for f in platform.index.describe("kb-fin-controls").fields
    ])
    fin_map = fin_map.model_copy(update={
        "rules": tuple(
            r.model_copy(update={"coercion": Coercion.DATE_DMY})
            if r.target == "updated_at" else
            r.model_copy(update={"value_map": _SENSITIVITY_MAP})
            if r.target == "sensitivity" else r
            for r in fin_map.rules
        )
    })
    platform.registry.put(
        Connector(
            connector_id="cn-finance",
            name="Finance — controls and customer policy",
            business_group="Finance",
            kb_id="kb-fin-controls",
            field_map=fin_map,
            scope=KnowledgeScope(
                roots=(SourceRoot(kind="space", value="FIN"),),
                sensitivity_ceiling=Sensitivity.CONFIDENTIAL,
            ),
            access=AccessBinding(groups=("group:finance",)),
            retrieval=config(),
            authority_rules=(
                AuthorityRule(label="approved", tier=AuthorityTier.AUTHORITATIVE),
                AuthorityRule(label="sox", tier=AuthorityTier.AUTHORITATIVE),
            ),
            state=ConnectorState.ACTIVE,
        ),
        actor="seed",
    )

    # -- Public web: no ACL fields at all ---------------------------------
    # Registered as a draft on purpose: it cannot be activated until an access
    # class is declared (CNT-ACL-03).
    web_map = suggest_map("kb-marketing-web", [
        f.name for f in platform.index.describe("kb-marketing-web").fields
    ])
    platform.registry.put(
        Connector(
            connector_id="cn-public-web",
            name="Public Web — trust and legal pages",
            business_group="Marketing",
            kb_id="kb-marketing-web",
            field_map=web_map,
            scope=KnowledgeScope(
                roots=(SourceRoot(kind="space", value="WEB"),),
                sensitivity_ceiling=Sensitivity.PUBLIC,
            ),
            access=AccessBinding(groups=("group:all-staff",)),
            retrieval=config(),
            state=ConnectorState.DRAFT,
        ),
        actor="seed",
    )


_SENSITIVITY_MAP = {
    "Public": "public",
    "Internal Use Only": "internal",
    "Confidential": "confidential",
    "Restricted": "restricted",
}
