"""The RetrievalSpec (CNT-RET-14).

The model fills this structure and nothing else. It is a closed union: there is
no raw-query variant, and `scope_ref` is an identifier of a reviewed scope
object rather than anything the model composes. Widening scope is therefore not
a thing the grammar can express.
"""

from __future__ import annotations

import datetime as dt
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .documents import AuthorityTier, DocType


class Intent(StrEnum):
    LOOKUP = "lookup"
    PROCEDURE = "procedure"
    COMPARE = "compare"
    TIMELINE = "timeline"
    WHO_OWNS = "who_owns"
    SUMMARIZE = "summarize"


class Channel(StrEnum):
    PGP = "pgp"
    ECM = "ecm"
    NATIVE = "native"


class FilterField(StrEnum):
    SPACE = "space"
    OWNER = "owner"
    LABEL = "label"
    DOC_TYPE = "doc_type"
    TITLE = "title"


class FilterOp(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    CONTAINS = "contains"


class Filter(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: FilterField
    op: FilterOp
    value: str | tuple[str, ...]


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: dt.date | None = None
    max_age_days: int | None = None


class AuthorityMode(StrEnum):
    AUTHORITATIVE_ONLY = "authoritative_only"
    INCLUDE_SUPPORTING = "include_supporting"


class DiversityDimension(StrEnum):
    DOCUMENT = "document"
    SPACE = "space"
    SOURCE = "source"


class RetrievalSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: Intent
    scope_ref: str
    question: str
    terms: tuple[str, ...] = ()
    filters: tuple[Filter, ...] = ()
    doc_types: tuple[DocType, ...] = ()
    freshness: Freshness = Field(default_factory=Freshness)
    authority: AuthorityMode = AuthorityMode.INCLUDE_SUPPORTING
    # Server-populated from the scope's configuration. A model-supplied value is
    # discarded before this object is constructed (CNT-RET-15).
    channels: tuple[Channel, ...] = (Channel.PGP, Channel.ECM)
    k_per_channel: int = 20
    diversity_by: DiversityDimension = DiversityDimension.DOCUMENT

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )

    def min_authority(self) -> set[AuthorityTier]:
        if self.authority is AuthorityMode.AUTHORITATIVE_ONLY:
            return {AuthorityTier.AUTHORITATIVE}
        return {AuthorityTier.AUTHORITATIVE, AuthorityTier.SUPPORTING}


class ModelRetrievalRequest(BaseModel):
    """Exactly what a model is permitted to emit.

    Note what is absent: `channels`, `k_per_channel` and any free-text filter.
    The server merges this with the scope's stored configuration to build the
    RetrievalSpec, which is why a compromised or confused model cannot widen
    its own reach.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: Intent
    question: str
    terms: tuple[str, ...] = ()
    filters: tuple[Filter, ...] = ()
    doc_types: tuple[DocType, ...] = ()
    freshness: Freshness = Field(default_factory=Freshness)
    authority: AuthorityMode = AuthorityMode.INCLUDE_SUPPORTING
