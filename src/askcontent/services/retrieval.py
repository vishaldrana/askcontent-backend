"""The two-stage retrieval pipeline (CNT-RET-01).

    ① Plan     model fills a RetrievalSpec; scope_ref is an identifier
    ② Compile  scope ∩ principal permissions -> concrete index filters
    ③ Candidates  PGP and ECM in parallel, per-channel k
    ④ Resolution  dedupe, re-apply both gates against ECM metadata, drop
    ⑤ Passages    fetch -> parse -> chunk -> select
    ⑥ Rerank      cross-encoder, diversity cap, context budget, citations

Every drop is attributed to exactly one named rule (CNT-ADM-10). Without
per-stage attribution, tuning a content pipeline is superstition.
"""

from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from pydantic import BaseModel, Field

from ..domain.catalog import as_utc, assign_authority, staleness
from ..domain.chunks import Chunk
from ..domain.documents import (
    AuthorityTier,
    DocMetadata,
    DocRef,
    Staleness,
)
from ..domain.ids import plan_hash
from ..domain.retrieval_spec import Channel, RetrievalSpec
from ..domain.role_rules import decide as role_decide
from ..domain.scope import ExclusionRule, KnowledgeScope, evaluate
from ..ports.content_index import IndexFilters, IndexUnavailable
from ..ports.content_repository import RepositoryUnavailable, ResolutionOutcome
from .mapping import apply_map
from .registry import Connector, ConnectorState

#: The corpus "now". Aware, because document timestamps arriving from
#: Postgres are `timestamptz` and mixing the two raises at retrieval time.
NOW = dt.datetime(2026, 8, 22, 12, 0, 0, tzinfo=dt.UTC)


# --------------------------------------------------------------------------
# Trace — CNT-ADM-09. Rendered as-is by /connectors/:id/diagnose, and
# available for any production answer subject to permission (CNT-ADM-11).
# --------------------------------------------------------------------------


class ChannelTrace(BaseModel):
    channel: str
    hits: int = 0
    latency_ms: float = 0.0
    error: str | None = None
    top: tuple[str, ...] = ()


class CandidateTrace(BaseModel):
    doc_id: str
    title: str | None = None
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    fusion_score: float = 0.0
    fusion_rank: int | None = None
    resolution: str | None = None
    dropped_by: str | None = None
    drop_detail: str | None = None
    chunks_selected: int = 0
    cache_hit: bool | None = None
    parse_path: str | None = None
    parse_quality: dict[str, object] = Field(default_factory=dict)
    rerank_score: float | None = None
    rerank_rank: int | None = None
    authority: str | None = None
    staleness: str | None = None


class RetrievalTrace(BaseModel):
    spec_json: str
    plan_hash: str
    filters: dict[str, object]
    channels: tuple[ChannelTrace, ...] = ()
    candidates: tuple[CandidateTrace, ...] = ()
    degraded: tuple[str, ...] = ()
    stale_index_count: int = 0
    forbidden_count: int = 0
    cache_hit_rate: float = 0.0
    total_ms: float = 0.0
    refusal: str | None = None


class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    url: str
    space: str | None
    owner: str | None
    authority: AuthorityTier
    updated_at: dt.datetime | None
    staleness: Staleness
    heading_path: tuple[str, ...]
    #: Carried so conflict detection can tell a jurisdictional difference from
    #: a contradiction.
    labels: tuple[str, ...] = ()
    span: str
    rerank_score: float
    fusion_rank: int


class Conflict(BaseModel):
    """Two authoritative sources that disagree are both shown, with dates and
    owners (CNT-RET-20). The system does not pick."""

    subject: str
    citations: tuple[Citation, ...]


class Evidence(BaseModel):
    citations: tuple[Citation, ...]
    conflicts: tuple[Conflict, ...] = ()
    notices: tuple[str, ...] = ()
    trace: RetrievalTrace
    refused: bool = False
    refusal_reason: str | None = None


# --------------------------------------------------------------------------


