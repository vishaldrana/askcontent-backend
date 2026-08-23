"""Collections, roles, glossary, retrieval configuration, embeds and settings.

Kept out of `app.py` so that file stays readable. Everything here reads and
writes the tables added in revisions 0001 and 0004; nothing here holds state in
the process.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from ..config import settings
from ..domain.urls import Rung

router = APIRouter()

S = settings.db_schema

#: Which rule kinds state a *place* and which state a *guess*. Additions from a
#: guess are never auto-accepted (CNT-COL-10): "everything in this folder" is a
#: statement about a place, and a new file there is inside the intent;
#: "everything matching these terms" drifts every time the index changes its
#: mind, and that is how a legal hold ends up answering a payroll question.
ENUMERABLE_KINDS = {
    "pgp_knowledgebase", "pgp_space", "pgp_path_prefix",
    "doc_id_list", "url_list", "crawl", "upload_batch",
    # Confluence, two ways. The native rule binds a space through the
    # Confluence API; the URL rule is `url_list` and works for a Confluence
    # page exactly as it works for anything else, which is why it is not a
    # Confluence-specific rule.
    "confluence_space",
}
PROPOSING_KINDS = {"pgp_query", "similar_to", "link_expansion"}

RULE_KINDS = sorted(ENUMERABLE_KINDS | PROPOSING_KINDS)


def _platform():
    from .app import platform

    return platform


def _org(session) -> uuid.UUID:
    return _platform().registry.org_id


def _org_of() -> uuid.UUID:
    return _platform().registry.org_id


def _sessions():
    from ..db.session import get_session_factory

    return get_session_factory()


# ---------------------------------------------------------------- collections


class CollectionCreate(BaseModel):
    name: str
    slug: str
    description: str = ""
    business_group: str = ""


class RuleCreate(BaseModel):
    kind: str
    effect: str = "include"
    config: dict = {}


@router.get("/api/collections")
def list_collections() -> list[dict]:
    with _sessions()() as session:
        rows = session.execute(text(f"""
            SELECT c.id, c.slug, c.name, c.description, c.business_group, c.state,
                   c.materialised_at, c.version, c.auto_accept_enumerable,
                   (SELECT count(*) FROM {S}.collection_rule r WHERE r.collection_id = c.id) AS rules,
                   (SELECT count(*) FROM {S}.collection_member m
                     WHERE m.collection_id = c.id AND m.state = 'member') AS members,
                   (SELECT count(*) FROM {S}.collection_member m
                     WHERE m.collection_id = c.id AND m.state = 'proposed') AS proposed
            FROM {S}.collection c WHERE c.org_id = :org ORDER BY c.name
        """), {"org": _org(session)}).mappings().all()
        return [dict(r) | {"id": str(r["id"])} for r in rows]


@router.post("/api/collections")
def create_collection(body: CollectionCreate) -> dict:
    with _sessions()() as session:
        row = session.execute(text(f"""
            INSERT INTO {S}.collection (org_id, slug, name, description, business_group)
            VALUES (:org, :slug, :name, :description, :group)
            ON CONFLICT (org_id, slug) DO UPDATE SET name = EXCLUDED.name,
                description = EXCLUDED.description, business_group = EXCLUDED.business_group
            RETURNING id
        """), {"org": _org(session), "slug": body.slug, "name": body.name,
               "description": body.description, "group": body.business_group}).scalar_one()
        session.commit()
        return {"id": str(row), "slug": body.slug}


@router.get("/api/collections/{slug}")
def get_collection(slug: str) -> dict:
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        rules = session.execute(text(f"""
            SELECT id, ordinal, kind, effect, config, enumerable, last_run_at,
                   last_candidate_count, capped
            FROM {S}.collection_rule WHERE collection_id = :cid ORDER BY ordinal, created_at
        """), {"cid": collection["id"]}).mappings().all()
        return dict(collection) | {
            "id": str(collection["id"]),
            "rules": [dict(r) | {"id": str(r["id"])} for r in rules],
            "rule_kinds": RULE_KINDS,
            "proposing_kinds": sorted(PROPOSING_KINDS),
        }


def _collection_row(session, slug: str):
    row = session.execute(text(f"""
        SELECT * FROM {S}.collection WHERE org_id = :org AND slug = :slug
    """), {"org": _org(session), "slug": slug}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, f"unknown collection {slug}")
    return row


@router.post("/api/collections/{slug}/rules")
def add_rule(slug: str, body: RuleCreate) -> dict:
    if body.kind not in RULE_KINDS:
        raise HTTPException(400, f"unknown rule kind {body.kind}")
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        ordinal = session.execute(text(
            f"SELECT coalesce(max(ordinal), -1) + 1 FROM {S}.collection_rule WHERE collection_id = :c"
        ), {"c": collection["id"]}).scalar_one()
        rid = session.execute(text(f"""
            INSERT INTO {S}.collection_rule
                (org_id, collection_id, ordinal, kind, effect, config, enumerable)
            VALUES (:org, :cid, :ord, :kind, :effect, :config, :enum) RETURNING id
        """), {"org": _org(session), "cid": collection["id"], "ord": ordinal,
               "kind": body.kind, "effect": body.effect,
               "config": json.dumps(body.config),
               "enum": body.kind in ENUMERABLE_KINDS}).scalar_one()
        session.commit()

    # The request returns; the worker does the work. Materialising fetches and
    # parses every member, which is not a thing to do inside an HTTP request.
    from ..worker import enqueue

    job = enqueue(
        _sessions(), _org_of(), "collection.materialise",
        collection_id=collection["id"], payload={"collection": slug},
    )
    return {
        "id": str(rid), "ordinal": ordinal,
        "enumerable": body.kind in ENUMERABLE_KINDS,
        "job_id": job,
        "note": "Queued. The worker materialises the collection, then enriches "
                "each member with its title, description and dates.",
    }


@router.delete("/api/collections/{slug}/rules/{rule_id}")
def delete_rule(slug: str, rule_id: str) -> dict:
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        session.execute(text(
            f"DELETE FROM {S}.collection_rule WHERE id = :r AND collection_id = :c"
        ), {"r": rule_id, "c": collection["id"]})
        session.commit()
    # Membership is deliberately untouched: a document another rule also claims
    # must not disappear because this one went away (CNT-COL-08). The next
    # materialisation proposes the removal, with a diff.
    return {"deleted": rule_id, "note": "membership unchanged until the next materialisation"}


class Materialise(BaseModel):
    apply: bool = False


@router.post("/api/collections/{slug}/materialise")
def materialise(slug: str, body: Materialise) -> dict:
    """Evaluate the rules and report the diff. Applies only when asked.

    Re-running produces a **proposal**, not a mutation (CNT-COL-09).
    """
    platform = _platform()
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        rules = session.execute(text(f"""
            SELECT id, kind, effect, config, enumerable FROM {S}.collection_rule
            WHERE collection_id = :c ORDER BY ordinal
        """), {"c": collection["id"]}).mappings().all()

        include: dict[str, dict] = {}
        exclude: set[str] = set()
        per_rule: list[dict] = []

        for rule in rules:
            hits, capped = _evaluate(platform, rule)
            per_rule.append({
                "rule_id": str(rule["id"]), "kind": rule["kind"],
                "effect": rule["effect"], "enumerable": rule["enumerable"],
                "candidates": len(hits), "capped": capped,
            })
            if rule["effect"] == "exclude":
                exclude.update(h["doc_id"] for h in hits)
                continue
            for hit in hits:
                entry = include.setdefault(hit["doc_id"], {**hit, "contributed_by": []})
                entry["contributed_by"].append(str(rule["id"]))

        # Exclude always beats include, whatever the order (CNT-COL-05).
        candidates = {k: v for k, v in include.items() if k not in exclude}

        existing = {
            r["doc_id"]: dict(r)
            for r in session.execute(text(f"""
                SELECT doc_id, state, pinned FROM {S}.collection_member WHERE collection_id = :c
            """), {"c": collection["id"]}).mappings().all()
        }

        # A human decision outranks the rules, in both directions.
        pinned_in = {d for d, r in existing.items() if r["pinned"] == "in"}
        pinned_out = {d for d, r in existing.items() if r["pinned"] == "out"}

        added = [d for d in candidates if d not in existing and d not in pinned_out]
        refresh = [d for d in candidates if d in existing]
        removed = [
            d for d, r in existing.items()
            if d not in candidates and r["state"] == "member" and d not in pinned_in
        ]

        result = {
            "collection": slug,
            "per_rule": per_rule,
            "added": len(added),
            "removed": len(removed),
            "unchanged": len(candidates) - len(added),
            "added_sample": [candidates[d].get("title") or d for d in added[:5]],
            "removed_sample": removed[:5],
            "applied": False,
            "summary": f"add {len(added)}, remove {len(removed)}, "
                       f"{len(candidates) - len(added)} unchanged",
        }

        if not body.apply:
            return result

        auto = collection["auto_accept_enumerable"]
        for doc_id in added + refresh:
            hit = candidates[doc_id]
            enumerable_only = all(
                any(r["enumerable"] for r in rules if str(r["id"]) == rid)
                for rid in hit["contributed_by"]
            )
            state = "member" if (auto and enumerable_only) else "proposed"
            session.execute(text(f"""
                INSERT INTO {S}.collection_member
                    (org_id, collection_id, doc_id, kb_id, title, url, contributed_by,
                     resolved_via, resolve_score, state)
                VALUES (:org, :cid, :doc, :kb, :title, :url, :by, :via, :score, :state)
                ON CONFLICT (collection_id, doc_id) DO UPDATE
                  SET contributed_by = EXCLUDED.contributed_by,
                      -- Refresh the display copy too. A member whose title was
                      -- captured before the index had one would otherwise show
                      -- its identifier forever.
                      title = coalesce(EXCLUDED.title, {S}.collection_member.title),
                      url = coalesce(EXCLUDED.url, {S}.collection_member.url),
                      kb_id = coalesce(EXCLUDED.kb_id, {S}.collection_member.kb_id),
                      missing_since = NULL
            """), {"org": _org(session), "cid": collection["id"], "doc": doc_id,
                   "kb": hit.get("kb_id"), "title": hit.get("title"), "url": hit.get("url"),
                   "by": hit["contributed_by"], "via": hit.get("rung"),
                   "score": hit.get("score"), "state": state})

        for doc_id in removed:
            # Retained and marked, not deleted — a rule edit is frequently a
            # mistake, and a hard delete turns a two-minute undo into a
            # re-materialisation (CNT-COL-11).
            session.execute(text(f"""
                UPDATE {S}.collection_member SET state = 'removed', missing_since = now()
                WHERE collection_id = :c AND doc_id = :d
            """), {"c": collection["id"], "d": doc_id})

        session.execute(text(
            f"UPDATE {S}.collection SET materialised_at = now(), version = version + 1 WHERE id = :c"
        ), {"c": collection["id"]})
        session.commit()

        result["applied"] = True
        result["auto_accepted"] = auto
        return result


def _evaluate(platform, rule) -> tuple[list[dict], bool]:
    """Turn one rule into candidates.

    Every branch is bounded, and a rule that hits its cap **says so** rather
    than truncating silently (CNT-COL-14): a silent truncation reads as
    "that's everything there is".
    """
    config = rule["config"] or {}
    kind = rule["kind"]
    cap = int(config.get("max", 2000))

    if kind == "pgp_knowledgebase":
        page = platform.index.list_documents(config["kb_id"], page_size=cap)
        hits = [{"doc_id": h.doc_id, "kb_id": h.kb_id,
                 "title": (h.metadata or {}).get("title") or h.doc_id,
                 "url": (h.metadata or {}).get("url")} for h in page.hits]
        return hits, bool(page.cursor)

    if kind in ("pgp_space", "pgp_path_prefix"):
        want = config.get("space") or config.get("prefix") or ""
        hits = []
        for kb in platform.index.list_knowledgebases():
            page = platform.index.list_documents(kb.kb_id, page_size=cap)
            for h in page.hits:
                meta = h.metadata or {}
                value = meta.get("space") or meta.get("practiceGroup") or ""
                path = meta.get("path") or ""
                if (kind == "pgp_space" and value == want) or (
                    kind == "pgp_path_prefix" and path.startswith(want)
                ):
                    hits.append({"doc_id": h.doc_id, "kb_id": kb.kb_id,
                                 "title": meta.get("title") or h.doc_id,
                                 "url": meta.get("url")})
        return hits[:cap], len(hits) > cap

    if kind == "url_list":
        from ..services.url_resolution import UrlResolutionService

        summary = UrlResolutionService(platform.index).resolve_text(config.get("text", ""))
        hits: list[dict] = []
        seen: set[str] = set()

        for res in summary.results:
            if not res.match:
                continue
            hits.append({"doc_id": res.match.doc_id, "kb_id": res.match.kb_id,
                         "title": res.match.title, "url": res.match.url,
                         "rung": str(res.match.rung), "score": res.match.score})
            seen.add(res.match.doc_id)

        # "Include sub-pages" — the option that makes pasting one link useful.
        #
        # Descendants are found by path prefix rather than by asking the source
        # for a page tree, so the same option works for Confluence, SharePoint,
        # a wiki or a plain intranet: every hierarchical system encodes the
        # hierarchy in the path, and the index already has it. A source-specific
        # tree API would be one integration per platform for the same answer.
        if config.get("include_descendants"):
            prefixes = [
                (h["url"] or "").split("://", 1)[-1].split("/", 1)[-1]
                for h in list(hits)
            ]
            for kb in platform.index.list_knowledgebases():
                page = platform.index.list_documents(kb.kb_id, page_size=cap)
                for h in page.hits:
                    meta = h.metadata or {}
                    path = (meta.get("path") or "").lstrip("/")
                    if h.doc_id in seen or not path:
                        continue
                    if any(p and path.startswith(p.lstrip("/") + "/") for p in prefixes):
                        seen.add(h.doc_id)
                        hits.append({"doc_id": h.doc_id, "kb_id": kb.kb_id,
                                     "title": meta.get("title") or h.doc_id,
                                     "url": meta.get("url"), "rung": "descendant"})

        return hits[:cap], len(hits) > cap

    if kind == "confluence_space":
        # NATIVE CONFLUENCE INTEGRATION
        #
        # REAL CALL: GET {CONFLUENCE}/wiki/api/v2/spaces?keys={key}
        #            GET {CONFLUENCE}/wiki/api/v2/spaces/{id}/pages?limit=250&cursor=
        #            (cursor pagination; `body-format=storage` only when the
        #             platform is the store as well as the index)
        #
        # OPEN Q, and it decides whether this rule is needed at all: **is the
        # Confluence space already in PGP?** If it is, `pgp_space` does this
        # job with no second credential, no second rate limit and no second
        # copy — and the URL rule covers the pages that are not.
        #
        # Build this only for spaces PGP does not reach. A native integration
        # that duplicates the index is a second system of record.
        space = config.get("space_key", "")
        hits = []
        for kb in platform.index.list_knowledgebases():
            page = platform.index.list_documents(kb.kb_id, page_size=cap)
            for h in page.hits:
                meta = h.metadata or {}
                if (meta.get("space") or meta.get("practiceGroup")) == space:
                    hits.append({"doc_id": h.doc_id, "kb_id": kb.kb_id,
                                 "title": meta.get("title") or h.doc_id,
                                 "url": meta.get("url")})
        return hits[:cap], len(hits) > cap

    if kind == "doc_id_list":
        ids = [i.strip() for i in (config.get("ids") or "").replace(",", "\n").splitlines() if i.strip()]
        return [{"doc_id": i, "kb_id": None, "title": i, "url": None} for i in ids[:cap]], len(ids) > cap

    if kind == "pgp_query":
        from ..ports.content_index import IndexFilters

        hits = []
        for kb in platform.index.list_knowledgebases():
            page = platform.index.search(kb.kb_id, config.get("terms", ""), IndexFilters(), k=min(cap, 50))
            for h in page.hits:
                meta = h.metadata or {}
                hits.append({"doc_id": h.doc_id, "kb_id": kb.kb_id,
                             "title": meta.get("title") or h.doc_id,
                             "url": meta.get("url"), "score": h.score})
        return hits[:cap], len(hits) > cap

    if kind == "upload_batch":
        # Uploads land through the upload endpoint; the rule records the batch.
        return [], False

    # similar_to, link_expansion and crawl are specified and not yet built. An
    # empty result with the rule visible is honest; a fabricated one is not.
    return [], False


@router.get("/api/collections/{slug}/detail")
def collection_detail(slug: str) -> dict:
    """Everything a reviewer needs to judge a knowledgebase.

    Per rule: what was entered, and what it found. Per page: what it is, when it
    was written, when it last changed — and **where each date came from**, since
    many sources supply none and the value is then recovered from the text.
    """
    with _sessions()() as session:
        collection = _collection_row(session, slug)

        rules = session.execute(text(f"""
            SELECT id, ordinal, kind, effect, config, enumerable, last_run_at,
                   last_candidate_count, capped
            FROM {S}.collection_rule WHERE collection_id = :c ORDER BY ordinal
        """), {"c": collection["id"]}).mappings().all()

        members = session.execute(text(f"""
            SELECT doc_id, kb_id, title, description, url, space, path, owner,
                   doc_type, contributed_by, resolved_via, resolve_score, state,
                   pinned, source_created_at, source_updated_at, created_source,
                   updated_source, date_evidence, first_seen_at, last_checked_at,
                   last_changed_at, missing_since
            FROM {S}.collection_member WHERE collection_id = :c
            ORDER BY state, title NULLS LAST, doc_id
        """), {"c": collection["id"]}).mappings().all()

        jobs = session.execute(text(f"""
            SELECT CAST(id AS text) AS id, kind, status, progress, error, attempts, created_at, finished_at
            FROM {S}.job WHERE collection_id = :c
            ORDER BY created_at DESC LIMIT 8
        """), {"c": collection["id"]}).mappings().all()

    by_rule = {str(r["id"]): dict(r) | {"id": str(r["id"]), "found": 0} for r in rules}
    for member in members:
        for rule_id in member["contributed_by"] or []:
            if rule_id in by_rule:
                by_rule[rule_id]["found"] += 1

    return {
        "collection": dict(collection) | {"id": str(collection["id"])},
        "rules": list(by_rule.values()),
        "members": [
            dict(m) | {
                "contributed_by_kinds": [
                    by_rule[x]["kind"] if x in by_rule else "removed rule"
                    for x in (m["contributed_by"] or [])
                ],
            }
            for m in members
        ],
        "jobs": [dict(j) for j in jobs],
        "counts": {
            "members": sum(1 for m in members if m["state"] == "member"),
            "proposed": sum(1 for m in members if m["state"] == "proposed"),
            "removed": sum(1 for m in members if m["state"] == "removed"),
            "dates_from_content": sum(1 for m in members if m["updated_source"] == "content"),
            "dates_missing": sum(1 for m in members if m["updated_source"] == "none"),
            "never_checked": sum(1 for m in members if m["last_checked_at"] is None),
        },
    }


class JobRequest(BaseModel):
    kind: str = "collection.refresh"
    connector: str | None = None
    root: str | None = None
    max_pages: int = 500
    delay_seconds: float = 0.4
    #: Where a crawl publishes what it fetches. Both default from the
    #: collection slug, so the common case needs neither.
    kb_id: str | None = None
    space: str | None = None


@router.post("/api/collections/{slug}/jobs")
def enqueue_job(slug: str, body: JobRequest) -> dict:
    """Ask a worker to do something: materialise, enrich, or check for updates."""
    from ..worker import enqueue

    if body.kind not in ("collection.materialise", "collection.enrich",
                         "collection.refresh", "collection.crawl_plan",
                         "collection.crawl_load"):
        raise HTTPException(400, f"unknown job kind {body.kind}")
    with _sessions()() as session:
        collection = _collection_row(session, slug)
    job = enqueue(
        _sessions(), _org_of(), body.kind, collection_id=collection["id"],
        payload={"collection": slug, "connector": body.connector,
                 "root": body.root, "max_pages": body.max_pages,
                 "delay_seconds": body.delay_seconds,
                 "kb_id": body.kb_id, "space": body.space},
    )
    return {"job_id": job, "kind": body.kind, "status": "queued"}


@router.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str):
    """Watch one job.

    Polls the job row and emits a frame whenever the snapshot changes. Polling
    the database rather than holding the worker's own stream is deliberate: the
    worker is a separate process, may be on another machine, and may be
    restarted mid-crawl — the row is the only thing both sides agree on.
    """
    import time as _time

    from fastapi.responses import StreamingResponse

    def generate():
        last = None
        deadline = _time.monotonic() + 1800
        while _time.monotonic() < deadline:
            with _sessions()() as session:
                row = session.execute(text(f"""
                    SELECT status, progress, error, attempts, finished_at
                    FROM {S}.job WHERE id = :id
                """), {"id": job_id}).mappings().one_or_none()

            if row is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'unknown job'})}\n\n"
                return

            snapshot = {
                "type": "progress", "status": row["status"],
                "attempts": row["attempts"], "error": row["error"],
                **(row["progress"] or {}),
            }
            payload = json.dumps(snapshot, default=str)
            if payload != last:
                last = payload
                yield f"data: {payload}\n\n"

            if row["status"] in ("done", "failed"):
                yield f"data: {json.dumps({'type': 'done', 'status': row['status']})}\n\n"
                return

            _time.sleep(0.5)

        yield f"data: {json.dumps({'type': 'done', 'status': 'timeout'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "cache-control": "no-cache", "x-accel-buffering": "no",
    })


@router.get("/api/jobs")
def list_jobs(limit: int = 20) -> list[dict]:
    with _sessions()() as session:
        rows = session.execute(text(f"""
            SELECT j.id, j.kind, j.status, j.attempts, j.error, j.progress,
                   j.created_at, j.started_at, j.finished_at, j.locked_by,
                   c.slug AS collection
            FROM {S}.job j LEFT JOIN {S}.collection c ON c.id = j.collection_id
            WHERE j.org_id = :o ORDER BY j.created_at DESC LIMIT :n
        """), {"o": _org(session), "n": limit}).mappings().all()
        return [dict(r) | {"id": str(r["id"])} for r in rows]


@router.get("/api/collections/{slug}/members")
def list_members(slug: str, state: str | None = None) -> dict:
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        clause = "AND m.state = :state" if state else ""
        rows = session.execute(text(f"""
            SELECT m.doc_id, m.kb_id, m.title, m.url, m.contributed_by, m.state,
                   m.pinned, m.resolved_via, m.resolve_score, m.first_seen_at, m.missing_since
            FROM {S}.collection_member m
            WHERE m.collection_id = :c {clause}
            ORDER BY m.state, m.title NULLS LAST, m.doc_id
        """), {"c": collection["id"], "state": state}).mappings().all()
        rules = {
            str(r["id"]): f'{r["kind"]}'
            for r in session.execute(text(
                f"SELECT id, kind FROM {S}.collection_rule WHERE collection_id = :c"
            ), {"c": collection["id"]}).mappings().all()
        }
        return {
            "members": [
                dict(r) | {"contributed_by_kinds": [rules.get(x, "removed rule") for x in (r["contributed_by"] or [])]}
                for r in rows
            ],
            "counts": {
                s: sum(1 for r in rows if r["state"] == s)
                for s in {row["state"] for row in rows}
            },
        }


class Pin(BaseModel):
    doc_id: str
    pinned: str | None  # "in" | "out" | None
    actor: str = "admin"


@router.post("/api/collections/{slug}/members/pin")
def pin_member(slug: str, body: Pin) -> dict:
    """A human decision that survives every future re-materialisation.

    This is the requirement that makes a disorganised corpus tractable: the
    rules will never be exactly right, and a correction that gets overwritten is
    one nobody makes twice.
    """
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        session.execute(text(f"""
            INSERT INTO {S}.collection_member
                (org_id, collection_id, doc_id, state, pinned, pinned_by)
            VALUES (:org, :c, :d, CASE WHEN :p = 'in' THEN 'member' ELSE 'removed' END, :p, :who)
            ON CONFLICT (collection_id, doc_id) DO UPDATE
              SET pinned = :p, pinned_by = :who,
                  state = CASE WHEN :p = 'in' THEN 'member'
                               WHEN :p = 'out' THEN 'removed'
                               ELSE {S}.collection_member.state END
        """), {"org": _org(session), "c": collection["id"], "d": body.doc_id,
               "p": body.pinned, "who": body.actor})
        session.commit()
    return {"doc_id": body.doc_id, "pinned": body.pinned}


# --------------------------------------------------------------------- roles


class RoleCreate(BaseModel):
    name: str
    description: str = ""
    principals: list[str] = []


def _connector_id(session, slug: str) -> uuid.UUID:
    row = session.execute(text(
        f"SELECT id FROM {S}.connector WHERE org_id = :o AND slug = :s"
    ), {"o": _org(session), "s": slug}).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"unknown connector {slug}")
    return row


@router.get("/api/connectors/{slug}/roles")
def list_roles(slug: str) -> list[dict]:
    """Roles are the unit of access, and the unit Diagnose runs as.

    A role is not a person. Asking "what would Finance see" is a question an
    administrator can answer without impersonating anybody's session, and it is
    the question they actually have.
    """
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        roles = session.execute(text(f"""
            SELECT id, name, description FROM {S}.rbac_role
            WHERE connector_id = :c ORDER BY name
        """), {"c": cid}).mappings().all()
        out = []
        for role in roles:
            members = session.execute(text(
                f"SELECT principal FROM {S}.rbac_role_member WHERE role_id = :r ORDER BY principal"
            ), {"r": role["id"]}).scalars().all()
            rules = session.execute(text(f"""
                SELECT space, label, effect FROM {S}.rbac_label_rule WHERE role_id = :r
            """), {"r": role["id"]}).mappings().all()
            out.append(dict(role) | {
                "id": str(role["id"]),
                "principals": list(members),
                "rules": [dict(x) for x in rules],
            })
        return out


@router.post("/api/connectors/{slug}/roles")
def create_role(slug: str, body: RoleCreate) -> dict:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rid = session.execute(text(f"""
            INSERT INTO {S}.rbac_role (org_id, connector_id, name, description)
            VALUES (:o, :c, :n, :d)
            ON CONFLICT (connector_id, name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """), {"o": _org(session), "c": cid, "n": body.name, "d": body.description}).scalar_one()
        session.execute(text(f"DELETE FROM {S}.rbac_role_member WHERE role_id = :r"), {"r": rid})
        for principal in body.principals:
            session.execute(text(f"""
                INSERT INTO {S}.rbac_role_member (org_id, role_id, principal)
                VALUES (:o, :r, :p) ON CONFLICT DO NOTHING
            """), {"o": _org(session), "r": rid, "p": principal})
        # Any change bumps the policy version, so a cached projection is
        # invalidated without an explicit purge.
        session.execute(text(
            f"UPDATE {S}.connector SET policy_version = policy_version + 1 WHERE id = :c"
        ), {"c": cid})
        session.commit()
        return {"id": str(rid), "name": body.name}


@router.delete("/api/connectors/{slug}/roles/{role_id}")
def delete_role(slug: str, role_id: str) -> dict:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        session.execute(text(
            f"DELETE FROM {S}.rbac_role WHERE id = :r AND connector_id = :c"
        ), {"r": role_id, "c": cid})
        session.execute(text(
            f"UPDATE {S}.connector SET policy_version = policy_version + 1 WHERE id = :c"
        ), {"c": cid})
        session.commit()
    return {"deleted": role_id}


class LabelRule(BaseModel):
    #: One of the two is set. A rule on a space covers everything in it; a rule
    #: on a label covers whatever carries it, wherever it lives.
    space: str | None = None
    label: str | None = None
    effect: str = "deny"


class LabelRules(BaseModel):
    rules: list[LabelRule] = []


@router.put("/api/connectors/{slug}/roles/{role_id}/rules")
def set_label_rules(slug: str, role_id: str, body: LabelRules) -> dict:
    """Narrow a role below the connector's own scope.

    The connector scope says what the *corpus* is. These say what a particular
    role may see of it — the content analogue of askdb's column rules, and the
    reason a single connector can serve two audiences without duplicating the
    knowledgebase.

    Replaced wholesale rather than patched. A partial update of an access rule
    set is how a deny survives the removal of the thing it was denying.
    """
    for rule in body.rules:
        if not rule.space and not rule.label:
            raise HTTPException(400, "a rule must name a space or a label")
        if rule.effect not in ("allow", "deny"):
            raise HTTPException(400, f"unknown effect {rule.effect}")

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        owned = session.execute(text(
            f"SELECT 1 FROM {S}.rbac_role WHERE id = :r AND connector_id = :c"
        ), {"r": role_id, "c": cid}).scalar_one_or_none()
        if not owned:
            raise HTTPException(404, "no such role on this connector")

        session.execute(text(
            f"DELETE FROM {S}.rbac_label_rule WHERE role_id = :r"
        ), {"r": role_id})
        for rule in body.rules:
            session.execute(text(f"""
                INSERT INTO {S}.rbac_label_rule (org_id, role_id, space, label, effect)
                VALUES (:o, :r, :s, :l, :e)
            """), {
                "o": _org(session), "r": role_id, "s": rule.space,
                "l": rule.label, "e": rule.effect,
            })
        # Any access change bumps the policy version, so a cached projection is
        # invalidated without an explicit purge.
        session.execute(text(
            f"UPDATE {S}.connector SET policy_version = policy_version + 1 WHERE id = :c"
        ), {"c": cid})
        session.commit()
    return {"role_id": role_id, "rules": len(body.rules)}


@router.get("/api/connectors/{slug}/facets")
def scope_facets(slug: str) -> dict:
    """The spaces and labels actually present in this connector's corpus.

    Offered so that an access rule is chosen from what exists rather than typed
    from memory. A deny on a label nobody uses is a rule that looks like
    protection and is not.
    """
    from ..services.retrieval import scope_population

    platform = _platform()
    connector = platform.registry.get(slug)
    population = scope_population(platform.index, connector)

    spaces: dict[str, int] = {}
    labels: dict[str, int] = {}
    for meta in population:
        if meta.space:
            spaces[meta.space] = spaces.get(meta.space, 0) + 1
        for label in meta.labels or ():
            labels[label] = labels.get(label, 0) + 1

    return {
        "documents": len(population),
        "spaces": [{"value": k, "documents": v} for k, v in sorted(spaces.items())],
        "labels": [{"value": k, "documents": v} for k, v in sorted(labels.items())],
    }


@router.get("/api/connectors/{slug}/roles/{role_id}/effective")
def effective_access(slug: str, role_id: str) -> dict:
    """What this role can actually reach, computed rather than asserted.

    An access screen that lists grants tells you what was configured. This tells
    you what it *means*, which is the only version anybody can act on.
    """
    from ..domain.role_rules import RoleRule
    from ..domain.role_rules import decide as role_decide
    from ..domain.scope import evaluate
    from ..services.retrieval import scope_population

    platform = _platform()
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        principals = session.execute(text(
            f"SELECT principal FROM {S}.rbac_role_member WHERE role_id = :r"
        ), {"r": role_id}).scalars().all()
        name = session.execute(text(
            f"SELECT name FROM {S}.rbac_role WHERE id = :r"
        ), {"r": role_id}).scalar_one_or_none()
        rules = tuple(
            RoleRule(effect=r["effect"], space=r["space"], label=r["label"])
            for r in session.execute(text(
                f"SELECT effect, space, label FROM {S}.rbac_label_rule WHERE role_id = :r"
            ), {"r": role_id}).mappings().all()
        )

    connector = platform.registry.get(slug)
    population = scope_population(platform.index, connector)
    principal = principals[0] if principals else "group:all-staff"

    from ..domain.documents import DocRef
    from ..ports.content_repository import ResolutionOutcome

    in_scope = [m for m in population if evaluate(connector.scope, m).in_scope]

    # The role's own rules first: they are decided locally and cost nothing,
    # and they are the same predicate the retrieval gate applies. A screen that
    # reimplemented this would eventually disagree with the gate, and the
    # disagreement would surface as an answer citing a document the screen
    # swore was hidden.
    narrowed, by_rule = [], []
    for meta in in_scope:
        verdict = role_decide(
            rules, space=meta.space, labels=tuple(meta.labels or ())
        )
        (narrowed if verdict.allowed else by_rule).append((meta, verdict.reason))

    # The store is asked only about what survived, and only up to a bound:
    # each authorize is a round trip, and 200 of them made this screen take
    # eleven seconds to answer a question about configuration.
    SAMPLE = 60
    readable, forbidden = [], []
    for meta, _why in narrowed[:SAMPLE]:
        outcome = platform.repository.authorize(
            principal, DocRef(doc_id=meta.doc_id, kb_id=meta.kb_id)
        )
        (readable if outcome is ResolutionOutcome.RESOLVED else forbidden).append(meta.title)

    checked = min(len(narrowed), SAMPLE)
    return {
        "role": name, "principals": principals, "principal_used": principal,
        "in_scope": len(in_scope),
        "allowed_by_rules": len(narrowed),
        "blocked_by_rules": len(by_rule),
        "blocked_sample": [
            {"title": m.title, "reason": why} for m, why in by_rule[:8]
        ],
        "readable": len(readable),
        "forbidden": len(forbidden),
        "forbidden_sample": forbidden[:8],
        "checked": checked,
        "note": (
            f"{len(by_rule)} of {len(in_scope)} in-scope documents are excluded by this "
            f"role's rules. Store permissions were checked on {checked} of the "
            f"{len(narrowed)} that remain."
        ),
    }


# ------------------------------------------------------------------ glossary


class TermCreate(BaseModel):
    term: str
    definition: str
    aliases: list[str] = []


@router.get("/api/connectors/{slug}/glossary")
def list_terms(slug: str) -> dict:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rows = session.execute(text(f"""
            SELECT id, term, definition, aliases, source, status, method,
                   confidence, occurrences, documents, evidence, updated_at
            FROM {S}.glossary_term WHERE connector_id = :c
            ORDER BY status, confidence DESC NULLS LAST, term
        """), {"c": cid}).mappings().all()
    terms = [dict(r) | {"id": str(r["id"])} for r in rows]
    return {
        "terms": terms,
        "counts": {
            state: sum(1 for t in terms if t["status"] == state)
            for state in ("proposed", "confirmed", "rejected")
        },
    }


@router.post("/api/connectors/{slug}/glossary/discover")
def discover_terms(slug: str) -> dict:
    """Propose terms from the indexed corpus.

    Runs inline rather than through the worker: it reads chunks we already hold
    and takes a second, and a reviewer who pressed the button wants the list,
    not a job id.
    """
    from ..services.glossary_service import GlossaryService

    return GlossaryService(_sessions(), _org_of()).discover_for(slug)


class TermDecision(BaseModel):
    status: str
    definition: str | None = None
    actor: str = "admin"


@router.post("/api/connectors/{slug}/glossary/{term_id}/review")
def review_term(slug: str, term_id: str, body: TermDecision) -> dict:
    """Confirm or reject a proposal.

    A rejection is stored, not deleted: the next discovery run would otherwise
    propose the same term again, and a reviewer who has already said no should
    not have to keep saying it.
    """
    if body.status not in ("confirmed", "rejected"):
        raise HTTPException(400, "status must be confirmed or rejected")
    with _sessions()() as session:
        _connector_id(session, slug)
        session.execute(text(f"""
            UPDATE {S}.glossary_term
            -- `:s` is read as a value and as a comparand, and Postgres
            -- cannot infer one type from that. Cast it once, explicitly.
            SET status = CAST(:s AS varchar), reviewed_by = :who,
                reviewed_at = now(),
                definition = coalesce(:definition, definition),
                source = CASE WHEN CAST(:s AS varchar) = 'confirmed'
                              THEN 'human' ELSE source END
            WHERE id = CAST(:t AS uuid)
        """), {"s": body.status, "who": body.actor, "t": term_id,
               "definition": body.definition})
        session.commit()
    return {"id": term_id, "status": body.status}


@router.post("/api/connectors/{slug}/glossary")
def create_term(slug: str, body: TermCreate) -> dict:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rid = session.execute(text(f"""
            INSERT INTO {S}.glossary_term
                (org_id, connector_id, term, definition, aliases, source, status)
            -- Typed by a person, so already reviewed. Leaving it 'proposed'
            -- would mean a curated glossary does nothing until somebody also
            -- approves their own entry.
            VALUES (:o, :c, :t, :d, :a, 'human', 'confirmed')
            ON CONFLICT (connector_id, term) DO UPDATE
              SET definition = EXCLUDED.definition, aliases = EXCLUDED.aliases,
                  status = 'confirmed' 
            RETURNING id
        """), {"o": _org(session), "c": cid, "t": body.term,
               "d": body.definition, "a": body.aliases}).scalar_one()
        session.commit()
        return {"id": str(rid), "term": body.term}


@router.delete("/api/connectors/{slug}/glossary/{term_id}")
def delete_term(slug: str, term_id: str) -> dict:
    with _sessions()() as session:
        session.execute(text(f"DELETE FROM {S}.glossary_term WHERE id = :t"), {"t": term_id})
        session.commit()
    return {"deleted": term_id}


# ------------------------------------------------------------------ retrieval


@router.get("/api/connectors/{slug}/retrieval")
def get_retrieval(slug: str) -> dict:
    """Every parameter with its default, its effective value, and **where the
    effective value came from**.

    Inherited configuration whose provenance is invisible is configuration
    nobody dares change.
    """
    from ..services.registry import PLATFORM_DEFAULTS

    connector = _platform().registry.get(slug)
    effective = connector.retrieval.model_dump(mode="json")
    defaults = PLATFORM_DEFAULTS.model_dump(mode="json")

    fields = []
    for key, value in effective.items():
        default = defaults.get(key)
        fields.append({
            "key": key,
            "value": value,
            "default": default,
            "source": "connector override" if value != default else "platform default",
        })
    return {"connector": slug, "fields": fields}


class RetrievalUpdate(BaseModel):
    values: dict


@router.put("/api/connectors/{slug}/retrieval")
def put_retrieval(slug: str, body: RetrievalUpdate) -> dict:
    from ..services.registry import RetrievalConfig

    platform = _platform()
    connector = platform.registry.get(slug)
    merged = connector.retrieval.model_dump(mode="json") | body.values
    updated = connector.model_copy(update={"retrieval": RetrievalConfig.model_validate(merged)})
    platform.registry.put(updated, "admin", note="retrieval parameters")
    return get_retrieval(slug)


# --------------------------------------------------------------------- embeds


class EmbedCreate(BaseModel):
    name: str
    allowed_origins: list[str] = []


@router.get("/api/connectors/{slug}/embeds")
def list_embeds(slug: str) -> list[dict]:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rows = session.execute(text(f"""
            SELECT id, name, publishable_key, allowed_origins, is_active,
                   appearance, session_count, last_used_at, created_at
            FROM {S}.embed WHERE connector_id = :c ORDER BY created_at DESC
        """), {"c": cid}).mappings().all()
        return [dict(r) | {"id": str(r["id"])} for r in rows]


@router.post("/api/connectors/{slug}/embeds")
def create_embed(slug: str, body: EmbedCreate) -> dict:
    # Public by construction: the key identifies the embed, it authorises
    # nothing. Every access decision is made server-side against the visitor's
    # own token.
    key = "pk_" + secrets.token_urlsafe(24)
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rid = session.execute(text(f"""
            INSERT INTO {S}.embed (org_id, connector_id, name, publishable_key, allowed_origins)
            VALUES (:o, :c, :n, :k, :orig) RETURNING id
        """), {"o": _org(session), "c": cid, "n": body.name, "k": key,
               "orig": body.allowed_origins}).scalar_one()
        session.commit()
    return {"id": str(rid), "name": body.name, "publishable_key": key}


class EmbedPatch(BaseModel):
    name: str | None = None
    allowed_origins: list[str] | None = None
    is_active: bool | None = None
    appearance: dict | None = None


@router.patch("/api/connectors/{slug}/embeds/{embed_id}")
def update_embed(slug: str, embed_id: str, body: EmbedPatch) -> dict:
    """Everything about an embed except its key.

    The key is never rotated in place. A rotation that keeps the same row is
    indistinguishable, from the pages that carry it, from an outage — so
    replacing a key means creating a second embed, moving the pages, and then
    deleting the first, which is a sequence somebody can carry out safely.
    """
    if body.allowed_origins is not None:
        for origin in body.allowed_origins:
            if not origin.startswith(("http://", "https://")):
                raise HTTPException(
                    400,
                    f"'{origin}' is not an origin. An origin is scheme and host "
                    f"with no path, for example https://intranet.example.com",
                )
            if origin.rstrip("/").count("/") > 2:
                raise HTTPException(
                    400, f"'{origin}' has a path; an origin has none",
                )

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        row = session.execute(text(f"""
            UPDATE {S}.embed SET
                name = coalesce(:n, name),
                allowed_origins = coalesce(:orig, allowed_origins),
                is_active = coalesce(:act, is_active),
                appearance = coalesce(CAST(:app AS jsonb), appearance),
                updated_at = now()
            WHERE id = CAST(:e AS uuid) AND connector_id = :c
            RETURNING id, name, publishable_key, allowed_origins, is_active,
                      appearance, session_count, last_used_at, created_at
        """), {
            "e": embed_id, "c": cid, "n": body.name,
            "orig": body.allowed_origins, "act": body.is_active,
            "app": json.dumps(body.appearance) if body.appearance is not None else None,
        }).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "no such embed on this connector")
        session.commit()
        return dict(row) | {"id": str(row["id"])}


@router.get("/api/connectors/{slug}/embeds/{embed_id}/snippet")
def embed_snippet(slug: str, embed_id: str, base: str | None = None) -> dict:
    """The code to paste, generated rather than documented.

    A snippet in a README drifts from the product the first time an option is
    renamed. Generating it from the row means the thing on screen is the thing
    that works, including this embed's own key and appearance.
    """
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        row = session.execute(text(f"""
            SELECT publishable_key, appearance FROM {S}.embed
            WHERE id = CAST(:e AS uuid) AND connector_id = :c
        """), {"e": embed_id, "c": cid}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "no such embed on this connector")

    key = row["publishable_key"]
    look = row["appearance"] or {}
    origin = (base or "https://api.example.com").rstrip("/")

    chosen = [
        (name, value)
        for name, value in (
            ("title", look.get("title")),
            ("placeholder", look.get("placeholder")),
            ("position", look.get("position")),
            ("size", look.get("size")),
            ("theme", look.get("theme")),
        )
        if value
    ]
    # `ensure_ascii=False` because these strings are shown to a person and
    # pasted into a page. An ellipsis rendered as \u2026 in a snippet is a
    # snippet that looks broken before it has run.
    options = "".join(
        f"\n    {name}: {json.dumps(value, ensure_ascii=False)},"
        for name, value in chosen
    )
    # JSX takes attributes, not object entries. Generating one string for both
    # produced a React example that could not compile.
    jsx_props = "".join(
        f"\n      {name}={json.dumps(value, ensure_ascii=False)}"
        for name, value in chosen
    )

    script = f"""<script>
  (function (w, d) {{
    w.askcontent = w.askcontent || function () {{ (w.askcontent.q = w.askcontent.q || []).push(arguments) }};
    var s = d.createElement('script');
    s.src = '{origin}/widget/embed.js'; s.async = true;
    d.head.appendChild(s);
  }})(window, document);

  askcontent('init', {{
    key: '{key}',
    baseUrl: '{origin}',{options}
    // Identity is required and there is no anonymous mode: an assistant that
    // does not know who is asking cannot honour "no answer cites a document
    // the asker cannot open". Mint this token on your server.
    user: {{ id: CURRENT_USER_ID, token: CURRENT_USER_TOKEN }},
  }});
</script>"""

    react = f"""import {{ AskContent }} from '@askcontent/widget/react'

export function Assistant({{ user }}) {{
  return (
    <AskContent
      publicKey="{key}"
      baseUrl="{origin}"{jsx_props}
      user={{{{ id: user.id, token: user.assistantToken }}}}
    />
  )
}}"""

    return {
        "snippet": script,
        "react_package": "@askcontent/widget",
        "react_snippet": react,
        "publishable_key": key,
    }


@router.delete("/api/connectors/{slug}/embeds/{embed_id}")
def delete_embed(slug: str, embed_id: str) -> dict:
    with _sessions()() as session:
        session.execute(text(f"DELETE FROM {S}.embed WHERE id = :e"), {"e": embed_id})
        session.commit()
    return {"deleted": embed_id}


# ------------------------------------------------------------------- settings


class SettingsPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    system_instructions: str | None = None


#: A prompt long enough to bury the grounding rules is a prompt that weakens
#: them by dilution rather than by contradiction. The cap is generous for
#: genuine guidance and short of an essay.
MAX_INSTRUCTIONS = 4000


@router.patch("/api/connectors/{slug}/settings")
def update_settings(slug: str, body: SettingsPatch) -> dict:
    if body.system_instructions and len(body.system_instructions) > MAX_INSTRUCTIONS:
        raise HTTPException(
            400,
            f"instructions are {len(body.system_instructions)} characters; the "
            f"limit is {MAX_INSTRUCTIONS}. Guidance this long buries the rules "
            f"it sits above rather than adding to them.",
        )

    with _sessions()() as session:
        session.execute(text(f"""
            UPDATE {S}.connector SET
                name = coalesce(:n, name),
                description = coalesce(:d, description),
                system_instructions = coalesce(:si, system_instructions),
                updated_at = now()
            WHERE org_id = :o AND slug = :s
        """), {
            "o": _org(session), "s": slug, "n": body.name,
            "d": body.description, "si": body.system_instructions,
        })
        session.commit()
    return get_settings(slug)


def _instructions_for(slug: str) -> str:
    with _sessions()() as session:
        return session.execute(text(
            f"SELECT system_instructions FROM {S}.connector "
            f"WHERE org_id = :o AND slug = :s"
        ), {"o": _org(session), "s": slug}).scalar_one_or_none() or ""


from ..domain.followups import suggest as suggest_followups


def _config_settings():
    from ..config import settings as _s

    return _s


@router.get("/api/connectors/{slug}/settings")
def get_settings(slug: str) -> dict:
    connector = _platform().registry.get(slug)
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        quarantine = session.execute(text(f"""
            SELECT doc_id, matched_class, redacted_span, status, created_at
            FROM {S}.quarantine_item WHERE connector_id = :c ORDER BY created_at DESC LIMIT 50
        """), {"c": cid}).mappings().all()
        jobs = session.execute(text(f"""
            SELECT kind, status, progress, error, created_at FROM {S}.job
            WHERE connector_id = :c ORDER BY created_at DESC LIMIT 10
        """), {"c": cid}).mappings().all()
        row_extra = session.execute(text(f"""
            SELECT name, description, system_instructions
            FROM {S}.connector WHERE id = :c
        """), {"c": cid}).mappings().one()

    # Which answerer is actually in use. Reported here because the fallback is
    # silent otherwise, and an unannounced downgrade to extractive answers is
    # how a demo becomes a misunderstanding — somebody reads the answers, finds
    # them poor, and concludes the product is poor.
    answerer = _platform().answering.answerer
    configured_key = bool(_config_settings().llm_api_key)

    return {
        "connector": slug,
        "name": row_extra["name"],
        "description": row_extra["description"],
        "system_instructions": row_extra["system_instructions"],
        "business_group": connector.business_group,
        "kb_id": connector.kb_id,
        "state": str(connector.state),
        "limits": {
            "max_documents": connector.scope.max_documents,
            "max_bytes": connector.scope.max_bytes,
            "sensitivity_ceiling": str(connector.scope.sensitivity_ceiling),
        },
        "answering": {
            "provider": answerer.name,
            "model": answerer.model_id,
            "grounded_model": answerer.name != "extractive-offline",
            "key_configured": configured_key,
            "note": (
                "Answers are composed by a model, grounded in the retrieved "
                "passages and required to cite them."
                if answerer.name != "extractive-offline"
                else "No model is configured, so answers are extracted verbatim "
                     "from the retrieved passages rather than composed. Set "
                     "ASKCONTENT_LLM_API_KEY to enable a grounded model."
            ),
        },
        "retrieval": {
            "reranker": connector.retrieval.reranker_id,
            "rerank_floor": connector.retrieval.rerank_floor,
            "k_per_channel": connector.retrieval.k_per_channel,
            "passages_per_document": connector.retrieval.passages_per_document,
            "expired_days": connector.retrieval.freshness.expired_days,
            "stale_days": connector.retrieval.freshness.stale_days,
        },
        "quarantine": [dict(q) for q in quarantine],
        "jobs": [dict(j) for j in jobs],
        "danger_zone": {
            "suspend": "Takes effect on the next query. Nothing is deleted.",
            "delete": "Removes the connector, its catalog and its audit rows. "
                      "Audit rows are what answer 'what could this account have "
                      "seen', so deletion is refused while a retention hold is "
                      "in force.",
        },
    }


# ----------------------------------------------------------------- chat (SSE)


class AskStream(BaseModel):
    connector_id: str
    question: str
    #: Prior turns in this thread, oldest first. Used to resolve what a
    #: follow-up refers to — never as a source of facts.
    history: list[dict] = []
    #: A **role**, not a person. Access is defined by roles on the Access
    #: screen; asking as an ad-hoc principal would let the console invent an
    #: identity the platform never granted.
    role: str | None = None


def _age_notices(citations, cited: tuple[int, ...]) -> list[str]:
    """Warn about the age of what the answer actually leant on.

    Raised after answering rather than after retrieval, because before the
    answer exists there is no way to know which of a dozen candidates matter.
    A warning about a document nobody cited is the kind of noise that teaches
    a reader to skip warnings.
    """
    used = [citations[n - 1] for n in cited if 1 <= n <= len(citations)]
    stale = [c for c in used if str(c.staleness) in ("stale", "expired")]
    if not stale:
        return []

    oldest = min(
        stale,
        key=lambda c: c.updated_at or dt.datetime.max.replace(tzinfo=dt.UTC),
    )
    if oldest.updated_at:
        return [f"This answer cites '{oldest.title}', last updated "
                f"{oldest.updated_at:%d %b %Y}."]
    return [f"This answer cites '{oldest.title}', which has no recorded date."]


def _run_answer(platform, question, citations, history, instructions="", synonyms=None):
    """Drive the async answerer from this synchronous stream.

    The endpoint is a sync generator because the retrieval pipeline is
    synchronous and blocking; the answerer is async because streaming an HTTP
    response body is. Rather than convert one to the other, the async
    generator is pumped on a private loop in a worker thread and its chunks
    handed back through a queue — so tokens still reach the client as they
    are produced, not in one batch at the end.
    """
    import asyncio
    import queue
    import threading

    outbox: queue.Queue = queue.Queue()
    _END = object()

    async def pump():
        try:
            async for item in platform.answering.stream(
                question, citations, history, instructions, synonyms
            ):
                outbox.put(item)
        except Exception as exc:  # noqa: BLE001
            outbox.put(("", _AnswerFailure(str(exc))))
        finally:
            outbox.put(_END)

    def drive() -> None:
        """Own the loop explicitly rather than using `asyncio.run`.

        `asyncio.run` finalises async generators as it closes, and the
        provider's streaming generator objects to being thrown into after the
        iteration has already finished — which surfaced as
        "generator didn't stop after athrow()" printed after every otherwise
        successful run. Shutting the generators down first, then closing, is
        the ordering it expects. An error printed on every success is how
        people learn to ignore errors.
        """
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(pump())
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()

    thread = threading.Thread(target=drive, daemon=True)
    thread.start()

    while True:
        item = outbox.get()
        if item is _END:
            return
        yield item


class _AnswerFailure:
    """An answerer that raised.

    Reported as an unsupported answer rather than a 500, because the retrieval
    work is still valid and the evidence is still worth showing — but it
    carries `error`, so a caller that needs to tell "we could not ask" from
    "the corpus does not cover this" can. An eval run that conflates them
    reports an outage as a content gap.
    """

    supported = False
    cited: tuple[int, ...] = ()
    invented: tuple[int, ...] = ()

    def __init__(self, message: str) -> None:
        self.error = message
        self.reason = f"the answerer failed: {message}"


def _sse(payload: dict) -> str:
    """One frame. Same envelope as askdb: `data:` plus a JSON object with a
    `type` discriminator, terminated by a blank line."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _principal_for_role(slug: str, role: str | None) -> str:
    if not role:
        return "group:all-staff"
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        row = session.execute(text(f"""
            SELECT m.principal FROM {S}.rbac_role r
            JOIN {S}.rbac_role_member m ON m.role_id = r.id
            WHERE r.connector_id = :c AND r.name = :n ORDER BY m.principal LIMIT 1
        """), {"c": cid, "n": role}).scalar_one_or_none()
    # A role with no principals grants nothing. Falling back to a broad
    # principal here would silently widen access, which is the one thing this
    # layer exists to prevent.
    return row or f"role:{role}"


def _answer_about_the_corpus(slug: str, question: str) -> str | None:
    """The answer to "what can you tell me", if that is what was asked.

    Returned before retrieval runs, because there is nothing to retrieve: the
    answer is the shape of the collection, not a passage in it. Running it
    through retrieval produces a refusal on a question the system can answer
    perfectly well — and it is the first thing many people type.
    """
    from ..domain.overview import describe
    from ..domain.question_kind import QuestionKind, classify

    kind = classify(question)
    if kind is QuestionKind.CONTENT:
        return None

    platform = _platform()
    connector = platform.registry.get(slug)

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        row = session.execute(text(f"""
            SELECT name, description FROM {S}.connector WHERE id = :c
        """), {"c": cid}).mappings().one()
        terms = session.execute(text(f"""
            SELECT term FROM {S}.glossary_term
            WHERE connector_id = :c AND (status = 'confirmed' OR source = 'human')
            ORDER BY coalesce(documents, 0) DESC LIMIT 6
        """), {"c": cid}).scalars().all()

    from ..services.retrieval import scope_population

    overview = describe(
        row["name"], row["description"] or "",
        list(scope_population(platform.index, connector)),
        terms=list(terms),
    )

    if kind is QuestionKind.SOCIAL:
        # A greeting answered with a wall of statistics is its own kind of
        # rude. One sentence of orientation, then get out of the way.
        first = overview.text.split(". ")[0]
        return f"Hello. {first}. What would you like to know?"

    return overview.text


def _glossary_for(slug: str) -> tuple:
    """The connector's approved terms, as the expander wants them.

    Confirmed ones, and ones a person typed.

    A *discovered* term is a proposal: the pass guesses from frequency and
    phrasing, and is wrong often enough that expanding on its guesses would put
    words into questions nobody agreed to. Review is what makes the glossary
    safe to use here, and it is why the review queue exists.

    A term somebody typed by hand needs no separate approval — typing it was
    the approval. Treating it as a proposal means the glossary somebody
    deliberately curated does nothing until they also click a button they have
    no reason to expect.
    """
    from ..domain.expansion import Term

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rows = session.execute(text(f"""
            SELECT term, aliases FROM {S}.glossary_term
            WHERE connector_id = :c
              AND (status = 'confirmed' OR source = 'human')
            ORDER BY length(term) DESC
        """), {"c": cid}).mappings().all()
    return tuple(
        Term(term=r["term"], aliases=tuple(r["aliases"] or ())) for r in rows
    )


def _rules_for_role(slug: str, role: str | None) -> tuple:
    """The narrowing a role carries, as the retrieval gate wants it.

    Loaded per question rather than cached on the connector, so that removing a
    role's access takes effect on the next query rather than whenever something
    happens to invalidate a cache. Narrowing that waits is narrowing that
    leaked.
    """
    from ..domain.role_rules import RoleRule

    if not role:
        return ()
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rows = session.execute(text(f"""
            SELECT lr.effect, lr.space, lr.label
            FROM {S}.rbac_label_rule lr
            JOIN {S}.rbac_role r ON r.id = lr.role_id
            WHERE r.connector_id = :c AND r.name = :n
        """), {"c": cid, "n": role}).mappings().all()
    return tuple(
        RoleRule(effect=r["effect"], space=r["space"], label=r["label"]) for r in rows
    )


@router.post("/api/chat/stream")
def chat_stream(body: AskStream):
    """The answer stream.

    Events, in the order they occur:

      tool_start / tool_end   one per pipeline stage, so the reader sees work
                              happening rather than a spinner
      token                   answer prose, as it is composed
      complete                the structured payload — citations, conflicts,
                              notices, refusal, trace
      timing / done | error

    Prose and evidence are separate frames on purpose: the prose may vary
    between runs, the citations must not, and merging them makes the varying
    half look as authoritative as the stable one.
    """
    from fastapi.responses import StreamingResponse

    from ..domain.retrieval_spec import Intent, RetrievalSpec

    platform = _platform()
    connector = platform.registry.get(body.connector_id)
    principal = _principal_for_role(body.connector_id, body.role)

    def generate():
        started = dt.datetime.now()
        try:
            # Questions about the corpus are answered from the corpus's shape,
            # not from a passage in it. Checked first because there is nothing
            # for retrieval to do.
            about = _answer_about_the_corpus(body.connector_id, body.question)
            if about is not None:
                for word in about.split(" "):
                    yield _sse({"type": "token", "text": word + " "})
                yield _sse({
                    "type": "complete", "citations": [], "conflicts": [],
                    "notices": [], "refused": False, "refusal_reason": None,
                    "followups": [],
                    "answered_by": {
                        "provider": "corpus-overview", "model": "constructed",
                        "grounded": True, "cited": [],
                    },
                    "trace": {"kind": "scope"},
                })
                elapsed = (dt.datetime.now() - started).total_seconds() * 1000
                yield _sse({"type": "timing", "elapsed_ms": round(elapsed)})
                yield _sse({"type": "done"})
                return

            spec = RetrievalSpec(
                intent=Intent.LOOKUP,
                scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
                question=body.question,
                channels=connector.retrieval.channels,
                k_per_channel=connector.retrieval.k_per_channel,
            )

            stages = [
                ("compile", "Compiling scope and permissions"),
                ("candidates", "Searching the index and the store"),
                ("resolve", "Resolving candidates against the store"),
                ("passages", "Recovering passages"),
                ("rerank", "Reranking"),
            ]
            for name, label in stages[:2]:
                yield _sse({"type": "tool_start", "name": name, "label": label})

            evidence = platform.retrieval.retrieve(
                connector, spec, principal,
                role_rules=_rules_for_role(body.connector_id, body.role),
                glossary=_glossary_for(body.connector_id),
            )
            trace = evidence.trace

            yield _sse({"type": "tool_end", "name": "compile", "label": stages[0][1],
                        "status": "ok", "detail": f"plan {trace.plan_hash}"})
            yield _sse({"type": "tool_end", "name": "candidates", "label": stages[1][1],
                        "status": "ok",
                        "detail": ", ".join(f"{c.channel} {c.hits}" for c in trace.channels)})
            for name, label, detail in [
                ("resolve", stages[2][1],
                 f"{len(trace.candidates)} candidates, {trace.stale_index_count} stale, "
                 f"{trace.forbidden_count} forbidden"),
                ("passages", stages[3][1],
                 f"cache {trace.cache_hit_rate:.0%}"),
                ("rerank", stages[4][1],
                 f"{len(evidence.citations)} passages above the floor"),
            ]:
                yield _sse({"type": "tool_start", "name": name, "label": label})
                yield _sse({"type": "tool_end", "name": name, "label": label,
                            "status": "ok", "detail": detail})

            # ⑦ answer ------------------------------------------------------
            # The relevance gate runs first and can refuse without calling the
            # answerer at all; see domain/groundedness.py for why that decision
            # is made here rather than left to the model.
            yield _sse({"type": "tool_start", "name": "answer",
                        "label": "Composing a grounded answer"})

            history = [
                (turn.get("question", ""), turn.get("answer", ""))
                for turn in body.history
                if turn.get("question")
            ]
            outcome = None
            for text, result in _run_answer(
                platform, body.question, evidence.citations, history,
                _instructions_for(body.connector_id),
                evidence.trace.synonyms,
            ):
                if result is not None:
                    outcome = result
                elif text:
                    yield _sse({"type": "token", "text": text})

            answerer = platform.answering.answerer
            yield _sse({
                "type": "tool_end", "name": "answer",
                "label": "Composing a grounded answer",
                "status": "ok" if (outcome and outcome.supported) else "warn",
                # No provider or model name: the trace is read by whoever is
                # asking the question, and which vendor answered is a
                # deployment detail they cannot act on.
                "detail": (
                    f"cited {len(outcome.cited)} of {len(evidence.citations)} passages"
                    if outcome and outcome.supported
                    else (outcome.reason if outcome and outcome.reason
                          else "not answerable from this corpus")
                ),
            })

            payload = evidence.model_dump(mode="json")
            payload["notices"] = list(payload.get("notices") or []) + _age_notices(
                evidence.citations, outcome.cited if outcome else ()
            )
            if outcome is not None and not outcome.supported:
                # Nothing supported the answer, so nothing may be shown as
                # supporting it. Leaving the passages on screen under an "I
                # don't know" is what makes a refusal look like a bug.
                payload["citations"] = []
                payload["unsupported_reason"] = outcome.reason
            # Constructed from what was actually retrieved, never generated.
            # A suggestion that turns out to be unanswerable advertises
            # coverage the corpus does not have and spends the reader's trust
            # to do it. Only offered when the answer stood up: proposing
            # follow-ups to "I could not find that" is noise.
            if outcome is not None and outcome.supported:
                payload["followups"] = [
                    {"question": f.question, "because": f.because}
                    for f in suggest_followups(
                        evidence.citations, question=body.question
                    )
                ]
            else:
                payload["followups"] = []

            payload["answered_by"] = {
                "provider": answerer.name, "model": answerer.model_id,
                "grounded": bool(outcome and outcome.supported),
                "cited": list(outcome.cited) if outcome else [],
            }
            yield _sse({"type": "complete", **payload})
            elapsed = (dt.datetime.now() - started).total_seconds() * 1000
            yield _sse({"type": "timing", "elapsed_ms": round(elapsed)})
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            # Without this a reverse proxy buffers the whole stream and the
            # reader gets a long silence followed by a wall of text.
            "x-accel-buffering": "no",
        },
    )


