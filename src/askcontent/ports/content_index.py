"""ContentIndex — the port PGP sits behind.

Finds things. Holds no content. A hit is an *address*, not a document.

Why this is separate from ContentRepository (CNT-FED-01): the index and the
store fail independently, scale differently, disagree about metadata, and one
will be replaced before the other. A single combined port forces every future
adapter to implement both halves and hides the most important fact about this
architecture.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class FieldSample(BaseModel):
    """What the mapping editor renders beside each field (CNT-MAP-03)."""

    name: str
    observed_type: str
    coverage: float
    distinct_estimate: int
    samples: tuple[str, ...]


class KnowledgeBaseDescriptor(BaseModel):
    kb_id: str
    name: str
    description: str = ""
    document_count: int = 0
    last_indexed_at: dt.datetime | None = None
    embedding_model: str = ""
    embedding_dimension: int = 0
    fields: tuple[FieldSample, ...] = ()
    # Whether this knowledgebase can answer "may this principal read this doc".
    # Absence forces the explicit access-class declaration of CNT-ACL-03.
    exposes_acl: bool = False

    #: Whether the index can rerank its own fragments — a cross-encoder inside
    #: the search service, asked for with a parameter on the query rather than
    #: run by us afterwards.
    #:
    #: This is a *capability*, declared, not assumed. It has to be, because the
    #: two topologies cannot both be right at once: if the index reranks and we
    #: rerank again, the second pass reorders on a scale it does not own, using
    #: passages we recovered ourselves rather than the fragments the index
    #: actually scored. That is the same class of mistake as merging two
    #: rankers' scores, and it is invisible — the answer still looks fine.
    supports_rerank: bool = False
    #: What model does it, when it does. Recorded so an eval run says which
    #: ranker produced its numbers, whoever ran it.
    reranker_id: str = ""


class IndexHit(BaseModel):
    doc_id: str
    kb_id: str
    score: float
    # Advisory only (CNT-FED-02). We do not control PGP's chunker: its fragment
    # boundaries are not ours, may change without notice, and cannot be mapped
    # to an offset in the document the user will open. Used to seed passage
    # selection; never cited.
    passage_hint: str | None = None
    # The index's *copy* of metadata, which may lag the ECM by a sync interval.
    # Never authoritative (CNT-RET-08).
    metadata: dict[str, object] = Field(default_factory=dict)


class IndexPage(BaseModel):
    hits: tuple[IndexHit, ...]
    cursor: str | None = None
    total_estimate: int | None = None

    #: Set by the index when it reranked these hits itself. Reported rather
    #: than inferred from the request: a service under load may ignore the
    #: flag, and a caller that assumed otherwise would skip its own reranking
    #: and quietly serve fusion order as though it had been ranked.
    reranked: bool = False
    reranker_id: str = ""


class IndexFilters(BaseModel):
    """Compiled from scope ∩ permissions. Sent *with* the query, never applied
    to the results afterwards (CNT-SCP-14)."""

    spaces: tuple[str, ...] = ()
    labels_any: tuple[str, ...] = ()
    labels_none: tuple[str, ...] = ()
    doc_types: tuple[str, ...] = ()
    updated_after: dt.date | None = None
    updated_before: dt.date | None = None
    principals: tuple[str, ...] = ()


class IndexUnavailable(RuntimeError):
    """Raised on timeout or upstream failure. Callers degrade visibly
    (CNT-RET-05); they never silently narrow the evidence base."""


@runtime_checkable
class ContentIndex(Protocol):
    def list_knowledgebases(self) -> list[KnowledgeBaseDescriptor]: ...

    def describe(self, kb_id: str) -> KnowledgeBaseDescriptor: ...

    def search(
        self,
        kb_id: str,
        query: str,
        filters: IndexFilters,
        k: int = 20,
        cursor: str | None = None,
        rerank: bool = False,
    ) -> IndexPage:
        """Fragments matching the query.

        `rerank` asks the index to order its own results with a cross-encoder
        before returning them. Passing it to an index that does not advertise
        `supports_rerank` is allowed and ignored — a caller should not have to
        branch on the capability, and silently *not* reranking is safe in a way
        that silently double-reranking is not.

        When the index does rerank, it says so on the page. Guessing from the
        request would be wrong the first time a search service starts ignoring
        the flag under load.
        """
        ...

    def resolve_urls(
        self, urls: list[str], kb_ids: tuple[str, ...] = ()
    ) -> dict[str, list[object]]:
        """Find the indexed documents a set of URLs names.

        REAL CALL: PGP has no single endpoint for this. Expect to build it from
        what the index does offer, in this order of preference:

          1. a metadata filter on the canonical URL field, if the index will
             filter on it — one request for the whole batch;
          2. a metadata filter on a document-id extracted from the URL, for
             platforms that mint id-bearing permalinks;
          3. full-text search on the final path segment, as the last rung.

        OPEN Q, and it decides how good this feature is: **does PGP index the
        document's URL as a filterable field?** If it does, resolution is one
        cheap request and most pasted links resolve at rung 1. If it does not,
        every URL degrades to a search on its slug, which lands in the ambiguous
        band far more often and turns the paste screen into review work.

        Ask for the URL field before building anything else here.
        """
        ...

    def list_documents(
        self, kb_id: str, cursor: str | None = None, page_size: int = 500
    ) -> IndexPage:
        """Enumerate without a query.

        Needed by the scope preview, the add/remove diff and the corpus browser
        (CNT-SCP-07..09, CNT-SCP-16) — none of which are searches. If PGP
        exposes no scroll or enumeration endpoint, this is satisfied from the
        ECM instead and the preview becomes an ECM cost rather than an index
        one. Settle it early: it changes which system the console leans on.
        """
        ...
