"""HTTP surface.

Mirrors the askdb console's shape: connection-scoped screens under a connector,
a discovery surface above them, and a chat surface that returns evidence rather
than prose.
"""

from __future__ import annotations

import datetime as dt
import os

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ..adapters.parsers.registry import capabilities
from ..bootstrap import Platform, build, build_postgres
from ..domain.catalog import as_utc, classify, staleness
from ..domain.documents import Sensitivity
from ..domain.retrieval_spec import Intent, ModelRetrievalRequest, RetrievalSpec
from ..domain.scope import KnowledgeScope, diff, evaluate
from ..services.mapping import suggest_map, validate_map
from ..services.probe import probe
from ..services.registry import ConnectorState
from ..services.retrieval import NOW, scope_population

def _platform() -> Platform:
    """Postgres when configured, mocks otherwise.

    One switch, in the composition root, and nothing above it changes — which is
    the property the two-port split exists to give us (CNT-FED-05).
    """
    import os

    if os.environ.get("ASKCONTENT_DATABASE_URL"):
        return build_postgres()
    return build(simulate_latency=False)


platform: Platform = _platform()

app = FastAPI(title="askcontent", version="0.1.0")
#: Two audiences, two policies, and one middleware chain to serve them.
#:
#: The console is ours and is called from origins we list. The widget is called
#: from customer domains nobody knows at build time — that is the point of it.
#: `CORSMiddleware` answers preflights itself and wraps mounted applications,
#: so a mount cannot carry a looser policy than its parent; the parent replies
#: first and the sub-application's rules never run.
#:
#: Rather than loosen the console's policy to accommodate the widget, the
#: policy is chosen by path. The widget's own protection is not CORS anyway:
#: it is the per-embed origin allowlist, checked on every request, which
#: refuses with an explanation a developer can act on instead of the browser's
#: opaque "Failed to fetch".
WIDGET_PREFIX = "/api/widget"
CONSOLE_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
WIDGET_HEADERS = "content-type, authorization, x-askcontent-key"


@app.middleware("http")
async def cors_by_audience(request: Request, call_next):
    origin = request.headers.get("origin")
    widget = request.url.path.startswith(WIDGET_PREFIX)

    if request.method == "OPTIONS" and origin:
        allowed = origin if widget else (origin if origin in CONSOLE_ORIGINS else None)
        if allowed is None:
            return Response(status_code=403)
        return Response(
            status_code=204,
            headers={
                "access-control-allow-origin": allowed,
                "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "access-control-allow-headers": (
                    WIDGET_HEADERS if widget else request.headers.get(
                        "access-control-request-headers", "*"
                    )
                ),
                "access-control-max-age": "600",
                "vary": "Origin",
            },
        )

    response = await call_next(request)
    if origin and (widget or origin in CONSOLE_ORIGINS):
        response.headers["access-control-allow-origin"] = origin
        response.headers["vary"] = "Origin"
    return response


# ---------------------------------------------------------------- discovery


from .extra import router as extra_router  # noqa: E402

app.include_router(extra_router)

from .widget import router as widget_router  # noqa: E402

app.include_router(widget_router, prefix="/api/widget")


