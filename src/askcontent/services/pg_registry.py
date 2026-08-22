"""Connector registry, persisted.

Same interface as the in-memory `Registry`, so nothing above it changes. The
in-memory one remains the test double; this one is what runs.

Every behaviour of the pipeline that varies per knowledgebase is a row here
(CNT-ADM-02). There is no per-knowledgebase branch anywhere in the retrieval
path, and a test asserts it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.catalog import AuthorityRule
from ..domain.documents import AuthorityTier
from ..domain.scope import KnowledgeScope
from ..db import models as m
from .mapping import FieldMap, FieldRule
from .registry import (
    AccessBinding,
    AuditEntry,
    ConfigVersion,
    Connector,
    ConnectorState,
    RetrievalConfig,
)

NOW = dt.datetime(2026, 8, 22, 12, 0, 0, tzinfo=dt.UTC)


def _scope_hash(scope: KnowledgeScope) -> str:
    return hashlib.blake2b(scope.canonical_json().encode(), digest_size=16).hexdigest()


class PgRegistry:
    """Reads and writes `askcontent.connector` and friends."""

    def __init__(self, session_factory, org_id: uuid.UUID) -> None:
        self._sessions = session_factory
        self.org_id = org_id

    # -- reads -------------------------------------------------------------

    def get(self, connector_id: str) -> Connector:
        with self._sessions() as session:
            row = self._row(session, connector_id)
            return self._to_domain(session, row)

    def list(self) -> list[Connector]:
        with self._sessions() as session:
            rows = session.scalars(
                select(m.Connector)
                .where(m.Connector.org_id == self.org_id)
                .order_by(m.Connector.name)
            ).all()
            return [self._to_domain(session, row) for row in rows]

    def _row(self, session: Session, connector_id: str) -> m.Connector:
        row = session.scalars(
            select(m.Connector).where(
                m.Connector.org_id == self.org_id, m.Connector.slug == connector_id
            )
        ).one_or_none()
        if row is None:
            raise KeyError(f"unknown connector: {connector_id}")
        return row

    def _to_domain(self, session: Session, row: m.Connector) -> Connector:
        kb = session.get(m.Knowledgebase, row.knowledgebase_id)
        rules = session.scalars(
            select(m.FieldRule).where(m.FieldRule.connector_id == row.id)
        ).all()
        authority = session.scalars(
            select(m.AuthorityRule)
            .where(m.AuthorityRule.connector_id == row.id)
            .order_by(m.AuthorityRule.ordinal)
        ).all()
        pins = session.scalars(
            select(m.DocumentPin).where(
                m.DocumentPin.connector_id == row.id, m.DocumentPin.field == "authority"
            )
        ).all()

        return Connector(
            connector_id=row.slug,
            name=row.name,
            business_group=session.get(m.Workspace, row.workspace_id).name,
            kb_id=kb.kb_id,
            field_map=FieldMap(
                kb_id=kb.kb_id,
                access_class=row.declared_access_class,
                rules=tuple(
                    FieldRule(
                        target=r.target,
                        source=r.source,
                        coercion=r.coercion,
                        value_map=r.value_map or {},
                        default=r.default_value,
                        prefer=r.prefer,
                    )
                    for r in rules
                ),
            ),
            scope=KnowledgeScope.model_validate(row.scope),
            access=AccessBinding(
                groups=tuple(row.access_groups or ()),
                declared_access_class=row.declared_access_class,
            ),
            retrieval=RetrievalConfig.model_validate(row.retrieval_config or {}),
            authority_rules=tuple(
                AuthorityRule(
                    space=a.space, path_prefix=a.path_prefix, label=a.label,
                    tier=AuthorityTier(a.tier),
                )
                for a in authority
            ),
            authority_pins={p.doc_id: AuthorityTier(p.value) for p in pins},
            state=ConnectorState(row.state),
            version=row.policy_version,
        )

    # -- writes ------------------------------------------------------------

    def put(self, connector: Connector, actor: str, note: str = "") -> Connector:
        with self._sessions() as session:
            kb = session.scalars(
                select(m.Knowledgebase).where(
                    m.Knowledgebase.org_id == self.org_id,
                    m.Knowledgebase.kb_id == connector.kb_id,
                )
            ).one()
            workspace = self._workspace(session, connector.business_group)

            row = session.scalars(
                select(m.Connector).where(
                    m.Connector.org_id == self.org_id,
                    m.Connector.slug == connector.connector_id,
                )
            ).one_or_none()
            if row is None:
                row = m.Connector(
                    org_id=self.org_id,
                    slug=connector.connector_id,
                    knowledgebase_id=kb.id,
                    workspace_id=workspace.id,
                    scope={},
                    scope_hash="",
                )
                session.add(row)

            row.name = connector.name
            row.state = str(connector.state)
            row.scope = connector.scope.model_dump(mode="json")
            row.scope_hash = _scope_hash(connector.scope)
            row.sensitivity_ceiling = str(connector.scope.sensitivity_ceiling)
            row.access_groups = list(connector.access.groups)
            row.declared_access_class = connector.access.declared_access_class
            row.retrieval_config = connector.retrieval.model_dump(mode="json")
            row.policy_version = (row.policy_version or 0) + 1
            session.flush()

            self._replace_field_rules(session, row, connector.field_map)
            self._replace_authority_rules(session, row, connector.authority_rules)
            self._audit(session, actor, "connector.put", row.id, {"note": note})
            session.commit()

            return self._to_domain(session, row)

    def _workspace(self, session: Session, name: str) -> m.Workspace:
        slug = name.lower().replace(" ", "-")[:64]
        row = session.scalars(
            select(m.Workspace).where(
                m.Workspace.org_id == self.org_id, m.Workspace.slug == slug
            )
        ).one_or_none()
        if row is None:
            row = m.Workspace(org_id=self.org_id, slug=slug, name=name)
            session.add(row)
            session.flush()
        return row

    def _replace_field_rules(self, session: Session, row: m.Connector, field_map: FieldMap) -> None:
        for existing in session.scalars(
            select(m.FieldRule).where(m.FieldRule.connector_id == row.id)
        ).all():
            session.delete(existing)
        session.flush()
        for rule in field_map.rules:
            session.add(
                m.FieldRule(
                    org_id=self.org_id, connector_id=row.id, target=rule.target,
                    source=rule.source, coercion=str(rule.coercion),
                    value_map=rule.value_map, default_value=rule.default, prefer=rule.prefer,
                )
            )

    def _replace_authority_rules(self, session, row, rules) -> None:
        for existing in session.scalars(
            select(m.AuthorityRule).where(m.AuthorityRule.connector_id == row.id)
        ).all():
            session.delete(existing)
        session.flush()
        for ordinal, rule in enumerate(rules):
            session.add(
                m.AuthorityRule(
                    org_id=self.org_id, connector_id=row.id, ordinal=ordinal,
                    space=rule.space, path_prefix=rule.path_prefix, label=rule.label,
                    tier=str(rule.tier),
                )
            )

    def set_state(self, connector_id: str, state: ConnectorState, actor: str) -> Connector:
        """CNT-ADM-05 — takes effect on the next query, with no deploy."""
        with self._sessions() as session:
            row = self._row(session, connector_id)
            row.state = str(state)
            self._audit(session, actor, f"connector.{state}", row.id, {})
            session.commit()
            return self._to_domain(session, row)

    def update_scope(self, connector_id: str, scope, actor: str, measured: dict) -> Connector:
        """CNT-CON-14 — the audit row carries the scope before, the scope after,
        and the add/remove counts the console *displayed at save time*.

        Storing the displayed counts matters: it records what the administrator
        was shown when they decided, not what a later recomputation would say.
        """
        with self._sessions() as session:
            row = self._row(session, connector_id)
            before = dict(row.scope)
            row.scope = scope.model_dump(mode="json")
            row.scope_hash = _scope_hash(scope)
            row.sensitivity_ceiling = str(scope.sensitivity_ceiling)
            row.policy_version = (row.policy_version or 0) + 1

            session.add(
                m.ScopeChange(
                    org_id=self.org_id, connector_id=row.id, actor=actor,
                    scope_before=before, scope_after=row.scope,
                    added=int(measured.get("added", 0)),
                    removed=int(measured.get("removed", 0)),
                    unchanged=int(measured.get("unchanged", 0)),
                )
            )
            self._audit(session, actor, "scope.update", row.id, measured)
            session.commit()
            return self._to_domain(session, row)

    # -- audit -------------------------------------------------------------

    def _audit(self, session: Session, actor: str, action: str, connector_id, detail: dict) -> None:
        session.add(
            m.RetrievalRun(
                org_id=self.org_id, connector_id=connector_id, actor=actor,
                question=f"[{action}]", spec={"action": action, "detail": detail},
                plan_hash="-", answered=True,
            )
        )

    @property
    def audit(self) -> list[AuditEntry]:
        with self._sessions() as session:
            rows = session.scalars(
                select(m.ScopeChange)
                .where(m.ScopeChange.org_id == self.org_id)
                .order_by(m.ScopeChange.created_at.desc())
            ).all()
            return [
                AuditEntry(
                    at=row.created_at, actor=row.actor, action="scope.update",
                    connector_id=str(row.connector_id),
                    detail={
                        "before": row.scope_before, "after": row.scope_after,
                        "measured": {
                            "added": row.added, "removed": row.removed,
                            "unchanged": row.unchanged,
                        },
                    },
                )
                for row in rows
            ]

    def versions(self, connector_id: str) -> list[ConfigVersion]:
        return []

    def revert(self, connector_id: str, version: int, actor: str) -> Connector:
        raise NotImplementedError(
            "configuration history is not yet persisted; see CNT-ADM-16"
        )