def _compose(evidence) -> list[str]:
    """Assemble the prose from the evidence.

    Deliberately mechanical, not a model call. Every sentence here is derived
    from a citation that exists, which is the only way "a claim with no
    supporting span is not emitted" can be a guarantee rather than a hope. A
    generator sits above this later; the constraint does not change.
    """
    if evidence.refused:
        text = evidence.refusal_reason or "No supported answer was found."
        if evidence.trace.forbidden_count:
            text += (
                " Material relevant to this question exists that this role "
                "cannot open; contact the owning team."
            )
        return [w + " " for w in text.split()]

    out: list[str] = []
    if evidence.conflicts:
        out.append("Sources disagree. ")
        for conflict in evidence.conflicts:
            names = " and ".join(
                f"{c.title} ({c.authority})" for c in conflict.citations
            )
            out.append(f"On {conflict.subject}, {names} do not agree. ")
        out.append("Both are shown below with their dates and owners. ")

    top = evidence.citations[0] if evidence.citations else None
    if top:
        out.append(
            f"Based on {top.title}"
            + (f", {top.heading_path[-1]}" if top.heading_path else "")
            + f" (updated {top.updated_at.date() if top.updated_at else 'unknown'}): "
        )
        out.extend(w + " " for w in top.span.replace("\n", " ").split()[:70])
    for notice in evidence.notices:
        out.append(f" {notice} ")
    return out


