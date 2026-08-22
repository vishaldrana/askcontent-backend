"""Proposing glossary terms from the indexed corpus."""

from __future__ import annotations

from sqlalchemy import text

from ..config import settings
from ..domain.glossary import discover

S = settings.db_schema


class GlossaryService:
    def __init__(self, sessions, org_id) -> None:
        self.sessions = sessions
        self.org_id = org_id

    def discover_for(self, connector_slug: str, limit: int = 60) -> dict:
        """Read the indexed chunks and propose terms.

        Runs over our own chunks rather than re-fetching: the corpus is already
        parsed, and a discovery pass that re-downloaded every document would
        cost more than the feature is worth.
        """
        with self.sessions() as session:
            connector_id = session.execute(text(
                f"SELECT id FROM {S}.connector WHERE org_id = :o AND slug = :s"
            ), {"o": self.org_id, "s": connector_slug}).scalar_one()

            rows = session.execute(text(f"""
                SELECT d.doc_id, string_agg(c.text, ' ' ORDER BY c.ordinal) AS body
                FROM {S}.document_chunk c
                JOIN {S}.document d ON d.id = c.document_id
                -- Prose only. A curl example is not a glossary of HTTP verbs,
                -- and a term list offering `POST` teaches the reviewer to skim.
                WHERE c.connector_id = :c AND NOT c.is_code
                GROUP BY d.doc_id
            """), {"c": connector_id}).all()

            if not rows:
                return {"proposed": 0, "note": "nothing indexed yet — run the indexer first"}

            proposals = discover([(r.doc_id, r.body or "") for r in rows], limit=limit)

            existing = {
                r.term.upper()
                for r in session.execute(text(
                    f"SELECT term FROM {S}.glossary_term WHERE connector_id = :c"
                ), {"c": connector_id}).all()
            }

            added = 0
            for proposal in proposals:
                if proposal.term.upper() in existing:
                    # Never overwrite a term a person has already ruled on —
                    # including one they rejected, or the rejection would be
                    # undone on every discovery run.
                    continue
                session.execute(text(f"""
                    INSERT INTO {S}.glossary_term (
                        org_id, connector_id, term, definition, aliases, source,
                        status, method, confidence, occurrences, documents, evidence
                    ) VALUES (
                        :o, :c, :term, :definition, :aliases, 'discovered',
                        'proposed', :method, :confidence, :occurrences, :documents, :evidence
                    )
                    ON CONFLICT (connector_id, term) DO NOTHING
                """), {
                    "o": self.org_id, "c": connector_id, "term": proposal.term,
                    "definition": proposal.definition, "aliases": list(proposal.aliases),
                    "method": proposal.method, "confidence": proposal.confidence,
                    "occurrences": proposal.occurrences, "documents": proposal.documents,
                    "evidence": list(proposal.evidence),
                })
                added += 1

            session.commit()
            return {
                "documents_scanned": len(rows),
                "proposed": added,
                "already_known": len(proposals) - added,
                "summary": f"{added} new terms proposed from {len(rows)} documents",
            }
