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


@router.get("/api/connectors/{slug}/roles/{role_id}/effective")
def effective_access(slug: str, role_id: str) -> dict:
    """What this role can actually reach, computed rather than asserted.

    An access screen that lists grants tells you what was configured. This tells
    you what it *means*, which is the only version anybody can act on.
    """
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

    connector = platform.registry.get(slug)
    population = scope_population(platform.index, connector)
    principal = principals[0] if principals else "group:all-staff"

    from ..domain.documents import DocRef
    from ..ports.content_repository import ResolutionOutcome

    in_scope = [m for m in population if evaluate(connector.scope, m).in_scope]
    readable, forbidden = [], []
    for meta in in_scope[:200]:
        outcome = platform.repository.authorize(
            principal, DocRef(doc_id=meta.doc_id, kb_id=meta.kb_id)
        )
        (readable if outcome is ResolutionOutcome.RESOLVED else forbidden).append(meta.title)

    return {
        "role": name, "principals": principals, "principal_used": principal,
        "in_scope": len(in_scope), "readable": len(readable),
        "forbidden": len(forbidden), "forbidden_sample": forbidden[:8],
        "note": "Computed against the store for the first 200 in-scope documents.",
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
            SET status = :s, reviewed_by = :who, reviewed_at = now(),
                definition = coalesce(:definition, definition),
                source = CASE WHEN :s = 'confirmed' THEN 'human' ELSE source END
            WHERE id = :t
        """), {"s": body.status, "who": body.actor, "t": term_id,
               "definition": body.definition})
        session.commit()
    return {"id": term_id, "status": body.status}


@router.post("/api/connectors/{slug}/glossary")
def create_term(slug: str, body: TermCreate) -> dict:
    with _sessions()() as session:
        cid = _connector_id(session, slug)
        rid = session.execute(text(f"""
            INSERT INTO {S}.glossary_term (org_id, connector_id, term, definition, aliases, source)
            VALUES (:o, :c, :t, :d, :a, 'human')
            ON CONFLICT (connector_id, term) DO UPDATE
              SET definition = EXCLUDED.definition, aliases = EXCLUDED.aliases
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
            SELECT id, name, publishable_key, allowed_origins, is_active, created_at
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


@router.delete("/api/connectors/{slug}/embeds/{embed_id}")
def delete_embed(slug: str, embed_id: str) -> dict:
    with _sessions()() as session:
        session.execute(text(f"DELETE FROM {S}.embed WHERE id = :e"), {"e": embed_id})
        session.commit()
    return {"deleted": embed_id}


# ------------------------------------------------------------------- settings


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

    return {
        "connector": slug,
        "state": str(connector.state),
        "limits": {
            "max_documents": connector.scope.max_documents,
            "max_bytes": connector.scope.max_bytes,
            "sensitivity_ceiling": str(connector.scope.sensitivity_ceiling),
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
    #: A **role**, not a person. Access is defined by roles on the Access
    #: screen; asking as an ad-hoc principal would let the console invent an
    #: identity the platform never granted.
    role: str | None = None


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

            evidence = platform.retrieval.retrieve(connector, spec, principal)
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

            for line in _compose(evidence):
                yield _sse({"type": "token", "text": line})

            yield _sse({"type": "complete", **evidence.model_dump(mode="json")})
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
