"""Connector registry and configuration store.

Every behaviour of the pipeline that varies per knowledgebase is configuration
data held here (CNT-ADM-02). There is no per-knowledgebase branch anywhere in
the retrieval path, and a test asserts it.

STORAGE: in-memory for the reference implementation. Production is Postgres
with row-level security per ARC-TEC-02/03 — every object below maps to one
table, and `versions` maps to a history table. The service interface does not
change.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, Field

from ..domain.catalog import AuthorityRule, FreshnessPolicy
from ..domain.documents import AuthorityTier, Sensitivity
from ..domain.retrieval_spec import Channel
from ..domain.scope import KnowledgeScope, SourceRoot
from .mapping import FieldMap


class ConnectorState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class RetrievalConfig(BaseModel):
    """Per-connector retrieval parameters (CNT-ADM-19).

    Every value carries its provenance to the UI (CNT-ADM-20): inherited
    configuration whose origin is invisible is configuration nobody dares
    change.
    """

    channels: tuple[Channel, ...] = (Channel.PGP, Channel.ECM)
    k_per_channel: int = 20
    rrf_constant: int = 60
    reranker_id: str = "lexical-deterministic"
    #: How many passages reach the reranker. Everything past it is dropped on
    #: the cheap similarity score, which is reliable about which passages are
    #: worth a careful look and unreliable about their order — so the expensive
    #: ranker is spent only where it changes the answer.
    rerank_shortlist: int = 16
    rerank_floor: float = 0.08
    max_rerank_pairs: int = 100
    passages_per_document: int = 3
    context_budget_chunks: int = 12
    diversity_by: str = "document"
    channel_timeout_seconds: float = 3.0
    fetch_timeout_seconds: float = 5.0
    freshness: FreshnessPolicy = Field(default_factory=FreshnessPolicy)


PLATFORM_DEFAULTS = RetrievalConfig()


class AccessBinding(BaseModel):
    groups: tuple[str, ...] = ()
    # Set when the source cannot answer per-document ACL questions
    # (CNT-ACL-03). The console then states that every document from this
    # connector is visible to every member of the bound groups.
    declared_access_class: str | None = None


class Connector(BaseModel):
    """A connector is four things, none optional (CNT-CON-01).

    A connector without a scope cannot be constructed — that is what makes
    scope constitutive rather than a filter bolted on later.
    """

    connector_id: str
    name: str
    business_group: str
    kb_id: str
    field_map: FieldMap
    scope: KnowledgeScope
    access: AccessBinding
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    authority_rules: tuple[AuthorityRule, ...] = ()
    authority_pins: dict[str, AuthorityTier] = Field(default_factory=dict)
    state: ConnectorState = ConnectorState.DRAFT
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime(2026, 8, 22))
    version: int = 1


class AuditEntry(BaseModel):
    at: dt.datetime
    actor: str
    action: str
    connector_id: str | None = None
    detail: dict[str, object] = Field(default_factory=dict)


class ConfigVersion(BaseModel):
    version: int
    at: dt.datetime
    actor: str
    snapshot: Connector
    note: str = ""


class Registry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}
        self._versions: dict[str, list[ConfigVersion]] = {}
        self.audit: list[AuditEntry] = []

    # -- CRUD --------------------------------------------------------------

    def put(self, connector: Connector, actor: str, note: str = "") -> Connector:
        existing = self._connectors.get(connector.connector_id)
        if existing is not None:
            connector = connector.model_copy(update={"version": existing.version + 1})
        self._connectors[connector.connector_id] = connector
        self._versions.setdefault(connector.connector_id, []).append(
            ConfigVersion(
                version=connector.version,
                at=dt.datetime(2026, 8, 22, 12, 0, 0),
                actor=actor,
                snapshot=connector,
                note=note,
            )
        )
        self._log(actor, "connector.put", connector.connector_id, {"version": connector.version, "note": note})
        return connector

    def get(self, connector_id: str) -> Connector:
        try:
            return self._connectors[connector_id]
        except KeyError:
            raise KeyError(f"unknown connector: {connector_id}") from None

    def list(self) -> list[Connector]:
        return sorted(self._connectors.values(), key=lambda c: c.name)

    def versions(self, connector_id: str) -> list[ConfigVersion]:
        return list(self._versions.get(connector_id, []))

    def revert(self, connector_id: str, version: int, actor: str) -> Connector:
        """One action, any prior version (CNT-ADM-16)."""
        for entry in self._versions.get(connector_id, []):
            if entry.version == version:
                return self.put(entry.snapshot, actor, note=f"revert to v{version}")
        raise KeyError(f"no version {version} for {connector_id}")

    # -- state -------------------------------------------------------------

    def set_state(self, connector_id: str, state: ConnectorState, actor: str) -> Connector:
        """Suspension is one click and takes effect on the next query
        (CNT-ADM-05). The first thing anyone needs during a content incident is
        a switch that stops a knowledgebase being answered from; if that needs
        a release, the answer during the incident is to take the product down.
        """
        connector = self.get(connector_id).model_copy(update={"state": state})
        self._connectors[connector_id] = connector
        self._log(actor, f"connector.{state}", connector_id, {})
        return connector

    # -- scope -------------------------------------------------------------

    def update_scope(
        self,
        connector_id: str,
        scope: KnowledgeScope,
        actor: str,
        measured: dict[str, object],
    ) -> Connector:
        """Every scope change writes an audit row carrying the scope before,
        the scope after, and the add/remove counts the console displayed at
        save time (CNT-CON-14)."""
        before = self.get(connector_id)
        after = before.model_copy(update={"scope": scope})
        self._log(
            actor,
            "scope.update",
            connector_id,
            {
                "before": before.scope.canonical_json(),
                "after": scope.canonical_json(),
                "measured": measured,
            },
        )
        return self.put(after, actor, note="scope update")

    def _log(self, actor: str, action: str, connector_id: str | None, detail: dict) -> None:
        self.audit.append(
            AuditEntry(
                at=dt.datetime(2026, 8, 22, 12, 0, 0),
                actor=actor,
                action=action,
                connector_id=connector_id,
                detail=detail,
            )
        )


def default_scope(space: str) -> KnowledgeScope:
    return KnowledgeScope(
        roots=(SourceRoot(kind="space", value=space),),
        sensitivity_ceiling=Sensitivity.INTERNAL,
    )