class RetrievalService:
    def __init__(self, index, repository, embedder, reranker, passages) -> None:
        self.index = index
        self.repository = repository
        self.embedder = embedder
        self.reranker = reranker
        self.passages = passages

    # -- ② compile ---------------------------------------------------------

    def compile_filters(self, connector: Connector, principal: str) -> IndexFilters:
        """Scope ∩ permissions, pushed into the query.

        Never applied to a result set afterwards (CNT-SCP-14): post-filtering
        means excluded documents influenced ranking, occupied the k budget, and
        were present in process memory.
        """
        scope = connector.scope
        spaces = tuple(r.value for r in scope.roots if r.kind == "space")
        return IndexFilters(
            spaces=spaces,
            labels_any=scope.labels_any,
            labels_none=scope.labels_none,
            doc_types=tuple(str(t) for t in scope.doc_types),
            updated_after=scope.updated_after,
            updated_before=scope.updated_before,
            principals=(principal,),
        )

    # -- the pipeline ------------------------------------------------------

    def retrieve(
        self, connector: Connector, spec: RetrievalSpec, principal: str,
        role_rules: tuple = (),
    ) -> Evidence:
        started = time.perf_counter()
        config = connector.retrieval
        filters = self.compile_filters(connector, principal)

        trace = RetrievalTrace(
            spec_json=spec.canonical_json(),
            plan_hash=plan_hash(
                spec.canonical_json(),
                self.reranker.reranker_id,
                self.reranker.reranker_version,
            ),
            filters=filters.model_dump(mode="json"),
        )

        if connector.state is not ConnectorState.ACTIVE:
            trace.refusal = f"connector is {connector.state}"
            return Evidence(citations=(), trace=trace, refused=True, refusal_reason=trace.refusal)

        # ③ candidate generation ------------------------------------------
        channel_results, channel_traces, degraded = self._generate_candidates(
            connector, spec, filters, principal, config
        )
        trace.channels = tuple(channel_traces)
        trace.degraded = tuple(degraded)

        if not channel_results:
            trace.refusal = "no candidates from any channel"
            trace.total_ms = (time.perf_counter() - started) * 1000
            return Evidence(citations=(), trace=trace, refused=True, refusal_reason=trace.refusal)

        # fusion by rank, never by raw score (CNT-RET-04) ------------------
        fused = _reciprocal_rank_fusion(channel_results, k=config.rrf_constant)

        candidates: dict[str, CandidateTrace] = {}
        for rank, (doc_id, score, channel_ranks) in enumerate(fused, start=1):
            candidates[doc_id] = CandidateTrace(
                doc_id=doc_id,
                channel_ranks=channel_ranks,
                fusion_score=round(score, 6),
                fusion_rank=rank,
            )

        # ④ resolution -----------------------------------------------------
        # One round trip for every candidate where the store supports it. The
        # gates below are identical either way — batching changes the number of
        # requests, never the decision.
        batch = self._resolve_batch(connector, list(candidates), principal)

        resolved: list[tuple[DocMetadata, CandidateTrace]] = []
        for doc_id, candidate in candidates.items():
            metadata = self._resolve(
                connector, doc_id, principal, candidate, trace, batch.get(doc_id)
            )
            if metadata is None:
                continue
            # The role's own narrowing, applied here rather than after ranking:
            # a document excluded post-hoc has already influenced the order and
            # occupied the k budget (CNT-SCP-14).
            verdict = role_decide(
                role_rules, space=metadata.space,
                labels=tuple(metadata.labels or ()),
            )
            if not verdict.allowed:
                candidate.dropped_by = "role_rule"
                candidate.drop_detail = verdict.reason
                continue
            resolved.append((metadata, candidate))

        if not resolved:
            trace.candidates = tuple(candidates.values())
            trace.refusal = "every candidate was dropped at resolution"
            trace.total_ms = (time.perf_counter() - started) * 1000
            return Evidence(citations=(), trace=trace, refused=True, refusal_reason=trace.refusal)

        # ⑤ passage recovery -----------------------------------------------
        question_vector = self.embedder.embed_query(spec.question)
        passage_pool: list[tuple[Chunk, DocMetadata, CandidateTrace]] = []

        for metadata, candidate in resolved:
            candidate.title = metadata.title
            before = self.passages.stats.hits
            try:
                entry = self.passages.load(
                    DocRef(doc_id=metadata.doc_id, kb_id=metadata.kb_id),
                    metadata,
                    principal,
                )
            except RepositoryUnavailable as exc:
                candidate.dropped_by = "fetch_unavailable"
                candidate.drop_detail = str(exc)
                continue

            candidate.cache_hit = self.passages.stats.hits > before
            candidate.parse_path = str(entry.parsed.parse_path)
            candidate.parse_quality = entry.parsed.quality.model_dump(mode="json")

            if entry.parsed.refused:
                # A document we cannot read reliably is reported, not indexed
                # at low confidence (CNT-PAR-11).
                candidate.dropped_by = "parse_refused"
                candidate.drop_detail = entry.parsed.refusal_reason
                continue

            selected = self.passages.select(
                question_vector, entry.chunks, config.passages_per_document
            )
            candidate.chunks_selected = len(selected)
            for chunk, _score in selected:
                passage_pool.append((chunk, metadata, candidate))

        if not passage_pool:
            trace.candidates = tuple(candidates.values())
            trace.refusal = "no passages could be recovered from any resolved document"
            trace.cache_hit_rate = self.passages.stats.hit_rate
            trace.total_ms = (time.perf_counter() - started) * 1000
            return Evidence(citations=(), trace=trace, refused=True, refusal_reason=trace.refusal)

        # ⑥ rerank ----------------------------------------------------------
        texts = [chunk.embed_text for chunk, _, _ in passage_pool]
        try:
            ranked = self.reranker.rerank(spec.question, texts)
        except Exception as exc:  # noqa: BLE001
            # Degrade to fusion order rather than failing the answer
            # (CNT-RNK-04).
            degraded.append(f"reranker unavailable ({exc}); fusion order used")
            trace.degraded = tuple(degraded)
            ranked = [
                type("R", (), {"index": i, "score": 1.0 / (1 + i)})()
                for i in range(len(texts))
            ]

        citations, conflicts, notices = self._assemble(
            spec, connector, ranked, passage_pool, candidates, config
        )

        trace.candidates = tuple(candidates.values())
        trace.cache_hit_rate = round(self.passages.stats.hit_rate, 4)
        trace.total_ms = round((time.perf_counter() - started) * 1000, 2)

        if not citations:
            # An answer with no passage above the floor is a refusal, not a
            # low-confidence answer (CNT-RNK-07).
            trace.refusal = (
                f"no passage scored above the reranker floor ({config.rerank_floor})"
            )
            return Evidence(
                citations=(), trace=trace, refused=True, refusal_reason=trace.refusal,
                notices=tuple(notices),
            )

        return Evidence(
            citations=tuple(citations),
            conflicts=tuple(conflicts),
            notices=tuple(notices),
            trace=trace,
        )

    # -- ③ ------------------------------------------------------------------

    def _generate_candidates(self, connector, spec, filters, principal, config):
        """Both channels always (CNT-RET-03). They have complementary blind
        spots, and the vector-only failure is the one users notice: an exact
        error code returns thematically similar documents that do not contain
        it."""
        channel_results: dict[str, list[str]] = {}
        traces: list[ChannelTrace] = []
        degraded: list[str] = []

        def run_pgp() -> tuple[list[str], float, str | None]:
            start = time.perf_counter()
            try:
                page = self.index.search(
                    connector.kb_id, spec.question, filters, k=config.k_per_channel
                )
                doc_ids = [hit.doc_id for hit in page.hits]
                # Pagination: real indexes do not return everything at once.
                while page.cursor and len(doc_ids) < config.k_per_channel:
                    page = self.index.search(
                        connector.kb_id, spec.question, filters,
                        k=config.k_per_channel, cursor=page.cursor,
                    )
                    doc_ids.extend(hit.doc_id for hit in page.hits)
                return doc_ids[: config.k_per_channel], (time.perf_counter() - start) * 1000, None
            except IndexUnavailable as exc:
                return [], (time.perf_counter() - start) * 1000, str(exc)

        def run_ecm() -> tuple[list[str], float, str | None]:
            start = time.perf_counter()
            try:
                refs = self.repository.search(
                    spec.question, principal, k=config.k_per_channel,
                    spaces=filters.spaces,
                )
                return [r.doc_id for r in refs], (time.perf_counter() - start) * 1000, None
            except RepositoryUnavailable as exc:
                return [], (time.perf_counter() - start) * 1000, str(exc)

        runners = {}
        if Channel.PGP in config.channels:
            runners["pgp"] = run_pgp
        if Channel.ECM in config.channels:
            runners["ecm"] = run_ecm

        with ThreadPoolExecutor(max_workers=max(1, len(runners))) as pool:
            futures = {name: pool.submit(fn) for name, fn in runners.items()}
            for name, future in futures.items():
                try:
                    doc_ids, latency, error = future.result(
                        timeout=config.channel_timeout_seconds
                    )
                except FutureTimeout:
                    doc_ids, latency, error = [], config.channel_timeout_seconds * 1000, "timeout"

                traces.append(
                    ChannelTrace(
                        channel=name, hits=len(doc_ids), latency_ms=round(latency, 2),
                        error=error, top=tuple(doc_ids[:5]),
                    )
                )
                if error:
                    # Visible degradation, never silent narrowing (CNT-RET-05,
                    # CNT-CON-13).
                    degraded.append(f"{name} channel unavailable: {error}")
                elif doc_ids:
                    channel_results[name] = doc_ids

        return channel_results, traces, degraded

    # -- ④ ------------------------------------------------------------------

    def _resolve_batch(self, connector: Connector, doc_ids: list[str], principal: str):
        """Pre-resolve every candidate in one call, when the store offers it."""
        batch_fn = getattr(self.repository, "fetch_metadata_batch", None)
        if batch_fn is None:
            return {}
        refs = [DocRef(doc_id=doc_id, kb_id=connector.kb_id) for doc_id in doc_ids]
        try:
            return batch_fn(refs, principal)
        except Exception:  # noqa: BLE001
            # A failed batch degrades to per-document resolution rather than
            # failing the question. Slower is a worse answer than fast; no
            # answer is worse than both.
            return {}

    def _resolve(
        self, connector: Connector, doc_id: str, principal: str,
        candidate: CandidateTrace, trace: RetrievalTrace,
        prefetched=None,
    ) -> DocMetadata | None:
        ref = DocRef(doc_id=doc_id, kb_id=connector.kb_id)
        try:
            resolution = prefetched or self.repository.fetch_metadata(ref, principal)
        except RepositoryUnavailable as exc:
            candidate.resolution = "unavailable"
            candidate.dropped_by = "ecm_unavailable"
            candidate.drop_detail = str(exc)
            return None

        candidate.resolution = str(resolution.outcome)

        if resolution.outcome is ResolutionOutcome.NOT_FOUND:
            # PGP holds an id the ECM no longer has. Aggregated per KB as the
            # index-health metric (CNT-RET-07).
            trace.stale_index_count += 1
            candidate.dropped_by = "stale_index"
            return None

        if resolution.outcome is ResolutionOutcome.FORBIDDEN:
            # Dropped *before* ranking. Its existence is never disclosed
            # (CNT-ACL-04).
            trace.forbidden_count += 1
            candidate.dropped_by = "forbidden"
            return None

        if resolution.metadata is None:
            candidate.dropped_by = "no_metadata"
            return None

        metadata = resolution.metadata

        # The retrieval gate: current scope, against ECM metadata, never
        # against an in_scope flag written at ingest and never against the
        # index's cached copy (CNT-SCP-11, CNT-RET-08).
        decision = evaluate(connector.scope, metadata)
        if not decision.in_scope:
            candidate.dropped_by = str(decision.rule)
            candidate.drop_detail = decision.detail
            return None

        state = staleness(metadata, connector.retrieval.freshness, NOW)
        tier, _why = assign_authority(
            metadata, list(connector.authority_rules), connector.authority_pins, state
        )
        candidate.authority = str(tier)
        candidate.staleness = str(state)

        if tier is AuthorityTier.ARCHIVE:
            candidate.dropped_by = "archive_tier"
            candidate.drop_detail = f"staleness={state}"
            return None

        return metadata.model_copy(update={"authority": tier})

    # -- ⑥ ------------------------------------------------------------------

    def _assemble(self, spec, connector, ranked, passage_pool, candidates, config):
        citations: list[Citation] = []
        notices: list[str] = []
        per_document: dict[str, int] = {}
        seen_texts: set[str] = set()

        # The reranker scores a passage in isolation; it has no idea which
        # document the passage came from or how the two channels ranked that
        # document. Ordering on its score alone throws away the strongest
        # signal available — that both the index and the store independently
        # put one document first — and lets a long, keyword-dense passage from
        # an unrelated page outrank the page that actually answers the
        # question.
        #
        # So the two orderings are fused, by rank, the same way the channels
        # were: reciprocal ranks added, never raw scores compared. The floor
        # below is still applied to the reranker's own score, so "nothing
        # cleared the bar" remains a refusal rather than a reordering.
        k = config.rrf_constant
        ordered = sorted(
            enumerate(ranked, start=1),
            key=lambda pair: -(
                1.0 / (k + pair[0])
                + 1.0 / (k + (
                    candidates[passage_pool[pair[1].index][2].doc_id].fusion_rank
                    or len(candidates)
                ))
            ),
        )

        for rank, result in ordered:
            if result.score < config.rerank_floor:
                continue
            if len(citations) >= config.context_budget_chunks:
                break

            chunk, metadata, candidate = passage_pool[result.index]

            # Cross-source near-duplicate collapse (CNT-PAR-21). Where the same
            # material exists in two places the canonical copy is cited; citing
            # the shadow copy makes the answer diverge from the system of
            # record the reader will open.
            fingerprint = _fingerprint(chunk.text)
            if fingerprint in seen_texts:
                continue
            seen_texts.add(fingerprint)

            if per_document.get(metadata.doc_id, 0) >= config.passages_per_document:
                continue
            per_document[metadata.doc_id] = per_document.get(metadata.doc_id, 0) + 1

            candidate.rerank_score = round(float(result.score), 6)
            candidate.rerank_rank = rank

            state = staleness(metadata, config.freshness, NOW)
            citations.append(
                Citation(
                    chunk_id=chunk.chunk_id,
                    doc_id=metadata.doc_id,
                    title=metadata.title,
                    # The link opens the document in the ECM, not in our copy
                    # (CNT-RET-19): our parsed copy can be stale, and sending
                    # the user there makes us the system of record for content
                    # we do not own.
                    url=metadata.url,
                    space=metadata.space,
                    owner=metadata.owner,
                    authority=metadata.authority,
                    updated_at=metadata.updated_at,
                    staleness=state,
                    heading_path=chunk.heading_path,
                    labels=metadata.labels,
                    span=_span_of(chunk),
                    rerank_score=round(float(result.score), 6),
                    fusion_rank=candidate.fusion_rank or 0,
                )
            )

        conflicts = _detect_conflicts(citations)

        stale = [c for c in citations if c.staleness in (Staleness.STALE, Staleness.EXPIRED)]
        if stale:
            oldest = min(stale, key=lambda c: as_utc(c.updated_at) if c.updated_at else NOW)
            notices.append(
                f"Best supporting evidence includes '{oldest.title}', last updated "
                f"{oldest.updated_at:%d %b %Y}" if oldest.updated_at else
                f"Best supporting evidence includes '{oldest.title}', which has no recorded date"
            )
        # Documents withheld for age are the dangerous silent case. If the
        # freshness policy archived a candidate that *outranked* everything
        # cited, the reader is being answered from worse sources and has no way
        # to tell — which is precisely the "confidently wrong" failure this
        # system exists to prevent. Say so, and name the best one withheld.
        archived = [
            c for c in candidates.values()
            if c.dropped_by == "archive_tier" and c.fusion_rank
        ]
        if archived:
            best_kept = min(
                (c.fusion_rank for c in candidates.values()
                 if c.dropped_by is None and c.fusion_rank),
                default=None,
            )
            best_withheld = min(archived, key=lambda c: c.fusion_rank)
            outranked = best_kept is None or best_withheld.fusion_rank < best_kept
            notices.append(
                f"{len(archived)} matching document(s) were withheld as archived by the "
                f"freshness policy (older than {config.freshness.expired_days} days)"
                + (
                    f" — including the closest match to this question. Raise the "
                    f"connector's expiry window, or pin the document, if this corpus "
                    f"is documentation that stays correct as it ages."
                    if outranked else "."
                )
            )

        unknown = [c for c in citations if c.staleness is Staleness.UNKNOWN_AGE]
        if unknown:
            notices.append(
                f"{len(unknown)} cited document(s) have no parseable date; treat their "
                "currency as unverified and check the field map."
            )

        return citations, conflicts, notices