@app.get("/widget/embed.js")
def widget_bundle():
    """Serve the built widget.

    The install snippet points here, so the bundle has to be reachable from
    wherever the API is — and in most deployments that is the only host the
    embedding page is already allowed to talk to. A CDN is better and is a
    deployment decision; this is what makes the snippet on the Embeds screen
    true out of the box rather than aspirational.
    """
    import pathlib

    from fastapi.responses import FileResponse, JSONResponse

    for candidate in (
        pathlib.Path(__file__).resolve().parents[3] / "widget" / "embed.js",
        pathlib.Path.home() / "IdeaProjects" / "askcontent-widget" / "dist" / "embed.js",
    ):
        if candidate.is_file():
            return FileResponse(
                candidate,
                media_type="application/javascript",
                headers={
                    # The bundle is versioned by deployment, not by URL, so it
                    # must not be cached for long — a stale widget talking to a
                    # new API is a bug nobody can reproduce.
                    "cache-control": "public, max-age=300",
                    # The whole point is being loaded by another origin.
                    "access-control-allow-origin": "*",
                },
            )

    return JSONResponse(
        {"detail": "the widget bundle is not built; run `npm run build` in askcontent-widget"},
        status_code=404,
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "parser_capabilities": capabilities(),
        "reranker": {
            "id": getattr(platform.reranker, "reranker_id", "unknown"),
            "version": getattr(platform.reranker, "reranker_version", "unknown"),
            "floor": getattr(platform.reranker, "score_floor", None),
        },
        "embedder": {
            "model": platform.embedder.model_id,
            "dimension": platform.embedder.dimension,
        },
        # Reported from the live objects, never as a literal. A health endpoint
        # that names the adapter it *expects* rather than the one that is wired
        # will confidently tell you the mocks are running when they are not.
        "sources": {
            "index": type(platform.index).__name__,
            "repository": type(platform.repository).__name__,
        },
        "database": _database_health(),
    }


def _database_health() -> dict:
    if not os.environ.get("ASKCONTENT_DATABASE_URL"):
        return {"configured": False}
    try:
        from ..db.session import healthcheck

        return {"configured": True, **healthcheck()}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "error": str(exc)}


@app.get("/api/knowledgebases")
def list_knowledgebases() -> list[dict]:
    """Discovery: everything visible in PGP, with registration state
    (CNT-ADM-03, CNT-ADM-04)."""
    registered = {c.kb_id: c for c in platform.registry.list()}
    out = []
    for kb in platform.index.list_knowledgebases():
        connector = registered.get(kb.kb_id)
        out.append({
            **kb.model_dump(mode="json"),
            "state": str(connector.state) if connector else "unregistered",
            "connector_id": connector.connector_id if connector else None,
        })
    return out


@app.get("/api/knowledgebases/{kb_id}")
def describe_knowledgebase(kb_id: str) -> dict:
    try:
        return platform.index.describe(kb_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, f"unknown knowledgebase {kb_id}") from None


@app.post("/api/knowledgebases/{kb_id}/suggest-map")
def suggest_field_map(kb_id: str) -> dict:
    """A starting point for the mapping editor, never an applied default."""
    descriptor = platform.index.describe(kb_id)
    return suggest_map(kb_id, [f.name for f in descriptor.fields]).model_dump(mode="json")


class ValidateMapRequest(BaseModel):
    field_map: dict


@app.post("/api/knowledgebases/{kb_id}/validate-map")
def validate_field_map(kb_id: str, body: ValidateMapRequest) -> dict:
    from ..services.mapping import FieldMap

    sample = [hit.metadata for hit in platform.index.list_documents(kb_id).hits]
    validation = validate_map(FieldMap.model_validate(body.field_map), sample)
    return validation.model_dump(mode="json") | {"can_activate": validation.can_activate}


class ResolveUrlsRequest(BaseModel):
    text: str
    kb_ids: tuple[str, ...] = ()


@app.post("/api/urls/resolve")
def resolve_urls(body: ResolveUrlsRequest) -> dict:
    """Resolve pasted links to documents that already exist in the index.

    Nothing is fetched. The naive reading — "paste a URL, we'll download it" —
    creates a second copy of a document the system of record already holds, and
    that copy diverges the moment the original changes.
    """
    from ..services.url_resolution import UrlResolutionService

    summary = UrlResolutionService(platform.index).resolve_text(body.text, body.kb_ids)
    return summary.model_dump(mode="json") | {
        "summary_line": summary.line(),
        "needs_review": summary.needs_review,
    }


# ---------------------------------------------------------------- connectors


