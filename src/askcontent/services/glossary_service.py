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

            added = refreshed = 0
            for proposal in proposals:
                if proposal.term.upper() in existing:
                    # Refresh what was *measured*; never touch what was
                    # *judged*.
                    #
                    # Skipping known terms entirely — which is what this did —
                    # freezes their counts at whatever the corpus looked like
                    # the first time. Three terms here still claimed forty
                    # documents from a period when every page carried the
                    # navigation menu, long after that was stripped, and the
                    # evidence quoted the menu. A reviewer cannot judge a
                    # proposal on numbers that describe a corpus that no longer
                    # exists.
                    #
                    # Status, definition, aliases and the reviewer's name are
                    # untouched, so a confirmation or a rejection survives
                    # every future run. That is the part that must never be
                    # undone; the counts are just observations.
                    session.execute(text(f"""
                        UPDATE {S}.glossary_term SET
                            method = :method, confidence = :confidence,
                            occurrences = :occurrences, documents = :documents,
                            evidence = :evidence, updated_at = now()
                        WHERE connector_id = :c AND upper(term) = upper(:term)
                    """), {
                        "c": connector_id, "term": proposal.term,
                        "method": proposal.method, "confidence": proposal.confidence,
                        "occurrences": proposal.occurrences,
                        "documents": proposal.documents,
                        "evidence": list(proposal.evidence),
                    })
                    refreshed += 1
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

            # Then measure every term directly, rather than inferring
            # anything from this pass.
            #
            # `limit` bounds discovery, so a term missing from `proposals` may
            # have fallen below the cut rather than left the corpus — and
            # guessing either way is wrong. SMTP sat at "40 documents" from a
            # period when every page carried the navigation menu; it is in one.
            # A stale count and an inferred zero are both fact-shaped lies, and
            # the fact is one query away.
            measured = session.execute(text(f"""
                UPDATE {S}.glossary_term g SET
                    documents = m.documents,
                    occurrences = m.occurrences,
                    updated_at = now()
                FROM (
                    SELECT t.id,
                           count(DISTINCT d.doc_id) AS documents,
                           coalesce(sum(
                               (length(c.text) - length(
                                   regexp_replace(lower(c.text), lower(t.term), '', 'g')
                               )) / greatest(length(t.term), 1)
                           ), 0) AS occurrences
                    FROM {S}.glossary_term t
                    LEFT JOIN {S}.document_chunk c
                           ON c.connector_id = t.connector_id
                          AND NOT c.is_code
                          -- Whole word. Without the boundaries "API" matches
                          -- "rapid" and every count is fiction.
                          AND c.text ~* ('\y' || t.term || '\y')
                    LEFT JOIN {S}.document d ON d.id = c.document_id
                    WHERE t.connector_id = :c
                    GROUP BY t.id
                ) m
                WHERE g.id = m.id
            """), {"c": connector_id}).rowcount

            session.commit()
            return {
                "documents_scanned": len(rows),
                "proposed": added,
                "refreshed": refreshed,
                "measured": measured,
                "already_known": refreshed,
                "summary": (
                    f"{added} new, {measured} terms measured "
                    f"against {len(rows)} documents"
                ),
            }