def _span_of(chunk) -> str:
    """The passage a reader sees.

    The chunk keeps its heading inline because the embedding needs it — the
    heading path is what separates "Rate limits" under two different parents.
    The *citation* renders that path above the span already, so leaving the
    markdown heading in the text shows it twice, once as chrome and once as
    literal `## Detail`.
    """
    text = chunk.text
    lines = text.split("\n")
    if lines and lines[0].lstrip().startswith("#"):
        text = "\n".join(lines[1:]).lstrip("\n")
    return text[:600]


def _fingerprint(text: str) -> str:
    import hashlib
    import re

    normalised = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return hashlib.blake2b(normalised.encode(), digest_size=12).hexdigest()


_CLAIM = __import__("re").compile(r"\b(\d+(?:\.\d+)?)\s+([a-z%][a-z]{1,14})\b", __import__("re").I)

#: Money, which the number-plus-unit pattern misses entirely: "$35 is assessed"
#: parses as the quantity 35 with the unit "is", and "is" is a stop word. In a
#: bank most quantified claims are amounts, so without this the conflict
#: surface is blind to the disagreements that matter most — a fee schedule
#: saying $35 and a branch card saying $15 produced no conflict at all.
_MONEY = __import__("re").compile(r"[$\u00a3\u20ac]\s?(\d[\d,]*(?:\.\d+)?)")
_CLAIM_STOP = frozenset(
    "the a an of to and or in on for with by from at as is are was were be per "
    "and following within least most more than about over under".split()
)