@app.get("/api/connectors")
def list_connectors() -> list[dict]:
    out = []
    for connector in platform.registry.list():
        population = scope_population(platform.index, connector)
        in_scope = sum(1 for m in population if evaluate(connector.scope, m).in_scope)
        out.append({
            "connector_id": connector.connector_id,
            "name": connector.name,
            "business_group": connector.business_group,
            "kb_id": connector.kb_id,
            "state": str(connector.state),
            "version": connector.version,
            "corpus_size": in_scope,
            "visible_documents": len(population),
            "sensitivity_ceiling": str(connector.scope.sensitivity_ceiling),
        })
    return out


@app.get("/api/connectors/{connector_id}")
def get_connector(connector_id: str) -> dict:
    connector = _connector(connector_id)
    return connector.model_dump(mode="json")


@app.post("/api/connectors/{connector_id}/state")
def set_state(connector_id: str, body: dict) -> dict:
    """Suspension takes effect on the next query (CNT-ADM-05)."""
    state = ConnectorState(body["state"])
    connector = platform.registry.set_state(connector_id, state, body.get("actor", "admin"))
    return {"connector_id": connector.connector_id, "state": str(connector.state)}


# --------------------------------------------------------------------- scope


class ScopePreviewRequest(BaseModel):
    scope: dict


@app.post("/api/connectors/{connector_id}/scope/preview")
def preview_scope(connector_id: str, body: ScopePreviewRequest) -> dict:
    """Preview and add/remove diff, before the scope can be saved
    (CNT-SCP-07..09). 'Add three hundred, remove eleven thousand' is a sentence
    that stops a mistake."""
    connector = _connector(connector_id)
    candidate = KnowledgeScope.model_validate(body.scope)
    population = scope_population(platform.index, connector)

    matched = [m for m in population if evaluate(candidate, m).in_scope]
    ceiling_rejected = sum(
        1 for m in population if m.sensitivity.rank > candidate.sensitivity_ceiling.rank
    )
    delta = diff(connector.scope, candidate, population)

    by_root: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_age: dict[str, int] = {}
    for meta in matched:
        by_root[meta.space or "—"] = by_root.get(meta.space or "—", 0) + 1
        key = str(meta.doc_type or "unclassified")
        by_type[key] = by_type.get(key, 0) + 1
        bucket = _age_bucket(meta.updated_at)
        by_age[bucket] = by_age.get(bucket, 0) + 1

    return {
        "matched": len(matched),
        "total_visible": len(population),
        "total_bytes": sum(m.size_bytes or 0 for m in matched),
        "rejected_by_ceiling": ceiling_rejected,
        "by_root": by_root,
        "by_type": by_type,
        "by_age": by_age,
        "diff": delta.model_dump(mode="json"),
        "diff_summary": delta.summary(),
    }


@app.put("/api/connectors/{connector_id}/scope")
def update_scope(connector_id: str, body: ScopePreviewRequest) -> dict:
    connector = _connector(connector_id)
    candidate = KnowledgeScope.model_validate(body.scope)
    population = scope_population(platform.index, connector)
    delta = diff(connector.scope, candidate, population)
    updated = platform.registry.update_scope(
        connector_id, candidate, "admin", delta.model_dump(mode="json")
    )
    return {
        "connector_id": updated.connector_id,
        "version": updated.version,
        "diff": delta.model_dump(mode="json"),
        # The asymmetry, stated in the words the console must show
        # (CNT-SCP-12, CNT-SCR-04).
        "effect": "Narrowing takes effect on the next query. Widening takes "
                  "effect after the next ingest run.",
    }


# -------------------------------------------------------------------- corpus