# -------------------------------------------------------------------- uploads


ACCEPT_REASONS = {
    "not_in_index": "Not held in the index at all",
    "restricted": "Held elsewhere but cannot be indexed centrally",
    "unresolvable": "Indexed, but no URL or identifier resolves to it",
}


@router.post("/api/collections/{slug}/uploads")
async def upload_file(slug: str, request: Request):
    """Accept a file — after checking whether the index already has it.

    Order matters and is not cosmetic (CNT-COL-20): upload is offered *after*
    resolution has failed, because a group that starts by uploading will upload
    things the index already holds. A second copy is not a convenience, it is a
    divergence with a date on it: the copy gets answered from after the original
    changes, and the reader who follows the citation sees something the system
    of record no longer says.
    """
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(400, "no file")

    blob = await upload.read()
    filename = upload.filename or "upload"
    reason = str(form.get("reason") or "")
    actor = str(form.get("actor") or "admin")

    if reason and reason not in ACCEPT_REASONS:
        raise HTTPException(400, f"unknown reason {reason}")

    from ..adapters.parsers.registry import parse_document
    from ..adapters.parsers.sniff import sniff
    from ..domain.chunks import CHUNKER_VERSION
    from ..domain.ids import file_hash, text_hash

    mime = sniff(blob, upload.content_type)
    parsed = parse_document(filename, blob, declared_mime=mime, sandbox=False)
    fhash = file_hash(blob)
    thash = (
        text_hash(parsed.full_text(), parsed.parser_id, parsed.parser_version, CHUNKER_VERSION)
        if not parsed.refused else None
    )
    title = _title_of(parsed, filename)

    duplicates = _find_duplicates(title, filename)

    with _sessions()() as session:
        collection = _collection_row(session, slug)
        existing = session.execute(text(
            f"SELECT id, filename FROM {S}.upload WHERE org_id = :o AND file_hash = :h"
        ), {"o": _org(session), "h": fhash}).mappings().one_or_none()
        if existing:
            # The same bytes, already here. Silently creating a second row would
            # make the corpus disagree with itself for no reason at all.
            return {
                "status": "already_uploaded",
                "filename": existing["filename"],
                "message": f"These exact bytes were already uploaded as "
                           f"{existing['filename']}.",
            }

        status = "pending" if (duplicates and not reason) else "accepted"
        uid = session.execute(text(f"""
            INSERT INTO {S}.upload (org_id, collection_id, filename, mime, size_bytes,
                file_hash, text_hash, parser_id, parser_version, parse_path,
                parse_quality, refusal_reason, title, blob, accepted_reason,
                accepted_by, duplicate_of, status)
            VALUES (:o, :c, :f, :m, :sz, :fh, :th, :pid, :pv, :pp, :pq, :rr, :t,
                    :blob, :reason, :actor, :dup, :status)
            RETURNING id
        """), {
            "o": _org(session), "c": collection["id"], "f": filename, "m": mime,
            "sz": len(blob), "fh": fhash, "th": thash,
            "pid": parsed.parser_id, "pv": parsed.parser_version,
            "pp": str(parsed.parse_path),
            "pq": json.dumps(parsed.quality.model_dump(mode="json")),
            "rr": parsed.refusal_reason, "t": title, "blob": blob,
            "reason": reason or None, "actor": actor,
            "dup": duplicates[0]["doc_id"] if duplicates else None,
            "status": status,
        }).scalar_one()

        if status == "accepted":
            session.execute(text(f"""
                INSERT INTO {S}.collection_member
                    (org_id, collection_id, doc_id, title, state, resolved_via)
                VALUES (:o, :c, :d, :t, 'member', 'upload')
                ON CONFLICT (collection_id, doc_id) DO NOTHING
            """), {"o": _org(session), "c": collection["id"],
                   "d": f"upload:{uid}", "t": title})
        session.commit()

    return {
        "id": str(uid),
        "status": status,
        "filename": filename,
        "mime": mime,
        "size_bytes": len(blob),
        "title": title,
        "parse_path": str(parsed.parse_path),
        "refused": parsed.refused,
        "refusal_reason": parsed.refusal_reason,
        "blocks": len(parsed.blocks),
        # The check that stops a duplicate becoming a divergence.
        "duplicates": duplicates,
        "reasons": ACCEPT_REASONS,
        "message": (
            f"This looks like {duplicates[0]['title']} ({duplicates[0]['doc_id']}), "
            f"which is already in the index. Use that instead, or say why a copy "
            f"is needed."
            if duplicates and status == "pending"
            else "Accepted."
        ),
    }


