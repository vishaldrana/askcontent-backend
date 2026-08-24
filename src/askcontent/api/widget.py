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
            FROM {S}.askcontent_embed e
            JOIN {S}.askcontent_connector c ON c.id = e.connector_id
            WHERE e.publishable_key = :k
        """), {"k": key}).mappings().one_or_none()

    if row is None:
        # Deliberately the same answer as a disabled embed. Distinguishing them
        # would turn this endpoint into an oracle for which keys exist.
        raise HTTPException(404, "This assistant is not configured on this page.")
    if not row["is_active"]:
        raise HTTPException(404, "This assistant is not configured on this page.")
    return dict(row)


#: Internal reasons, as a visitor should hear them. Matched on a fragment
#: rather than exhaustively, because a reason nobody has translated yet should
#: still come out as a sentence rather than as a stack of jargon.
_VISITOR_REASONS = (
    ("cited nothing", "I could not support that from anything I can show you, so I "
                      "have not answered it."),
    ("never supplied", "Something went wrong assembling that answer, so I have not "
                       "given it."),
    ("live values that were never supplied", "Something went wrong assembling that "
                                             "answer, so I have not given it."),
    ("not on the page", "I would have had to work that figure out rather than read "
                        "it, and a number I calculated is not one you can check."),
)


def _for_a_visitor(reason: str | None) -> str:
    if not reason:
        return "This is not covered by the documents I can see."
    lowered = reason.lower()
    for fragment, sentence in _VISITOR_REASONS:
        if fragment in lowered:
            return sentence
    return reason


def _context_source_for(slug: str) -> object:
    from .extra import _sessions

    with _sessions()() as session:
        return session.execute(text(f"""
            SELECT context_source FROM {S}.askcontent_connector WHERE slug = :s
        """), {"s": slug}).scalar_one_or_none()


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

    from ..services.live_context import parse as parse_source

    source = parse_source(_context_source_for(slug))

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        weights = {
            r["doc_id"]: r["chunks"]
            for r in session.execute(text(f"""
                SELECT d.doc_id AS doc_id, count(*) AS chunks
                  FROM {S}.askcontent_document_chunk c
                  JOIN {S}.askcontent_document d ON d.id = c.document_id
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
        _answering_for,
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

    from ..services.live_context import parse as parse_source

    source = parse_source(_context_source_for(slug))

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

            # Live values, if this connector names a source and the question
            # calls for one. After retrieval, because the corpus answers most
            # questions and costs nothing extra; before answering, because the
            # answer has to be able to use them.
            data = None
            if source is not None:
                from ..domain.groundedness import assess
                from ..services.live_context import read as read_live

                covers = assess(
                    question, [c.span for c in evidence.citations]
                ).covered
                live_at = time.monotonic()
                data = read_live(
                    source,
                    connector_id=slug,
                    question=question,
                    key=(page.key if page else ""),
                    corpus_covers=covers,
                    visitor_token=authorization.strip(),
                    fetcher=platform.context_fetcher,
                )
                if data is not None:
                    yield step(
                        f"Read {data.source}"
                        + (" · cached" if data.cached else "")
                        + (" · unavailable" if data.error else ""),
                        live_at,
                    )

            answer_at = time.monotonic()
            outcome = None
            answer_model, answer_tone = _answering_for(slug)
            for chunk, result in _run_answer(
                platform, question, evidence.citations, (),
                _instructions_for(slug), evidence.trace.synonyms, page, data,
                answer_tone, answer_model,
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
            payload["datapoints"] = (
                [p.model_dump(mode="json") for p in data.points]
                if data is not None and outcome is not None and outcome.used_data
                else []
            )
            # A source that was called and failed is named, never swallowed. An
            # answer that silently omits the figures it was meant to include
            # looks complete and is wrong about the one thing that was asked.
            # `alerts` and `notices` are separated because the reader has to
            # do different things with them.
            #
            # An alert changes how the answer should be read — the live figures
            # were unreachable, a sentence was removed — so it is shown beside
            # the answer. A notice is a remark about the evidence ("best
            # supporting evidence is from 2022"), which belongs with the
            # evidence, folded away with it until somebody wants to check.
            if data is not None and data.error:
                payload["alerts"] = list(payload.get("alerts") or []) + [data.notice()]
            if outcome is not None and not outcome.supported:
                # Nothing supported the answer, so nothing may be shown as
                # supporting it — and the widget refuses to render prose with
                # no evidence, which is the behaviour we want.
                payload["citations"] = []
                payload["refused"] = True
                # Withheld when *we* rejected it, not when the model declined.
                #
                # The distinction decides whether the prose is worth keeping.
                # A model that declines writes the explanation — "the passages
                # do not say which screen" — and that sentence is the most
                # useful thing on offer. An answer our own gates rejected is
                # the opposite: it reads as a confident answer and the reason
                # it was rejected is that it cannot be trusted, so leaving it
                # on screen under a caveat shows the reader the exact figure we
                # just decided they should not rely on.
                #
                # Only our gates set a reason; the model's own refusal does not.
                payload["withheld"] = bool(outcome.reason)

            # A sentence the figure gate removed. The answer stands; the
            # sentence that credited a worked-out number to something it was
            # not in does not — and the reader is told, because an answer
            # silently different from what was written is its own kind of
            # unattributable.
            if outcome is not None and outcome.revised is not None:
                payload["revised"] = outcome.revised
                payload["alerts"] = list(payload.get("alerts") or []) + [
                    "One sentence was left out: it worked out a figure rather "
                    "than reading one, and a number nobody can look up is not "
                    "one to act on."
                ]
                # Translated for the person reading it. `outcome.reason` is
                # written for whoever has to fix it — "the answer cited
                # nothing, so none of it can be checked" is precise, useful in
                # Diagnose, and to a visitor reads as the product talking to
                # itself. The precise reason stays on the console's side of the
                # wall; a stranger gets a sentence.
                payload["refusal_reason"] = _for_a_visitor(outcome.reason)
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
                UPDATE {S}.askcontent_embed
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