@app.get("/api/connectors/{connector_id}/corpus")
def corpus(connector_id: str) -> dict:
    """The effective corpus, with the single named rule that excluded each
    document (CNT-SCP-16). 'Why isn't the platform answering from this page?'
    must be a lookup, not an investigation."""
    connector = _connector(connector_id)
    population = scope_population(platform.index, connector)

    documents = []
    reasons: dict[str, int] = {}
    for meta in population:
        decision = evaluate(connector.scope, meta)
        classification = classify(meta, None)
        state = staleness(meta, connector.retrieval.freshness, NOW)
        if not decision.in_scope:
            key = str(decision.rule)
            reasons[key] = reasons.get(key, 0) + 1
        documents.append({
            "doc_id": meta.doc_id,
            "title": meta.title,
            "url": meta.url,
            "space": meta.space,
            "path": meta.path,
            "owner": meta.owner,
            "labels": list(meta.labels),
            "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
            "sensitivity": str(meta.sensitivity),
            "in_scope": decision.in_scope,
            "exclusion_rule": str(decision.rule) if decision.rule else None,
            "exclusion_detail": decision.detail,
            "doc_type": str(classification.doc_type),
            "doc_type_confidence": classification.confidence,
            "doc_type_evidence": list(classification.evidence),
            "doc_type_source": classification.source,
            "staleness": str(state),
        })

    documents.sort(key=lambda d: (not d["in_scope"], d["title"]))
    return {
        "in_scope": sum(1 for d in documents if d["in_scope"]),
        "excluded": sum(1 for d in documents if not d["in_scope"]),
        "exclusion_reasons": reasons,
        "documents": documents,
    }


# --------------------------------------------------------------------- probe


@app.post("/api/connectors/{connector_id}/probe")
def run_probe(connector_id: str, body: dict | None = None) -> dict:
    connector = _connector(connector_id)
    principal = (body or {}).get("principal", "service")
    result = probe(platform.index, platform.repository, connector, principal)
    return result.model_dump(mode="json") | {"passed": result.passed}


# ------------------------------------------------------------------ retrieve


class AskRequest(BaseModel):
    connector_id: str
    question: str
    principal: str = "user:asha"
    intent: Intent = Intent.LOOKUP


@app.post("/api/ask")
def ask(body: AskRequest) -> dict:
    """Returns evidence, not prose.

    Answer synthesis sits above this and may emit only sentences backed by one
    of these citations (CNT-RET-18). Keeping the boundary here is what makes
    'a claim with no supporting span is not emitted' enforceable rather than
    aspirational.
    """
    connector = _connector(body.connector_id)

    # What a model is permitted to emit. `channels` and `k_per_channel` are
    # absent from this type by construction (CNT-RET-15).
    model_request = ModelRetrievalRequest(intent=body.intent, question=body.question)

    spec = RetrievalSpec(
        intent=model_request.intent,
        scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
        question=model_request.question,
        terms=model_request.terms,
        filters=model_request.filters,
        doc_types=model_request.doc_types,
        freshness=model_request.freshness,
        authority=model_request.authority,
        channels=connector.retrieval.channels,          # server-populated
        k_per_channel=connector.retrieval.k_per_channel,  # server-populated
    )

    evidence = platform.retrieval.retrieve(connector, spec, body.principal)
    return evidence.model_dump(mode="json")