def _title_of(parsed, filename: str) -> str:
    """The document's own title, then a heading, then the filename.

    Never the first block of body text: for a PDF that is the title run
    together with the opening paragraph, and searching the index with a whole
    paragraph ranks the *right* document third. That is how the first version
    of the duplicate check missed a document the index plainly held.
    """
    if parsed.title:
        return parsed.title.strip()[:200]
    for block in parsed.blocks:
        if str(block.kind) == "heading" and block.text.strip():
            return block.text.strip()[:200]
    for block in parsed.blocks:
        text = block.text.strip()
        if text:
            # First sentence, and only if it is short enough to be a title.
            head = text.split(". ")[0]
            return (head if len(head) <= 120 else text[:120])[:200]
    return filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")


def _find_duplicates(title: str, filename: str) -> list[dict]:
    """Is this already in the index?

    Checked by title and by filename slug, which is what the platform can see
    without holding a hash of every indexed document. It will not catch a
    re-typed document under a different name — and that limitation is the
    argument for asking the index owners for content hashes.
    """
    from ..ports.content_index import IndexFilters

    platform = _platform()
    probe = title or filename
    seen: dict[str, dict] = {}
    for kb in platform.index.list_knowledgebases():
        try:
            page = platform.index.search(kb.kb_id, probe, IndexFilters(), k=3)
        except Exception:  # noqa: BLE001
            continue
        for hit in page.hits:
            meta = hit.metadata or {}
            hit_title = str(meta.get("title") or "")
            # Title equality is the reliable signal; similarity is the fallback.
            # A vector score is a statement about prose, and two different
            # documents about the same subject score highly against each other.
            if _same_slug(hit_title, probe) or hit.score > 0.72:
                seen[hit.doc_id] = {
                    "doc_id": hit.doc_id, "kb_id": hit.kb_id,
                    "title": hit_title or hit.doc_id,
                    "url": meta.get("url"), "score": round(hit.score, 3),
                }
    return sorted(seen.values(), key=lambda d: -d["score"])[:3]