#: Relative ages printed as page furniture — "Last updated 3 years ago" — are
#: not claims about anything. Two help pages footered "2 years ago" and "3
#: years ago" were being reported as sources disagreeing about a quantity,
#: which is worse than useless: a conflict panel that cries wolf is one nobody
#: reads on the day two documents really do disagree.
_RELATIVE_AGE = __import__("re").compile(
    r"\b(?:last\s+(?:updated|modified|edited|reviewed)|updated|posted|published)\b"
    r"[^.\n]{0,40}?\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago",
    __import__("re").I,
)

#: Units that are dates rather than measures. "15 April" and "1 March" are not
#: competing claims about the same quantity, they are two effective dates.
_DATE_UNITS = frozenset(
    "january february march april may june july august september october "
    "november december monday tuesday wednesday thursday friday saturday "
    "sunday am pm".split()
)

#: Dimensions along which two documents are *allowed* to disagree because they
#: are about different things. A per-state statutory summary saying 10 business
#: days and another saying 4 is not a contradiction — it is federalism.
#:
#: This came straight out of the POA corpus: the first run flagged Texas
#: against Florida, which is the kind of false positive that trains people to
#: ignore the conflict banner. A banner nobody reads is worse than none.
#:
#: The rule is deliberately asymmetric. Two documents that *both* declare a
#: scope and declare different ones are parallel. A document that declares
#: none applies everywhere, so it can still contradict any of them — which is
#: exactly the case that matters here: an internal procedure misstating the
#: statutory window is wrong in every state, and suppressing that would hide
#: the one finding worth having.
_SCOPE_LABEL_PREFIXES = ("state-", "region-", "jurisdiction-", "entity-", "product-")


