"""Composition root.

The only module that knows which adapters exist. Everything else depends on
ports (CNT-FED-05) — a test asserts the services layer imports no adapter.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .adapters.embedders import build_embedder
from .adapters.embedders.hashing import HashingEmbedder
from .adapters.index.mock_pgp import MockPgpIndex
from .adapters.repository.mock_ecm import MockEcmRepository
from .adapters.rerankers.lexical import LexicalReranker
from .adapters.answerers import build_answerer
from .domain.catalog import AuthorityRule, FreshnessPolicy
from .services.answering import AnsweringService
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


def build_reranker(embedder=None):
    """Cross-encoder when the runtime is installed and enabled, deterministic
    otherwise (CNT-RNK-03).

    ASKCONTENT_RERANKER=cross-encoder switches it on; the model is expected to
    be present in the image rather than downloaded at boot.
    """
    from .config import settings

    choice = os.environ.get("ASKCONTENT_RERANKER", "auto")

    # Preference order, best first:
    #   cross-encoder   trained for this judgement, local, deterministic
    #   llm             TEMPORARY — a model call per query; see rerankers/llm.py
    #   embedding       bi-encoder; cannot attend across the pair
    #   lexical         word overlap; loses whenever the wording differs
    if choice in ("auto", "cross-encoder"):
        try:
            import sentence_transformers  # noqa: F401

            from .adapters.rerankers.cross_encoder import CrossEncoderReranker

            model = os.environ.get("ASKCONTENT_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
            logger.info("reranker: cross-encoder %s", model)
            # Installing the runtime is enough to select this path, so the
            # model has to be present too — otherwise `auto` silently starts a
            # multi-gigabyte download inside somebody's first question, which
            # is the failure the adapter's own deployment note warns about.
            # Better to say so at boot than to hang at request time.
            return CrossEncoderReranker(model)
        except ImportError:
            if choice == "cross-encoder":
                raise

    if choice in ("auto", "llm") and settings.llm_api_key:
        from .adapters.rerankers.embedding import EmbeddingReranker
        from .adapters.rerankers.llm import LlmReranker

        try:
            return LlmReranker(
                model=os.environ.get("ASKCONTENT_RERANK_MODEL", "gpt-4.1-mini"),
                api_key=settings.llm_api_key,
                # If the model is unreachable mid-query, ranking degrades to
                # the bi-encoder rather than collapsing to insertion order.
                fallback=(
                    EmbeddingReranker(embedder)
                    if embedder is not None
                    and getattr(embedder, "model_id", "") != "hashing-ngram-v1"
                    else None
                ),
            )
        except Exception:  # noqa: BLE001
            if choice == "llm":
                raise
            logger.warning("reranker: llm unavailable, falling back")

    if choice in ("auto", "embedding") and embedder is not None:
        if getattr(embedder, "model_id", "") != "hashing-ngram-v1":
            from .adapters.rerankers.embedding import EmbeddingReranker

            logger.info("reranker: embedding bi-encoder (%s)", embedder.model_id)
            return EmbeddingReranker(embedder)
        if choice == "embedding":
            raise RuntimeError(
                "ASKCONTENT_RERANKER=embedding needs a real embedding model; "
                "the hashed n-gram bag cannot rank semantically"
            )

    logger.info(
        "reranker: deterministic lexical fallback — it scores word overlap, so "
        "a question worded differently from the document will rank badly"
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

    embedder = build_embedder()
    reranker = build_reranker(embedder)
    # The stub search service gets the cross-encoder, because that is where the
    # real one lives: inside the index, asked for with a flag on the query. The
    # same object is still handed to retrieval as a fallback, for indexes that
    # cannot rank their own fragments.
    index = PgPgpIndex(engine, embedder, reranker=reranker)
    repository = PgEcmRepository(engine)
    reranker = build_reranker(embedder)
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
        answering=AnsweringService(build_answerer()),
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

    #: The vector is joined in, not decoration. Without it every chunk arrives
    #: unembedded and passage selection re-embeds all of them at query time —
    #: against a hosted model that was six to twelve seconds per question,
    #: spent recomputing vectors already stored at index time.
    #:
    #: The join is LEFT: a chunk written before its embedding lands is still a
    #: usable passage, and dropping it would make an indexing race look like a
    #: missing document.
    _COLUMNS = """
        d.doc_id, c.chunk_id, c.ordinal, c.text, c.heading_path,
        c.parent_text, c.page, c.is_table, e.vector
    """
    _JOIN = """
        JOIN {schema}.document d ON d.id = c.document_id
        LEFT JOIN {schema}.embedding e
               ON e.connector_id = c.connector_id
              AND e.kind = 'chunk'
              AND e.ref_id = c.chunk_id
    """

    def _chunk(self, row):
        from .domain.chunks import Chunk

        return Chunk(
            chunk_id=row["chunk_id"], doc_id=row["doc_id"], text=row["text"],
            heading_path=tuple(row["heading_path"] or ()), ordinal=row["ordinal"],
            page=row["page"], is_table=row["is_table"],
            parent_text=row["parent_text"] or "",
            vector=_as_vector(row["vector"]),
        )

    def load(self, doc_id: str):
        from sqlalchemy import text

        from .config import settings

        schema = settings.db_schema
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT {self._COLUMNS}
                    FROM {schema}.document_chunk c
                    {self._JOIN.format(schema=schema)}
                    WHERE c.org_id = :org AND d.doc_id = :doc
                    ORDER BY c.ordinal
                    """
                ),
                {"org": self._org, "doc": doc_id},
            ).mappings().all()
        if not rows:
            return None
        return tuple(self._chunk(r) for r in rows)

    def load_many(self, doc_ids: list[str]) -> dict:
        """Every candidate's chunks in one query.

        One query per document is the same statement twenty times, and against
        a database a network away each is a round trip inside the reader's
        wait. The set is bounded by the candidate count, so a single `ANY` is
        safe.
        """
        from sqlalchemy import text

        from .config import settings

        if not doc_ids:
            return {}

        schema = settings.db_schema
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT {self._COLUMNS}
                    FROM {schema}.document_chunk c
                    {self._JOIN.format(schema=schema)}
                    WHERE c.org_id = :org AND d.doc_id = ANY(:docs)
                    ORDER BY d.doc_id, c.ordinal
                    """
                ),
                {"org": self._org, "docs": list(doc_ids)},
            ).mappings().all()

        out: dict[str, list] = {}
        for row in rows:
            out.setdefault(row["doc_id"], []).append(self._chunk(row))
        return {doc: tuple(chunks) for doc, chunks in out.items()}


def _as_vector(raw) -> list[float] | None:
    """pgvector over a raw `text()` query comes back as `'[0.1,0.2,…]'`.

    The driver only knows to hand back a list when the column is typed, and a
    hand-written SELECT carries no type information — so `list(raw)` silently
    produces a list of *characters*, which fails validation a thousand
    elements later with a message about a comma.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        body = raw.strip().lstrip("[").rstrip("]")
        return [float(part) for part in body.split(",")] if body else None
    return list(raw)


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
    embedder: object
    reranker: object
    passages: PassageService
    retrieval: RetrievalService
    registry: Registry
    answering: AnsweringService


def build(*, simulate_latency: bool = True, failure_rate: float = 0.0) -> Platform:
    index = MockPgpIndex(simulate_latency=simulate_latency, failure_rate=failure_rate)
    repository = MockEcmRepository(simulate_latency=simulate_latency, failure_rate=failure_rate)
    embedder = HashingEmbedder()
    reranker = build_reranker(embedder)
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
        answering=AnsweringService(build_answerer()),
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
            # Reference documentation ages differently from a policy library.
            # A page describing how to add a hyperlink was written once and is
            # still correct; the default three-year expiry archived 32 of 34
            # candidates for one question and answered from the two survivors,
            # which is worse than saying nothing. Ageing still flags them.
            retrieval={"freshness": FreshnessPolicy(
                ageing_days=540, stale_days=1095, expired_days=3650
            )},
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
                retrieval=config(**seed.get("retrieval", {})),
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