def _same_slug(a: str, b: str) -> bool:
    import re

    norm = lambda v: "-".join(re.findall(r"[a-z0-9]+", (v or "").lower()))  # noqa: E731
    return bool(a) and norm(a) == norm(b)


@router.get("/api/collections/{slug}/uploads")
def list_uploads(slug: str) -> list[dict]:
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        rows = session.execute(text(f"""
            SELECT id, filename, mime, size_bytes, title, parse_path, refusal_reason,
                   accepted_reason, duplicate_of, status, created_at
            FROM {S}.upload WHERE collection_id = :c ORDER BY created_at DESC
        """), {"c": collection["id"]}).mappings().all()
        return [dict(r) | {"id": str(r["id"])} for r in rows]


class UploadDecision(BaseModel):
    reason: str
    actor: str = "admin"


@router.post("/api/collections/{slug}/uploads/{upload_id}/accept")
def accept_upload(slug: str, upload_id: str, body: UploadDecision) -> dict:
    """Accept a duplicate anyway, on the record.

    The reason is required, not optional. Without it the pile of uploads says
    only "people uploaded things"; with it, it says which knowledgebases are
    missing from the index — which is the case for putting them there.
    """
    if body.reason not in ACCEPT_REASONS:
        raise HTTPException(400, f"unknown reason {body.reason}")
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        row = session.execute(text(f"""
            UPDATE {S}.upload SET status = 'accepted', accepted_reason = :r,
                   accepted_by = :a, updated_at = now()
            WHERE id = :u AND collection_id = :c RETURNING title
        """), {"r": body.reason, "a": body.actor, "u": upload_id,
               "c": collection["id"]}).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "unknown upload")
        session.execute(text(f"""
            INSERT INTO {S}.collection_member
                (org_id, collection_id, doc_id, title, state, resolved_via)
            VALUES (:o, :c, :d, :t, 'member', 'upload')
            ON CONFLICT (collection_id, doc_id) DO NOTHING
        """), {"o": _org(session), "c": collection["id"],
               "d": f"upload:{upload_id}", "t": row["title"]})
        session.commit()
    return {"id": upload_id, "status": "accepted", "reason": body.reason}