def _scope_key(citation: Citation) -> frozenset[str]:
    """The scope dimensions a citation declares, from its labels."""
    return frozenset(
        label for label in getattr(citation, "labels", ()) or ()
        if label.startswith(_SCOPE_LABEL_PREFIXES)
    )


def _detect_conflicts(citations: list[Citation]) -> list[Conflict]:
    """Quantified claims that disagree across *different documents*.

    A claim is a number followed by a unit ("18 weeks", "60 days", "45 per"),
    together with the significant terms around it. Two claims conflict when
    they share a unit and enough surrounding terms to be about the same thing,
    and give different values.

    Deliberately narrow and deterministic — no model call (CNT-PAR-06). It is
    not a general contradiction detector and does not pretend to be. Its job is
    to guarantee that the presentation rule of CNT-RET-20 has something to
    present, so that a disagreement is never silently resolved by ranking.

    Two properties matter and are easy to get wrong:
      * a document is never in conflict with itself — a policy stating the same
        figure in prose and again in a table is agreement, not disagreement;
      * a conflict across authority tiers is still reported, with the tiers
        named, so a reader can see that the authoritative source says one thing
        and a supporting one says another.
    """
    claims: list[tuple[Citation, str, str, frozenset[str]]] = []

    for citation in citations:
        text = citation.span
        tokens = _significant(text)
        # Strip the furniture before looking for claims, rather than after:
        # the surrounding-terms window would otherwise still be polluted by it.
        text = _RELATIVE_AGE.sub(" ", text)

        for match in _MONEY.finditer(text):
            value = match.group(1).replace(",", "")
            window = _significant(
                text[max(0, match.start() - 120) : match.end() + 120]
            ) or tokens
            claims.append((citation, value, "currency", window))

        for match in _CLAIM.finditer(text):
            value, unit = match.group(1), match.group(2).lower()
            if unit in _CLAIM_STOP or unit in _DATE_UNITS:
                continue
            if len(value) == 4 and value.startswith("20"):
                continue
            window = _significant(
                text[max(0, match.start() - 120) : match.end() + 120]
            ) or tokens
            claims.append((citation, value, unit, window))

    conflicts: list[Conflict] = []
    seen: set[tuple[str, str]] = set()

    for i, (cite_a, value_a, unit_a, terms_a) in enumerate(claims):
        for cite_b, value_b, unit_b, terms_b in claims[i + 1 :]:
            if cite_a.doc_id == cite_b.doc_id:
                continue
            if unit_a != unit_b or value_a == value_b:
                continue
            # Two documents scoped to different jurisdictions are parallel, not
            # contradictory. Only compare where the scopes match, or where
            # neither declares one.
            scope_a, scope_b = _scope_key(cite_a), _scope_key(cite_b)
            if scope_a and scope_b and scope_a != scope_b:
                continue
            shared = terms_a & terms_b
            if len(shared) < 2:
                continue
            key = tuple(sorted((cite_a.doc_id, cite_b.doc_id))) + (unit_a,)
            if key[:2] in seen:
                continue
            seen.add(key[:2])
            subject = " ".join(sorted(shared)[:4]) + f" ({unit_a})"
            conflicts.append(Conflict(subject=subject, citations=(cite_a, cite_b)))

    return conflicts


