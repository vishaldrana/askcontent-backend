"""The platform's own database.

This is **not** the customer's content store. PGP indexes it and the ECM owns
it; we hold a *description* — metadata, classification, chunk text and vectors —
and we cite back into the ECM rather than serving our copy.

Table groups mirror askdb's (`PLT-DM-*`) so an engineer who knows one knows the
other. Where a group differs it is because content differs from a schema, and
the docstring says how.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ..config import settings

# PLT-VEC-07 — one storage width, wide enough for the widest model this
# deployment intends to store. Shorter vectors are padded on write and the true
# dimension is recorded on the row, so two models can coexist during a rebuild.
VECTOR_WIDTH = 1536

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    # ARC-TEC-05 — the schema is pinned in the metadata, so every statement is
    # emitted fully qualified. Never a search_path option on the connection
    # string: poolers drop that startup parameter and the tables land in the
    # default schema.
    metadata = MetaData(schema=settings.db_schema, naming_convention=NAMING)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class PkMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantMixin:
    """PLT-TEN-18 / PLT-DM-02 — every tenant-scoped table carries an
    organisation identifier and a row-level security policy.

    Defence in depth. The application already filters by organisation; the
    policy is there for when it does not.
    """

    @property
    def __org_fk__(self) -> str:
        return f"{settings.db_schema}.org.id"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.org.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# 1 · Identity and tenancy
# ---------------------------------------------------------------------------


class Org(Base, PkMixin, TimestampMixin):
    __tablename__ = "org"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class AppUser(Base, PkMixin, TimestampMixin):
    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Membership(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.app_user.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)


class Workspace(Base, PkMixin, TenantMixin, TimestampMixin):
    """A grouping inside an organisation. Connectors and threads live in one.

    For askcontent this is the **business group** — the unit that owns a
    connector and whose members ask questions of it (CNT-CON-02).
    """

    __tablename__ = "workspace"
    __table_args__ = (UniqueConstraint("org_id", "slug"),)

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class AuthSession(Base, PkMixin, TimestampMixin):
    """PLT-DM-03 — the deliberate exception to row-level security, documented
    as such.

    A policy here would have to be satisfied *before* the session could be read
    in order to satisfy it: authentication happens before tenant scoping, not
    inside it. Isolation comes from the token hash instead — a row cannot be
    read without already holding its token.
    """

    __tablename__ = "auth_session"

    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.app_user.id", ondelete="CASCADE")
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 2 · Sources — where askdb has `connection`, we have a discovered
#     knowledgebase plus a connector that binds it to one business group.
# ---------------------------------------------------------------------------


class Knowledgebase(Base, PkMixin, TenantMixin, TimestampMixin):
    """A knowledgebase discovered in PGP (CNT-ADM-03).

    Discovered, not typed in. Registration state lives on the connector, not
    here, because the same knowledgebase may be registered more than once with
    different scopes for different groups (CNT-CON-06).
    """

    __tablename__ = "knowledgebase"
    __table_args__ = (UniqueConstraint("org_id", "kb_id"),)

    kb_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_indexed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    # False forces the explicit access-class declaration of CNT-ACL-03.
    exposes_acl: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    observed_fields: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Connector(Base, PkMixin, TenantMixin, TimestampMixin):
    """A connector is four things, none optional (CNT-CON-01): source binding,
    credential, knowledge scope, access binding.

    `scope` is stored as the canonical JSON of the closed grammar rather than as
    columns, because it is evaluated by one pure function shared by the ingest
    gate, the retrieval gate and the console preview (CNT-SCP-05). Splitting it
    into columns would invite a second, divergent evaluation in SQL.
    """

    __tablename__ = "connector"
    __table_args__ = (
        UniqueConstraint("org_id", "slug"),
        CheckConstraint("state in ('draft','active','suspended')", name="state"),
    )

    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.workspace.id", ondelete="CASCADE")
    )
    knowledgebase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.knowledgebase.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)

    scope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: One live source, or none. See design 09 and migration 0019 for why one
    #: and not a list.
    context_source: Mapped[dict | None] = mapped_column(JSONB)
    sensitivity_ceiling: Mapped[str] = mapped_column(String(16), default="internal", nullable=False)

    access_groups: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    declared_access_class: Mapped[str | None] = mapped_column(String(200))

    retrieval_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # PLT-DM-05 — the catalog version lives here and increments on every ingest
    # run. It is the invalidation hook for the plan cache.
    catalog_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Bumped on any scope or access change, so a cached projection is
    # invalidated without an explicit purge (PLT-DM-16, applied to scope).
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # PLT-DM-04 — envelope-encrypted, with the connector identifier as
    # additional authenticated data: moving a ciphertext to another connector
    # makes it undecryptable rather than merely wrong.
    credential_ciphertext: Mapped[bytes | None] = mapped_column()
    credential_key_id: Mapped[str | None] = mapped_column(String(64))

    last_ingest_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    documents = relationship("Document", back_populates="connector", cascade="all, delete-orphan")


class FieldRule(Base, PkMixin, TenantMixin, TimestampMixin):
    """One canonical field, one source field, one coercion, one optional value
    map, one optional default (CNT-MAP-02).

    That is the entire expressive surface, and there is deliberately no column
    for a script: a per-knowledgebase transform script is a per-knowledgebase
    code branch wearing a costume — unreviewable, untestable in aggregate, and a
    remote-execution surface in an admin console.
    """

    __tablename__ = "field_rule"
    __table_args__ = (UniqueConstraint("connector_id", "target"),)

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200))
    coercion: Mapped[str] = mapped_column(String(32), default="string", nullable=False)
    value_map: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    default_value: Mapped[str | None] = mapped_column(Text)
    # Which system wins where both expose the field. Default is the ECM, as the
    # system of record (CNT-MAP-05).
    prefer: Mapped[str] = mapped_column(String(8), default="ecm", nullable=False)
    observed_coverage: Mapped[float | None] = mapped_column(Float)


# ---------------------------------------------------------------------------
# 3 · The catalog — the heart
# ---------------------------------------------------------------------------


class Document(Base, PkMixin, TenantMixin, TimestampMixin):
    """One row per document the connector can see.

    Carries three groups of fields at once, exactly as askdb's column table
    does, and for the same reason — they are read together on every query:

      Identity   doc_id, title, url, path, space, version
      Structure  mime, size, parse path, parse quality, hashes
      Meaning    doc_type, confidence, authority, staleness, sensitivity
    """

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("connector_id", "doc_id"),
        Index("ix_document_scope", "connector_id", "in_scope"),
        Index("ix_document_space", "connector_id", "space"),
        Index("ix_document_updated", "connector_id", "source_updated_at"),
    )

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    connector = relationship("Connector", back_populates="documents")

    # -- identity, as the ECM reports it ---------------------------------
    doc_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    space: Mapped[str | None] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(String(320))
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(200))
    source_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    sensitivity: Mapped[str] = mapped_column(String(16), default="internal", nullable=False)
    acl_principals: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    # CNT-MAP-06 — unmapped source fields retained verbatim. The field nobody
    # mapped is routinely the one carrying the authority signal.
    extras: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    # -- structure --------------------------------------------------------
    mime: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    # CNT-PAR-13 — includes parser_id and parser_version, so a parser upgrade
    # re-embeds exactly the documents whose extracted text actually changed.
    text_hash: Mapped[str | None] = mapped_column(String(64))
    parser_id: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    parse_path: Mapped[str | None] = mapped_column(String(32))
    parse_quality: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    refusal_reason: Mapped[str | None] = mapped_column(Text)

    # -- meaning ----------------------------------------------------------
    doc_type: Mapped[str | None] = mapped_column(String(32))
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    doc_type_source: Mapped[str | None] = mapped_column(String(16))
    doc_type_evidence: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    authority: Mapped[str] = mapped_column(String(16), default="supporting", nullable=False)
    authority_reason: Mapped[str | None] = mapped_column(String(64))
    staleness: Mapped[str] = mapped_column(String(16), default="unknown_age", nullable=False)

    # -- state ------------------------------------------------------------
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # The single named rule that excluded it (CNT-SCP-16, CNT-ADM-10).
    exclusion_rule: Mapped[str | None] = mapped_column(String(64))
    exclusion_detail: Mapped[str | None] = mapped_column(Text)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(String(300))
    canonical_doc_id: Mapped[str | None] = mapped_column(String(300))

    # PLT-DM-08 applied to content: a document that vanishes upstream is marked
    # missing and kept, not deleted — a transient permission loss must not
    # destroy human corrections.
    missing_since: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentPin(Base, PkMixin, TenantMixin, TimestampMixin):
    """CNT-CAT-11 — a human correction that survives **every** future ingest,
    re-map, re-parse and re-classify.

    Held in its own table rather than as flags on `document` so that a
    re-ingest can truncate-and-rebuild document rows without a `create is
    update` merge, and human work is still safe. askdb solves the same problem
    with flags because its schema is stable; a corpus is not.
    """

    __tablename__ = "document_pin"
    __table_args__ = (UniqueConstraint("connector_id", "doc_id", "field"),)

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    doc_id: Mapped[str] = mapped_column(String(300), nullable=False)
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class AuthorityRule(Base, PkMixin, TenantMixin, TimestampMixin):
    """Tier by rule (CNT-CAT-05); a pin overrides it and nothing overrides a pin."""

    __tablename__ = "authority_rule"

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    space: Mapped[str | None] = mapped_column(String(200))
    path_prefix: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(200))
    tier: Mapped[str] = mapped_column(String(16), nullable=False)


class DocumentChunk(Base, PkMixin, TenantMixin, TimestampMixin):
    """The citable unit.

    `chunk_id` is content-derived and stable across re-ingest while text and
    heading path are unchanged (CNT-CHK-06), so citations stored in a thread do
    not rot when a document is re-parsed.
    """

    __tablename__ = "document_chunk"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        Index("ix_chunk_chunk_id", "connector_id", "chunk_id"),
    )

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.document.id", ondelete="CASCADE")
    )
    document = relationship("Document", back_populates="chunks")

    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Prepended before embedding (CNT-CHK-02): "Rate limits" under API › v2 and
    # under Support › Escalation are different subjects.
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    # The tail of the previous chunk in the same section, embedded with this
    # one and never cited. Persisted so the row carries everything its vector
    # was built from — it cannot be recovered from the neighbouring rows,
    # because runt-merging joins chunks after the overlap has been assigned.
    overlap: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Parent-child (CNT-CHK-04): the child is embedded, the parent is returned.
    parent_text: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    is_table: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_estimate: Mapped[int | None] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)


# ---------------------------------------------------------------------------
# 4 · Vectors
# ---------------------------------------------------------------------------


class Embedding(Base, PkMixin, TenantMixin, TimestampMixin):
    """One row per embedded chunk.

    PLT-VEC-06 — kind, reference, content hash, model identifier, true
    dimension, vector. The content hash is what makes a nightly re-index
    effectively free: unchanged content is skipped without an embedding call.
    """

    __tablename__ = "embedding"
    __table_args__ = (
        UniqueConstraint("connector_id", "kind", "ref_id", "model_id"),
        Index("ix_embedding_lookup", "connector_id", "kind"),
    )

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # chunk | document | term
    ref_id: Mapped[str] = mapped_column(String(300), nullable=False)
    parent_ref: Mapped[str | None] = mapped_column(String(300))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # PLT-VEC-07 — the storage column is one fixed width; the *true* dimension
    # is recorded per row and shorter vectors are padded on write.
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(VECTOR_WIDTH), nullable=False)


# ---------------------------------------------------------------------------
# 5 · Plans and terms
# ---------------------------------------------------------------------------


class RetrievalPlan(Base, PkMixin, TenantMixin, TimestampMixin):
    """The plan cache. CNT-RET-17 — cache the plan and the resolved evidence
    set, never the prose.

    Two people asking the same question of the same corpus get the same
    citations; the wording may differ. Keyed on the catalog version and the
    reranker identity, so an ingest run or a model change invalidates without
    an explicit purge (CNT-RNK-06).
    """

    __tablename__ = "retrieval_plan"
    __table_args__ = (
        UniqueConstraint("connector_id", "question_hash", "catalog_version", "reranker_id"),
    )

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reranker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class GlossaryTerm(Base, PkMixin, TenantMixin, TimestampMixin):
    """What a word means on this connector.

    Resolution applies the same three thresholds as askdb's value resolution:
    above accept it is a confident match; between accept and the floor the
    candidates are offered; **below the floor nothing is returned** and the
    answer says the term does not exist in this corpus rather than substituting
    a plausible synonym.
    """

    __tablename__ = "glossary_term"
    __table_args__ = (UniqueConstraint("connector_id", "term"),)

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    term: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="human", nullable=False)


# ---------------------------------------------------------------------------
# 6 · Conversations
# ---------------------------------------------------------------------------


class Thread(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "thread"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.workspace.id", ondelete="CASCADE")
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.app_user.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(Text)


class Message(Base, PkMixin, TenantMixin, TimestampMixin):
    """PLT-DM-13 — the sidecar carries citations, conflicts, notices and
    follow-ups. Anything structured that is not prose goes there.

    It is why a reloaded thread keeps its citations. Treat "does it survive a
    reload?" as part of the definition of done for every answer feature.
    """

    __tablename__ = "message"
    __table_args__ = (Index("ix_message_thread", "thread_id", "created_at"),)

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.thread.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sidecar: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    refused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    refusal_reason: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# 7 · Audit
# ---------------------------------------------------------------------------


class RetrievalRun(Base, PkMixin, TenantMixin, TimestampMixin):
    """CNT-CON-15 — one row per retrieval, **whether or not it succeeded**.

    `refused_doc_ids` is the interesting field, exactly as it is in askdb: after
    a leak the question is "what could this account have seen, and what did it
    see", and a pattern of denials is a signal worth having.
    """

    __tablename__ = "retrieval_run"
    __table_args__ = (Index("ix_run_connector_time", "connector_id", "created_at"),)

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    returned_doc_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    refused_doc_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    stale_index_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    forbidden_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    degraded: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    refusal_reason: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    cache_hit_rate: Mapped[float | None] = mapped_column(Float)


class ScopeChange(Base, PkMixin, TenantMixin, TimestampMixin):
    """CNT-CON-14 — scope before, scope after, and the add/remove counts the
    console displayed at save time.

    Storing the *displayed* counts matters: it records what the administrator
    was shown when they decided, not what a later recomputation would say.
    """

    __tablename__ = "scope_change"

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    scope_before: Mapped[dict | None] = mapped_column(JSONB)
    scope_after: Mapped[dict] = mapped_column(JSONB, nullable=False)
    added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    removed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unchanged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ---------------------------------------------------------------------------
# 8 · RBAC
# ---------------------------------------------------------------------------


class RbacRole(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "rbac_role"
    __table_args__ = (UniqueConstraint("connector_id", "name"),)

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class RbacRoleMember(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "rbac_role_member"
    __table_args__ = (UniqueConstraint("role_id", "principal"),)

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.rbac_role.id", ondelete="CASCADE")
    )
    principal: Mapped[str] = mapped_column(String(320), nullable=False)


class RbacLabelRule(Base, PkMixin, TenantMixin, TimestampMixin):
    """Where askdb denies or masks *columns*, we deny *documents* by space or
    label. There is no masking analogue: you cannot redact a paragraph the way
    you redact a column and still have a citable span."""

    __tablename__ = "rbac_label_rule"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.rbac_role.id", ondelete="CASCADE")
    )
    space: Mapped[str | None] = mapped_column(String(200))
    label: Mapped[str | None] = mapped_column(String(200))
    effect: Mapped[str] = mapped_column(String(8), nullable=False)  # allow | deny


class RbacPolicyVersion(Base, PkMixin, TenantMixin, TimestampMixin):
    """PLT-DM-16 — bumps on any change, so cached projections invalidate
    immediately without an explicit purge."""

    __tablename__ = "rbac_policy_version"

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(320))


# ---------------------------------------------------------------------------
# 9 · Operations
# ---------------------------------------------------------------------------


class Job(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_queue", "status", "created_at"),)

    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class QuarantineItem(Base, PkMixin, TenantMixin, TimestampMixin):
    """CNT-CON-06/07 — quarantine is visible work, not a silent drop.

    The matched span is stored **redacted**: the point is to show a reviewer
    what class of thing matched, not to make a second copy of the secret.
    """

    __tablename__ = "quarantine_item"

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    doc_id: Mapped[str] = mapped_column(String(300), nullable=False)
    matched_class: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_span: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(320))


class Embed(Base, PkMixin, TenantMixin, TimestampMixin):
    """WGT-10 — a publishable key resolves to exactly one connector,
    server-side. The widget cannot name a connector; there is no field for it."""

    __tablename__ = "embed"
    __table_args__ = (UniqueConstraint("publishable_key"),)

    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.connector.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    publishable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EmbedSession(Base, PkMixin, TenantMixin, TimestampMixin):
    __tablename__ = "embed_session"

    embed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{settings.db_schema}.embed.id", ondelete="CASCADE")
    )
    # WGT-02 — identity is required; there is no anonymous mode, so this column
    # is NOT nullable by design.
    visitor_id: Mapped[str] = mapped_column(String(320), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# 10 · Control plane — a separate database (PLT-TEN-01/02)
# ---------------------------------------------------------------------------


class ControlBase(DeclarativeBase):
    metadata = MetaData(schema="askcontent_control", naming_convention=NAMING)


class Tenant(ControlBase, PkMixin, TimestampMixin):
    __tablename__ = "tenant"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="provisioning", nullable=False)
    region: Mapped[str] = mapped_column(String(32), nullable=False)
    cluster: Mapped[str | None] = mapped_column(String(64))
    # PLT-TEN-05 — sealed with a key separate from the application encryption
    # key, so the two rotate independently. PLT-TEN-06 — no endpoint ever
    # returns this; rotation is write-only.
    sealed_dsn: Mapped[bytes] = mapped_column(nullable=False)
    revision: Mapped[str | None] = mapped_column(String(64))


class TenantMigration(ControlBase, PkMixin, TimestampMixin):
    __tablename__ = "tenant_migration"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("askcontent_control.tenant.id", ondelete="CASCADE")
    )
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class GlobalUser(ControlBase, PkMixin, TimestampMixin):
    __tablename__ = "global_user"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)


class UserTenant(ControlBase, PkMixin, TimestampMixin):
    __tablename__ = "user_tenant"
    __table_args__ = (UniqueConstraint("global_user_id", "tenant_id"),)

    global_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("askcontent_control.global_user.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("askcontent_control.tenant.id", ondelete="CASCADE")
    )


# Tables that carry an org_id and therefore get a row-level security policy in
# the initial migration. `auth_session` is deliberately absent — see its
# docstring and PLT-DM-03.
TENANT_TABLES = tuple(
    table.name
    for table in Base.metadata.sorted_tables
    if "org_id" in table.c and table.name != "auth_session"
)