@router.delete("/api/collections/{slug}/uploads/{upload_id}")
def delete_upload(slug: str, upload_id: str) -> dict:
    with _sessions()() as session:
        collection = _collection_row(session, slug)
        session.execute(text(
            f"DELETE FROM {S}.upload WHERE id = :u AND collection_id = :c"
        ), {"u": upload_id, "c": collection["id"]})
        session.execute(text(
            f"DELETE FROM {S}.collection_member WHERE collection_id = :c AND doc_id = :d"
        ), {"c": collection["id"], "d": f"upload:{upload_id}"})
        session.commit()
    return {"deleted": upload_id}


# ------------------------------------------------------------------- threads
#
# A chat that forgets is a search box with a slower interface. The value of a
# conversation is the second question, and that only works if the first one is
# still there. Same endpoint shape as askdb, so the two products are one thing
# to learn.


class ThreadCreate(BaseModel):
    connector_id: str | None = None
    role: str | None = None
    title: str | None = None


class ThreadPatch(BaseModel):
    title: str | None = None
    role: str | None = None
    archived: bool | None = None


class TurnCreate(BaseModel):
    question: str
    answer: str = ""
    evidence: dict = {}
    steps: list = []
    grounded: bool = False
    unsupported_reason: str | None = None
    answered_by: dict = {}
    elapsed_ms: int | None = None
    error: str | None = None


