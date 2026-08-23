"""Typed settings, loaded from the environment and a dotfile.

Every knob in one place (ARC-TEC). Per-connector overrides are seeded from
these platform defaults, and the console shows which of the two an effective
value came from (CNT-ADM-20).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASKCONTENT_", env_file=".env", extra="ignore"
    )

    # -- database ---------------------------------------------------------
    # Supabase's direct host (`db.<ref>.supabase.co`) is IPv6-only and does not
    # resolve from an IPv4 network, so both URLs point at the pooler:
    #   port 5432 = SESSION mode  — DDL, CREATE EXTENSION, migrations
    #   port 6543 = TRANSACTION mode — the application, if you want it
    # Session mode is used for both here because the pooler on this project is
    # shared and the connection count is small.
    database_url: str = "postgresql+psycopg://localhost:5432/askcontent"
    migration_database_url: str | None = None

    # ARC-TEC-05 — our tables live in a configurable schema, pinned through the
    # ORM metadata so every statement is emitted fully qualified.
    #
    # Trap: do NOT attempt this with a search_path option on the connection
    # string. Connection poolers silently drop that startup parameter and the
    # tables land in the default schema. Most requests then work and a few
    # fail, and which ones is a matter of pool timing.
    db_schema: str = "askcontent"

    # The pooler is shared with askdb and other services on this project.
    # Pool arithmetic is the operational risk: N services on one pooler means
    # N pools, and the ceiling belongs to whoever hits it last.
    pool_size: int = 3
    pool_max_overflow: int = 2
    statement_timeout_ms: int = 15_000

    # -- multi-tenancy ----------------------------------------------------
    # PLT-TEN-03 — a single-team deployment runs with tenancy disabled and
    # binds every request to one configured database. Both shapes are the same
    # code path.
    multi_tenant: bool = False
    control_plane_url: str | None = None

    # -- retrieval defaults (per-connector overridable) -------------------
    k_per_channel: int = 20
    rrf_constant: int = 60
    rerank_floor: float = 0.08
    max_rerank_pairs: int = 100
    passages_per_document: int = 3
    context_budget_chunks: int = 12
    channel_timeout_seconds: float = 3.0
    fetch_timeout_seconds: float = 5.0

    # -- providers --------------------------------------------------------
    # ARC-TEC-14 — text generation and embedding are configured independently.
    # They are routinely different vendors.
    #: "auto" uses a real model when a key is present and the deterministic
    #: hashed n-gram bag when it is not. The offline one is right for tests and
    #: materially worse for retrieval: it can only match documents that repeat
    #: the words of the question.
    embedding_provider: str = "auto"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str | None = None
    embedding_dim: int = 1536
    reranker: str = "auto"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    #: The same triple askdb uses, so one deployment configures both the same
    #: way. "auto" uses the model when a key is present and the extractive
    #: stand-in when it is not; "extractive" forces offline answers.
    llm_provider: str = "auto"
    llm_model: str = "gpt-4.1-2025-04-14"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    #: Share of a question's content words that must appear in the retrieved
    #: passages before an answer is attempted. See domain/groundedness.py.
    relevance_floor: float = 0.34

    # -- external systems -------------------------------------------------
    pgp_base_url: str | None = None
    ecm_base_url: str | None = None

    encryption_key: str = Field(
        default="dev-only-not-a-real-key",
        description="Envelope encryption root key. Rotates independently of the "
        "control plane's connection-string key (PLT-TEN-05).",
    )

    @property
    def migrations_url(self) -> str:
        return self.migration_database_url or self.database_url


settings = Settings()
