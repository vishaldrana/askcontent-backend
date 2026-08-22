"""Filling in what a collection's members are, beyond an identifier.

Reviewing a knowledgebase means answering four questions per page — what is it,
when was it written, when did it last change, and which rule pulled it in. The
index answers the last one and often none of the others, so the rest is
recovered here: from the store's metadata where it exists, and from the
document's own text where it does not.

Every recovered value carries where it came from. A date read out of prose is
weaker evidence than one the system of record supplied, and a reviewer deciding
whether a policy is current has to be able to tell.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from ..config import settings
from ..domain.dates import DateSource, resolve_dates, summarise
from ..domain.documents import DocRef
from ..domain.ids import file_hash

S = settings.db_schema


class EnrichmentService:
    def __init__(self, platform, sessions, org_id) -> None:
        self.platform = platform
        self.sessions = sessions
        self.org_id = org_id

    def enrich_collection(self, slug: str, limit: int = 500) -> dict:
        with self.sessions() as session:
            collection = session.execute(text(
                f"SELECT id FROM {S}.collection WHERE org_id = :o AND slug = :s"
            ), {"o": self.org_id, "s": slug}).mappings().one_or_none()
            if collection is None:
                raise KeyError(slug)

            members = session.execute(text(f"""
                SELECT doc_id, kb_id FROM {S}.collection_member
                WHERE collection_id = :c AND state <> 'removed'
                ORDER BY last_checked_at NULLS FIRST LIMIT :n
            """), {"c": collection["id"], "n": limit}).mappings().all()

            counts = {"enriched": 0, "unreadable": 0, "dates_from_content": 0}

            for member in members:
                if member["doc_id"].startswith("upload:"):
                    # Ours, not the store's. Counting an upload as unreadable
                    # would put a permanent false number on the review screen.
                    counts["uploads"] = counts.get("uploads", 0) + 1
                    continue
                detail = self._describe(member["doc_id"], member["kb_id"])
                if detail is None:
                    counts["unreadable"] += 1
                    session.execute(text(f"""
                        UPDATE {S}.collection_member
                        SET last_checked_at = now()
                        WHERE collection_id = :c AND doc_id = :d
                    """), {"c": collection["id"], "d": member["doc_id"]})
                    continue

                if detail["usource"] == str(DateSource.CONTENT):
                    counts["dates_from_content"] += 1
                counts["enriched"] += 1

                session.execute(text(f"""
                    UPDATE {S}.collection_member SET
                        title = coalesce(:title, title),
                        url = coalesce(:url, url),
                        description = :description,
                        space = :space, path = :path, owner = :owner,
                        doc_type = :doc_type,
                        source_created_at = :created,
                        source_updated_at = :updated,
                        created_source = :csource,
                        updated_source = :usource,
                        date_evidence = :evidence,
                        content_hash = CAST(:hash AS varchar),
                        last_changed_at = CASE
                            WHEN content_hash IS DISTINCT FROM CAST(:hash AS varchar) THEN now()
                            ELSE last_changed_at END,
                        last_checked_at = now()
                    WHERE collection_id = :c AND doc_id = :d
                """), {"c": collection["id"], "d": member["doc_id"], **detail})

            session.commit()
            return counts

    def _describe(self, doc_id: str, kb_id: str | None) -> dict | None:
        """Everything a reviewer needs about one page."""
        from ..adapters.parsers.registry import parse_document
        from ..ports.content_repository import ResolutionOutcome

        ref = DocRef(doc_id=doc_id, kb_id=kb_id or "")
        try:
            resolution = self.platform.repository.fetch_metadata(ref, "service")
        except Exception:  # noqa: BLE001
            return None
        if resolution.outcome is not ResolutionOutcome.RESOLVED or not resolution.metadata:
            return None

        meta = resolution.metadata
        body = ""
        content_hash = None
        try:
            raw = self.platform.repository.fetch(ref, "service")
            content_hash = file_hash(raw.blob)
            parsed = parse_document(doc_id, raw.blob, declared_mime=raw.mime, sandbox=False)
            body = parsed.full_text()
        except Exception:  # noqa: BLE001
            # Metadata without content is still worth having; the description
            # and any content-derived date simply stay empty.
            pass

        created, updated = resolve_dates(meta.updated_at, body)

        from ..domain.catalog import classify

        doc_type = None
        try:
            doc_type = str(classify(meta, None).doc_type)
        except Exception:  # noqa: BLE001
            pass

        return {
            "title": meta.title,
            "url": meta.url,
            "description": summarise(body),
            "space": meta.space,
            "path": meta.path,
            "owner": meta.owner,
            "doc_type": doc_type,
            "created": created.value,
            "updated": updated.value,
            "csource": str(created.source),
            "usource": str(updated.source),
            "evidence": updated.evidence or created.evidence,
            "hash": content_hash,
        }

    def check_for_updates(self, slug: str, limit: int = 500) -> dict:
        """Re-check members by URL and report what moved.

        The comparison is on the content hash rather than on a reported date,
        because a source that does not maintain a modified date is exactly the
        source whose dates cannot be trusted to detect a change — and one that
        does can still touch the date without changing a word.
        """
        with self.sessions() as session:
            collection = session.execute(text(
                f"SELECT id FROM {S}.collection WHERE org_id = :o AND slug = :s"
            ), {"o": self.org_id, "s": slug}).mappings().one_or_none()
            if collection is None:
                raise KeyError(slug)

            members = session.execute(text(f"""
                SELECT doc_id, kb_id, content_hash, source_updated_at
                FROM {S}.collection_member
                WHERE collection_id = :c AND state <> 'removed'
                ORDER BY last_checked_at NULLS FIRST LIMIT :n
            """), {"c": collection["id"], "n": limit}).mappings().all()

            report = {"checked": 0, "changed": 0, "gone": 0, "unchanged": 0,
                      "changed_docs": []}

            for member in members:
                report["checked"] += 1
                detail = self._describe(member["doc_id"], member["kb_id"])
                if detail is None:
                    # Present in the collection, no longer readable at source.
                    report["gone"] += 1
                    session.execute(text(f"""
                        UPDATE {S}.collection_member
                        SET missing_since = coalesce(missing_since, now()),
                            last_checked_at = now()
                        WHERE collection_id = :c AND doc_id = :d
                    """), {"c": collection["id"], "d": member["doc_id"]})
                    continue

                changed = (
                    member["content_hash"] is not None
                    and detail["hash"] is not None
                    and member["content_hash"] != detail["hash"]
                )
                if changed:
                    report["changed"] += 1
                    report["changed_docs"].append(member["doc_id"])
                else:
                    report["unchanged"] += 1

                session.execute(text(f"""
                    UPDATE {S}.collection_member SET
                        title = coalesce(:title, title), url = coalesce(:url, url),
                        description = :description, space = :space, path = :path,
                        owner = :owner, doc_type = :doc_type,
                        source_created_at = :created, source_updated_at = :updated,
                        created_source = :csource, updated_source = :usource,
                        date_evidence = :evidence, content_hash = CAST(:hash AS varchar),
                        missing_since = NULL,
                        last_changed_at = CASE
                            WHEN content_hash IS DISTINCT FROM CAST(:hash AS varchar)
                            THEN now() ELSE last_changed_at END,
                        last_checked_at = now()
                    WHERE collection_id = :c AND doc_id = :d
                """), {"c": collection["id"], "d": member["doc_id"], **detail})

            session.commit()
            return report