def _thread_row(session, thread_id: str):
    row = session.execute(text(f"""
        SELECT CAST(t.id AS text) AS id, t.title, t.role, t.archived_at,
               t.created_at, t.updated_at,
               c.slug AS connector_id,
               (SELECT count(*) FROM {S}.chat_turn x WHERE x.thread_id = t.id) AS turns
        FROM {S}.chat_thread t
        LEFT JOIN {S}.connector c ON c.id = t.connector_id
        WHERE t.id = CAST(:id AS uuid) AND t.org_id = :o
    """), {"id": thread_id, "o": _org(session)}).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "no such thread")
    return dict(row)


@router.post("/api/threads", status_code=201)
def create_thread(body: ThreadCreate) -> dict:
    with _sessions()() as session:
        connector_id = None
        if body.connector_id:
            connector_id = session.execute(text(
                f"SELECT id FROM {S}.connector WHERE org_id = :o AND slug = :s"
            ), {"o": _org(session), "s": body.connector_id}).scalar_one_or_none()

        thread_id = session.execute(text(f"""
            INSERT INTO {S}.chat_thread (org_id, connector_id, title, role)
            VALUES (:o, :c, :t, :r) RETURNING CAST(id AS text)
        """), {
            "o": _org(session), "c": connector_id,
            "t": body.title, "r": body.role,
        }).scalar_one()
        session.commit()
        return _thread_row(session, thread_id)


@router.get("/api/threads")
def list_threads(connector_id: str | None = None, limit: int = 100) -> list[dict]:
    """Most recently used first — the order people actually look for a
    conversation in. Archived threads are excluded rather than dimmed: a list
    that shows everything forever stops being a list."""
    with _sessions()() as session:
        rows = session.execute(text(f"""
            SELECT CAST(t.id AS text) AS id, t.title, t.role,
                   t.created_at, t.updated_at,
                   c.slug AS connector_id,
                   (SELECT count(*) FROM {S}.chat_turn x WHERE x.thread_id = t.id) AS turns
            FROM {S}.chat_thread t
            LEFT JOIN {S}.connector c ON c.id = t.connector_id
            WHERE t.org_id = :o AND t.archived_at IS NULL
              AND (CAST(:conn AS text) IS NULL OR c.slug = CAST(:conn AS text))
            ORDER BY t.updated_at DESC
            LIMIT :n
        """), {"o": _org(session), "conn": connector_id, "n": limit}).mappings().all()
        return [dict(r) for r in rows]


@router.get("/api/threads/{thread_id}")
def get_thread(thread_id: str) -> dict:
    with _sessions()() as session:
        thread = _thread_row(session, thread_id)
        turns = session.execute(text(f"""
            SELECT CAST(id AS text) AS id, ordinal, question, answer, evidence,
                   steps, grounded, unsupported_reason, answered_by,
                   elapsed_ms, error, created_at
            FROM {S}.chat_turn
            WHERE thread_id = CAST(:id AS uuid) AND org_id = :o
            ORDER BY ordinal
        """), {"id": thread_id, "o": _org(session)}).mappings().all()
        return thread | {"turns": [dict(t) for t in turns]}


@router.patch("/api/threads/{thread_id}")
def update_thread(thread_id: str, body: ThreadPatch) -> dict:
    with _sessions()() as session:
        _thread_row(session, thread_id)
        session.execute(text(f"""
            UPDATE {S}.chat_thread SET
                title = coalesce(:t, title),
                role = coalesce(:r, role),
                archived_at = CASE WHEN :arch IS NULL THEN archived_at
                                   WHEN :arch THEN now() ELSE NULL END,
                updated_at = now()
            WHERE id = CAST(:id AS uuid) AND org_id = :o
        """), {
            "id": thread_id, "o": _org(session), "t": body.title,
            "r": body.role, "arch": body.archived,
        })
        session.commit()
        return _thread_row(session, thread_id)