@app.post("/api/connectors/{connector_id}/diagnose")
def diagnose(connector_id: str, body: dict) -> dict:
    """Dry run with the full trace, executable as another principal
    (CNT-ADM-09, CNT-ADM-12)."""
    connector = _connector(connector_id)
    spec = RetrievalSpec(
        intent=Intent(body.get("intent", "lookup")),
        scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
        question=body["question"],
        channels=connector.retrieval.channels,
        k_per_channel=connector.retrieval.k_per_channel,
    )
    # Diagnose runs as a *role*, so it must carry that role's narrowing too —
    # a dry run that skips a gate the real query applies is a dry run that
    # certifies the wrong thing.
    from .extra import _glossary_for, _principal_for_role, _rules_for_role

    role = body.get("role")
    principal = (
        _principal_for_role(connector_id, role) if role
        else body.get("principal", "user:asha")
    )
    evidence = platform.retrieval.retrieve(
        connector, spec, principal,
        role_rules=_rules_for_role(connector_id, role),
        glossary=_glossary_for(connector_id),
    )

    payload = evidence.model_dump(mode="json")

    # The answer itself, not just the evidence behind it. Diagnose is where
    # somebody checks whether a change helped, and "did the retrieval improve"
    # is a different question from "is the answer better" — the second is the
    # one that ships. Composing it here makes this screen a test harness rather
    # than a window onto an intermediate stage.
    if body.get("answer", True):
        from .extra import _followups
        from .extra import _answering_for, _instructions_for, _run_answer

        text_out, outcome = "", None
        answer_model, answer_tone = _answering_for(connector_id)
        for chunk, result in _run_answer(
            platform, spec.question, evidence.citations, (),
            _instructions_for(connector_id), evidence.trace.synonyms,
            None, None, answer_tone, answer_model,
        ):
            if result is not None:
                outcome = result
            else:
                text_out += chunk

        payload["answer"] = text_out.strip()
        payload["grounded"] = bool(outcome and outcome.supported)
        payload["unsupported_reason"] = (
            outcome.reason if outcome and not outcome.supported else None
        )
        payload["cited"] = list(outcome.cited) if outcome else []
        payload["answered_by"] = {
            "provider": platform.answering.answerer.name,
            "model": platform.answering.answerer.model_id,
        }
        payload["followups"] = (
            _followups(platform, evidence.citations, spec.question)
            if outcome and outcome.supported
            else []
        )

    return payload


# -------------------------------------------------------------------- health


@app.get("/api/connectors/{connector_id}/health")
def connector_health(connector_id: str) -> dict:
    """Metrics that surface decay, each naming a likely cause and next action
    (CNT-ADM-13, CNT-ADM-14). A number an administrator cannot act on is
    decoration."""
    connector = _connector(connector_id)
    population = scope_population(platform.index, connector)
    in_scope = [m for m in population if evaluate(connector.scope, m).in_scope]

    unknown_dates = sum(1 for m in in_scope if m.updated_at is None)
    expired = sum(
        1 for m in in_scope
        if str(staleness(m, connector.retrieval.freshness, NOW)) in ("stale", "expired")
    )

    return {
        "corpus_size": len(in_scope),
        "metrics": [
            {
                "key": "passage_cache_hit_rate",
                "value": round(platform.passages.stats.hit_rate, 3),
                "cause": "Low after a parser or chunker version change, or when the "
                         "ECM exposes no document version.",
                "action": "If persistently low, ask the ECM owners for an etag — "
                          "without one the fetch cannot be skipped.",
            },
            {
                "key": "documents_without_parseable_date",
                "value": unknown_dates,
                "cause": "The updated_at field map is wrong, or the source omits it.",
                "action": "Open the mapping editor and check the live samples for "
                          "the date field. These documents are never treated as fresh.",
            },
            {
                "key": "stale_or_expired_documents",
                "value": expired,
                "cause": "The corpus is ageing faster than its owners review it.",
                "action": "Send the work list to the content owners; expired documents "
                          "are excluded from retrieval by default.",
            },
            {
                "key": "parser_capabilities",
                "value": capabilities(),
                "cause": "A missing extra means PDFs refuse rather than parse badly.",
                "action": "Install the 'pdf' extra in the worker image.",
            },
        ],
    }


@app.get("/api/audit")
def audit() -> list[dict]:
    return [entry.model_dump(mode="json") for entry in reversed(platform.registry.audit)]


# ------------------------------------------------------------------- helpers


def _connector(connector_id: str):
    try:
        return platform.registry.get(connector_id)
    except KeyError:
        raise HTTPException(404, f"unknown connector {connector_id}") from None


def _age_bucket(updated_at: dt.datetime | None) -> str:
    if updated_at is None:
        return "unknown"
    days = (NOW - as_utc(updated_at)).days
    if days < 90:
        return "0–90 days"
    if days < 365:
        return "90–365 days"
    if days < 1095:
        return "1–3 years"
    return "3+ years"