def _significant(text: str) -> frozenset[str]:
    import re

    return frozenset(
        t for t in re.findall(r"[a-z]{4,}", text.lower()) if t not in _CLAIM_STOP
    )


def _reciprocal_rank_fusion(
    channel_results: dict[str, list[str]], k: int = 60
) -> list[tuple[str, float, dict[str, int]]]:
    """RRF (CNT-RET-04).

    Rank-based on purpose: PGP scores are cosine similarities from a model we
    do not control, ECM scores are BM25-family relevance on a different scale.
    Merging them numerically produces an ordering that looks principled and is
    arbitrary.
    """
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for channel, doc_ids in channel_results.items():
        for position, doc_id in enumerate(doc_ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)
            ranks.setdefault(doc_id, {})[channel] = position
    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [(doc_id, score, ranks[doc_id]) for doc_id, score in ordered]


class PopulationResult(BaseModel):
    """What the connector can see, and what it could not read.

    The failure this exists for: a required field the map does not cover makes
    every document fail to map, and a population of zero looks identical to a
    knowledgebase that is empty. One is a five-second fix on the mapping screen;
    the other is a conversation with the knowledgebase owner.
    """

    documents: list[DocMetadata] = []
    mapping_failures: int = 0
    sample_errors: tuple[str, ...] = ()


def scope_population_detailed(index, connector: Connector) -> PopulationResult:
    population: list[DocMetadata] = []
    failures = 0
    errors: list[str] = []
    cursor: str | None = None
    while True:
        page = index.list_documents(connector.kb_id, cursor=cursor)
        for hit in page.hits:
            outcome = apply_map(connector.field_map, hit.metadata, connector.kb_id)
            if outcome.metadata is not None:
                population.append(outcome.metadata)
            else:
                failures += 1
                if len(errors) < 3:
                    errors.append(f"{hit.doc_id}: {'; '.join(outcome.errors)}")
        cursor = page.cursor
        if not cursor:
            break
    return PopulationResult(
        documents=population, mapping_failures=failures, sample_errors=tuple(errors)
    )


def scope_population(index, connector: Connector, principal: str = "service") -> list[DocMetadata]:
    """Every document the connector *could* see, mapped to canonical metadata.

    Powers the scope preview, the add/remove diff and the corpus browser — all
    of which read the one effective-corpus definition (CNT-SCP-15). When 'how
    many documents are in this connector' has two implementations, one of them
    is wrong, and it is always the one shown to the customer.
    """
    return scope_population_detailed(index, connector).documents