@router.delete("/api/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: str) -> None:
    with _sessions()() as session:
        _thread_row(session, thread_id)
        session.execute(text(
            f"DELETE FROM {S}.chat_thread WHERE id = CAST(:id AS uuid) AND org_id = :o"
        ), {"id": thread_id, "o": _org(session)})
        session.commit()


@router.delete("/api/threads")
def delete_threads(connector_id: str | None = None) -> dict:
    """Clear the list. Scoped to one connector when given, because "delete all"
    on a screen showing one knowledgebase must not take the others with it."""
    with _sessions()() as session:
        deleted = session.execute(text(f"""
            DELETE FROM {S}.chat_thread t
            USING {S}.connector c
            WHERE t.org_id = :o
              AND (CAST(:conn AS text) IS NULL
                   OR (c.id = t.connector_id AND c.slug = CAST(:conn AS text)))
              AND (CAST(:conn AS text) IS NULL OR t.connector_id IS NOT NULL)
        """), {"o": _org(session), "conn": connector_id}).rowcount
        session.commit()
        return {"deleted": deleted}


@router.post("/api/threads/{thread_id}/turns", status_code=201)
def append_turn(thread_id: str, body: TurnCreate) -> dict:
    """Record one exchange.

    Written by the console after the stream finishes rather than by the stream
    itself: a turn is only worth keeping once it is complete, and a half-written
    answer in a transcript is worse than a missing one.
    """
    with _sessions()() as session:
        thread = _thread_row(session, thread_id)
        org = _org(session)

        # The ordinal is chosen inside the INSERT rather than read first.
        #
        # Two appends racing — a retry, a double-submit, React running an
        # effect twice — both read the same maximum and both write it, and one
        # loses on the unique constraint. The turn is then simply gone, which
        # is worse than a slow write: the answer was given and the transcript
        # does not have it.
        turn_id = session.execute(text(f"""
            INSERT INTO {S}.chat_turn
                (org_id, thread_id, ordinal, question, answer, evidence, steps,
                 grounded, unsupported_reason, answered_by, elapsed_ms, error)
            SELECT :o, CAST(:t AS uuid),
                   coalesce(max(ordinal), 0) + 1,
                   :q, :a, CAST(:ev AS jsonb), CAST(:st AS jsonb), :g, :ur,
                   CAST(:ab AS jsonb), :ms, :err
            FROM {S}.chat_turn WHERE thread_id = CAST(:t AS uuid)
            RETURNING CAST(id AS text), ordinal
        """), {
            "o": org, "t": thread_id, "q": body.question,
            "a": body.answer, "ev": json.dumps(body.evidence, default=str),
            "st": json.dumps(body.steps, default=str), "g": body.grounded,
            "ur": body.unsupported_reason,
            "ab": json.dumps(body.answered_by, default=str),
            "ms": body.elapsed_ms, "err": body.error,
        }).one()

        # The first question becomes the thread's name. People recognise a
        # conversation by what they asked, not by a date or an id.
        session.execute(text(f"""
            UPDATE {S}.chat_thread
               SET updated_at = now(),
                   title = CASE WHEN title IS NULL OR title = ''
                                THEN left(:q, 300) ELSE title END
             WHERE id = CAST(:t AS uuid) AND org_id = :o
        """), {"t": thread_id, "o": org, "q": body.question})
        session.commit()
        return {"id": turn_id[0], "ordinal": turn_id[1], "thread_id": thread["id"]}


# ------------------------------------------------------- feedback and evals
#
# A thumbs-down is a question that was answered badly, which is the same thing
# as a test case nobody has written yet. The two are one feature.


#: Closed, because a free-text reason is a field nobody can group by. Each of
#: these points at a different fix, which is the only justification for asking.
REASONS = {
    "wrong": "The answer is wrong",
    "incomplete": "The answer is incomplete",
    "should_have_answered": "It refused, but the answer is in our content",
    "should_not_have_answered": "It answered from the wrong thing",
    "outdated": "The source it cited is out of date",
    "unclear": "The answer is hard to follow",
}


class FeedbackCreate(BaseModel):
    connector_id: str
    question: str
    answer: str = ""
    citations: list[dict] = []
    verdict: str
    reason: str | None = None
    comment: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    actor: str | None = None


@router.get("/api/feedback/reasons")
def feedback_reasons() -> dict:
    return {"reasons": [{"value": k, "label": v} for k, v in REASONS.items()]}


@router.post("/api/feedback", status_code=201)
def create_feedback(body: FeedbackCreate) -> dict:
    if body.verdict not in ("helpful", "unhelpful"):
        raise HTTPException(400, "verdict must be helpful or unhelpful")
    if body.reason and body.reason not in REASONS:
        raise HTTPException(400, f"unknown reason {body.reason}")

    with _sessions()() as session:
        cid = _connector_id(session, body.connector_id)
        fid = session.execute(text(f"""
            INSERT INTO {S}.answer_feedback
                (org_id, connector_id, thread_id, turn_id, question, answer,
                 citations, verdict, reason, comment, actor)
            VALUES (:o, :c, CAST(NULLIF(:th, '') AS uuid),
                    CAST(NULLIF(:tu, '') AS uuid), :q, :a, CAST(:cit AS jsonb),
                    :v, :r, :cm, :actor)
            RETURNING CAST(id AS text)
        """), {
            "o": _org(session), "c": cid, "th": body.thread_id or "",
            "tu": body.turn_id or "", "q": body.question, "a": body.answer,
            "cit": json.dumps(body.citations, default=str), "v": body.verdict,
            "r": body.reason, "cm": body.comment, "actor": body.actor,
        }).scalar_one()
        session.commit()
    return {"id": fid}


@router.get("/api/connectors/{slug}/feedback")
def list_feedback(slug: str, open_only: bool = True, limit: int = 50) -> dict:
    """Unhelpful first, and by default only the ones not yet turned into tests.

    A review queue that also lists what has already been dealt with grows
    without bound and stops being looked at.
    """
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rows = session.execute(text(f"""
            SELECT CAST(id AS text) AS id, question, answer, citations, verdict,
                   reason, comment, actor, created_at,
                   CAST(promoted_case_id AS text) AS promoted_case_id
            FROM {S}.answer_feedback
            WHERE connector_id = :c AND org_id = :o
              AND (NOT :open_only OR (verdict = 'unhelpful' AND promoted_case_id IS NULL))
            ORDER BY (verdict = 'unhelpful') DESC, created_at DESC
            LIMIT :n
        """), {"c": cid, "o": _org(session), "open_only": open_only, "n": limit}).mappings().all()

        counts = session.execute(text(f"""
            SELECT verdict, count(*) AS n FROM {S}.answer_feedback
            WHERE connector_id = :c AND org_id = :o GROUP BY verdict
        """), {"c": cid, "o": _org(session)}).mappings().all()

    tally = {r["verdict"]: r["n"] for r in counts}
    helpful, unhelpful = tally.get("helpful", 0), tally.get("unhelpful", 0)
    return {
        "items": [dict(r) for r in rows],
        "helpful": helpful,
        "unhelpful": unhelpful,
        # Reported as a share only when there is enough to mean anything. Three
        # ratings out of two hundred conversations is not a satisfaction rate,
        # and printing one as though it were invites a decision on noise.
        "rate": round(helpful / (helpful + unhelpful), 3)
        if helpful + unhelpful >= 10 else None,
        "reasons": [{"value": k, "label": v} for k, v in REASONS.items()],
    }


class CaseCreate(BaseModel):
    question: str
    expectations: list[dict] = []
    note: str = ""
    role: str | None = None
    origin: str = "authored"
    from_feedback: str | None = None


@router.get("/api/connectors/{slug}/evals")
def list_cases(slug: str) -> dict:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        cases = session.execute(text(f"""
            SELECT CAST(id AS text) AS id, question, expectations, note, origin,
                   enabled, role, created_at
            FROM {S}.eval_case WHERE connector_id = :c AND org_id = :o
            ORDER BY created_at
        """), {"c": cid, "o": _org(session)}).mappings().all()
        runs = session.execute(text(f"""
            SELECT CAST(id AS text) AS id, started_at, finished_at, total,
                   passed, failed, context
            FROM {S}.eval_run WHERE connector_id = :c AND org_id = :o
            ORDER BY started_at DESC LIMIT 10
        """), {"c": cid, "o": _org(session)}).mappings().all()

        latest = None
        if runs:
            latest = [
                dict(r) for r in session.execute(text(f"""
                    SELECT CAST(case_id AS text) AS case_id, question, passed,
                           failures, answer, cited, grounded, elapsed_ms
                    FROM {S}.eval_result WHERE run_id = CAST(:r AS uuid)
                """), {"r": runs[0]["id"]}).mappings().all()
            ]

    return {
        "cases": [dict(c) for c in cases],
        "runs": [dict(r) for r in runs],
        "latest_results": latest or [],
        "kinds": [
            {"value": "answers", "label": "Answers the question"},
            {"value": "refuses", "label": "Refuses to answer"},
            {"value": "cites", "label": "Cites a document"},
            {"value": "cites_first", "label": "Cites a document first"},
            {"value": "cites_something", "label": "Cites at least one source"},
            {"value": "says", "label": "Says exactly"},
            {"value": "does_not_say", "label": "Does not say"},
        ],
    }


@router.post("/api/connectors/{slug}/evals", status_code=201)
def create_case(slug: str, body: CaseCreate) -> dict:
    from ..domain.expectations import KINDS

    for expectation in body.expectations:
        if expectation.get("kind") not in KINDS:
            raise HTTPException(400, f"unknown expectation {expectation.get('kind')}")

    with _sessions()() as session:
        cid = _connector_id(session, slug)
        case_id = session.execute(text(f"""
            INSERT INTO {S}.eval_case
                (org_id, connector_id, question, expectations, note, origin, role)
            VALUES (:o, :c, :q, CAST(:e AS jsonb), :n, :orig, :role)
            RETURNING CAST(id AS text)
        """), {
            "o": _org(session), "c": cid, "q": body.question,
            "e": json.dumps(body.expectations), "n": body.note,
            "orig": body.origin, "role": body.role,
        }).scalar_one()

        # Closing the loop: the complaint leaves the queue because it has
        # become a test, not because somebody dismissed it.
        if body.from_feedback:
            session.execute(text(f"""
                UPDATE {S}.answer_feedback SET promoted_case_id = CAST(:case AS uuid),
                       updated_at = now()
                 WHERE id = CAST(:f AS uuid) AND org_id = :o
            """), {"case": case_id, "f": body.from_feedback, "o": _org(session)})
        session.commit()
    return {"id": case_id}


@router.delete("/api/connectors/{slug}/evals/{case_id}")
def delete_case(slug: str, case_id: str) -> dict:
    with _sessions()() as session:
        session.execute(text(
            f"DELETE FROM {S}.eval_case WHERE id = CAST(:i AS uuid) AND org_id = :o"
        ), {"i": case_id, "o": _org(session)})
        session.commit()
    return {"deleted": case_id}


@router.post("/api/connectors/{slug}/evals/run")
def run_evals(slug: str, body: dict | None = None) -> dict:
    from ..services.evaluation import EvaluationService

    platform = _platform()
    service = EvaluationService(platform, _sessions(), _org_of())
    return service.run(slug, case_ids=(body or {}).get("case_ids"))
