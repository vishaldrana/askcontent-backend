"""The endpoint the embedded widget calls.

Separate from `/api/chat/stream` because the callers are not comparable. The
console is used by an administrator who has already authenticated to this
product and chosen a connector from a list. This is used by a script tag on
somebody else's page, and every one of those differences is a way in:

  * the caller **names no connector**. It presents a publishable key, and the
    key resolves to exactly one connector server-side. There is no field in
    which to put a connector, so a client cannot widen its own reach — the same
    reasoning as the closed grammar, applied to the client.
  * the caller **names no role**. It presents the visitor's own token, and the
    principal is derived from that. A widget that could choose who it was
    asking as would make every access rule advisory.
  * the page it runs on is checked against the embed's origin allowlist, so a
    leaked key cannot be pasted onto an unrelated site.

The response is SSE with **named** events — `token`, `evidence`, `error` —
rather than the console's single `data:` envelope. That is the widget's
contract and it is a better one for this shape: the client needs to tell prose
from evidence, and named events make that a property of the frame rather than
a field it has to parse and switch on.
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from ..config import settings
from ..domain.retrieval_spec import Intent, RetrievalSpec

logger = logging.getLogger("askcontent.widget")

#: Mounted as its own application rather than added to the main router.
#:
#: The console's CORS policy is a short static allowlist, and it must stay
#: that way. This endpoint is the opposite: it is called from customer domains
#: nobody knows at build time. One `CORSMiddleware` cannot express both, and
#: the middleware answers preflights itself — so a permissive rule here would
#: have to be a permissive rule for the admin API too. A sub-application gives
#: each the policy it needs.
router = APIRouter()
S = settings.db_schema


def _cors(origin: str | None) -> dict[str, str]:
    """CORS for a widget that runs on other people's sites.

    The console's static allowlist cannot serve this endpoint: the whole point
    is that it is called from a customer's own domain, which we do not know at
    build time.

    The origin is **reflected**, including when it is not on the embed's
    allowlist. That sounds backwards and is not: CORS is not the security
    boundary here — `_origin_allowed` is, and it runs on every request and
    refuses with a 403. Blocking at the CORS layer instead would replace that
    explanatory refusal with "Failed to fetch" in the browser console, which
    is the error somebody spends an afternoon on.

    No credentials are allowed, because none are used: identity travels in an
    Authorization header the page sets deliberately, not in an ambient cookie.
    """
    return {
        "access-control-allow-origin": origin or "*",
        "access-control-allow-headers": "content-type, authorization, x-askcontent-key",
        "access-control-allow-methods": "POST, OPTIONS",
        "access-control-max-age": "600",
        "vary": "Origin",
    }


@router.options("/ask")
def widget_preflight(origin: str | None = Header(default=None)):
    from fastapi.responses import Response

    return Response(status_code=204, headers=_cors(origin))


@router.options("/starters")
def widget_starters_preflight(origin: str | None = Header(default=None)):
    from fastapi.responses import Response

    return Response(status_code=204, headers=_cors(origin))


def _frame(event: str, payload: str) -> str:
    """One SSE frame with a named event.

    Payload newlines are re-emitted as separate `data:` lines, which is what
    the format requires; a raw newline inside one would silently truncate the
    frame at the first line.

    Exactly one space after the colon. The reader strips one, so a token that
    begins with a space survives — and tokens routinely do. Two spaces here, or
    a reader that trims all leading whitespace, welds the answer together into
    "thentypeyourURL".
    """
    body = "\n".join(f"data: {line}" for line in payload.split("\n"))
    return f"event: {event}\n{body}\n\n"


def _embed_for(key: str) -> dict:
    from .extra import _sessions

    with _sessions()() as session:
        row = session.execute(text(f"""
            SELECT e.id, e.name, e.allowed_origins, e.is_active,
                   c.slug AS connector, c.state
            FROM {S}.embed e
            JOIN {S}.connector c ON c.id = e.connector_id
            WHERE e.publishable_key = :k
        """), {"k": key}).mappings().one_or_none()

    if row is None:
        # Deliberately the same answer as a disabled embed. Distinguishing them
        # would turn this endpoint into an oracle for which keys exist.
        raise HTTPException(404, "This assistant is not configured on this page.")
    if not row["is_active"]:
        raise HTTPException(404, "This assistant is not configured on this page.")
    return dict(row)


def _origin_allowed(embed: dict, origin: str | None) -> bool:
    """An empty allowlist permits nothing.

    The safe default for a key that has just been created, and the opposite of
    what "no rules yet" usually means — which is exactly why it is stated here
    rather than left to a falsy check somewhere.
    """
    allowed = list(embed["allowed_origins"] or [])
    if not allowed:
        return False
    if origin is None:
        # A same-origin or server-side call sends no Origin header. It cannot
        # be a browser on another site, which is what the allowlist defends
        # against.
        return True
    parts = urlsplit(origin)
    return f"{parts.scheme}://{parts.netloc}" in {a.rstrip("/") for a in allowed}


@router.get("/starters")
def widget_starters(
    x_askcontent_key: str = Header(default=""),
    origin: str | None = Header(default=None),
):
    """What to offer a visitor who has not asked anything yet.

    The same suggestions the console shows, resolved through the publishable
    key rather than a connector name — the widget cannot name a connector, and
    this endpoint must not become the first place it can.

    Origin-checked like /ask. A corpus's section titles are not secret, but
    they do describe what a company documents, and an endpoint that hands them
    to any page holding a leaked key is a slower version of the same leak.
    """
    from fastapi.responses import JSONResponse

    from ..domain.starters import choose
    from ..services.retrieval import scope_population
    from .extra import _connector_id, _sessions

    if not x_askcontent_key:
        raise HTTPException(404, "This assistant is not configured on this page.")

    embed = _embed_for(x_askcontent_key)
    if not _origin_allowed(embed, origin):
        raise HTTPException(403, "not permitted on this origin", headers=_cors(origin))
    if embed["state"] != "active":
        raise HTTPException(404, "This assistant is not configured on this page.")

    slug = embed["connector"]
    platform = _platform()
    connector = platform.registry.get(slug)

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        weights = {
            r["doc_id"]: r["chunks"]
            for r in session.execute(text(f"""
                SELECT d.doc_id AS doc_id, count(*) AS chunks
                  FROM {S}.document_chunk c
                  JOIN {S}.document d ON d.id = c.document_id
                 WHERE c.connector_id = :c
                 GROUP BY d.doc_id
            """), {"c": cid}).mappings().all()
        }

    starters = choose(scope_population(platform.index, connector), weights=weights)
    return JSONResponse(
        {"starters": [s.model_dump(mode="json") for s in starters]},
        headers=_cors(origin),
    )


@router.post("/ask")
async def widget_ask(
    request: Request,
    x_askcontent_key: str = Header(default=""),
    authorization: str = Header(default=""),
    origin: str | None = Header(default=None),
):
    from .extra import (
        _answer_about_the_corpus,
        _glossary_for,
        _instructions_for,
        _principal_for_role,
        _run_answer,
        _sessions,
    )

    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "no question")

    # What the host says its page is showing. Bounded on the way in, because
    # this is text from somebody else's page arriving in our prompt, and it
    # arrives on every question.
    from ..domain.page_context import from_payload

    page = from_payload(body.get("context"))

    if not x_askcontent_key:
        raise HTTPException(404, "This assistant is not configured on this page.")

    embed = _embed_for(x_askcontent_key)

    if not _origin_allowed(embed, origin):
        raise HTTPException(
            403,
            f"'{origin}' is not on this embed's origin allowlist. Add it on the "
            f"Embeds screen.",
            headers=_cors(origin),
        )

    if not authorization.strip():
        # WGT-02 — there is no anonymous mode. An assistant that does not know
        # who is asking cannot honour "no answer cites a document the asker
        # cannot open", so it must not answer at all rather than answer as
        # nobody.
        raise HTTPException(
            401, "This assistant could not verify who you are.", headers=_cors(origin)
        )

    if embed["state"] != "active":
        raise HTTPException(
            404, "This assistant is not configured on this page.", headers=_cors(origin)
        )

    slug = embed["connector"]
    platform = _platform()
    connector = platform.registry.get(slug)

    # The visitor's identity comes from their token, never from the request
    # body. `user` in the body is a display hint and is not trusted for access.
    principal = _principal_from_token(authorization, body.get("user"))

    def generate():
        started = time.monotonic()

        def step(label: str, at: float) -> str:
            """One stage, named, with what it cost.

            Stage names and timings only — never the trace, which names the
            documents a visitor was refused and is therefore a question about
            somebody else's access. What is left is what the console's own
            steps header shows: that the work happened, and how long it took.
            """
            return _frame("step", json.dumps({
                "label": label, "ms": round((time.monotonic() - at) * 1000),
            }))

        try:
            # Same routing as the console. "What can you help with" is the
            # first thing a visitor types into a widget, and a refusal there
            # is the whole product's first impression.
            # Skipped when the host has told us what its page shows: "what is
            # this?" is a question about the screen then, not about the
            # collection, and answering with a description of the corpus is a
            # confident non-answer.
            about = None if page is not None else _answer_about_the_corpus(slug, question)
            if about is not None:
                yield step("Described the collection", started)
                yield _frame("token", about)
                yield _frame("evidence", json.dumps({
                    "citations": [], "conflicts": [], "notices": [],
                    "refused": False, "refusal_reason": None,
                }))
                _record_use(embed["id"])
                return

            spec = RetrievalSpec(
                intent=Intent.LOOKUP,
                scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
                question=question,
                channels=connector.retrieval.channels,
                k_per_channel=connector.retrieval.k_per_channel,
            )
            search_at = time.monotonic()
            evidence = platform.retrieval.retrieve(
                connector, spec, principal, glossary=_glossary_for(slug)
            )
            yield step(
                f"Searched {len(evidence.citations)} passage"
                f"{'' if len(evidence.citations) == 1 else 's'}",
                search_at,
            )

            answer_at = time.monotonic()
            outcome = None
            for chunk, result in _run_answer(
                platform, question, evidence.citations, (),
                _instructions_for(slug), evidence.trace.synonyms, page,
            ):
                if result is not None:
                    outcome = result
                elif chunk:
                    yield _frame("token", chunk)

            yield step("Composed the answer", answer_at)

            payload = evidence.model_dump(mode="json")
            # Reported so the widget can say where the answer came from. A
            # sentence marked [page] is not backed by anything a reader can
            # open, and the interface has to be able to say so.
            payload["used_page"] = bool(outcome is not None and outcome.used_page)
            if outcome is not None and not outcome.supported:
                # Nothing supported the answer, so nothing may be shown as
                # supporting it — and the widget refuses to render prose with
                # no evidence, which is the behaviour we want.
                payload["citations"] = []
                payload["refused"] = True
                payload["refusal_reason"] = outcome.reason or (
                    "This is not covered by the documents I can see."
                )
            # The trace is not sent. It names documents a visitor was refused,
            # which is a question about somebody else's access.
            payload.pop("trace", None)

            yield _frame("evidence", json.dumps(payload, default=str))
            _record_use(embed["id"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("widget ask failed")
            yield _frame("error", str(exc))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            **_cors(origin),
            "cache-control": "no-cache",
            # Streaming dies behind a buffering proxy, and the widget's
            # fallback then renders the whole answer at once rather than
            # failing — but telling nginx not to buffer is cheaper than
            # relying on the fallback.
            "x-accel-buffering": "no",
        },
    )


def _principal_from_token(authorization: str, hint: str | None) -> str:
    """The visitor's principal, from the token they presented.

    ╔════════════════════════════════════════════════════════════════════════╗
    ║  REPLACE THIS. The token is not verified.                              ║
    ║                                                                        ║
    ║  In production this is where the host application's token is checked — ║
    ║  a signed JWT whose signature, audience and expiry are validated, and  ║
    ║  whose subject becomes the principal. Until then the widget is safe to ║
    ║  demonstrate and unsafe to expose, because anyone can present any      ║
    ║  token and be believed.                                                ║
    ╚════════════════════════════════════════════════════════════════════════╝
    """
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "This assistant could not verify who you are.")
    return f"user:{hint}" if hint else "group:all-staff"


def _record_use(embed_id) -> None:
    """Counted so that "is this embed still in use" has an answer other than
    "delete it and find out"."""
    from .extra import _sessions

    try:
        with _sessions()() as session:
            session.execute(text(f"""
                UPDATE {S}.embed
                   SET session_count = session_count + 1, last_used_at = now()
                 WHERE id = :id
            """), {"id": embed_id})
            session.commit()
    except Exception:  # noqa: BLE001
        # Never fail an answer over a counter.
        logger.warning("could not record embed use", exc_info=True)


def _platform():
    from .extra import _platform as inner

    return inner()
