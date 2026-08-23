"""Running the eval suite.

The question worth answering about a retrieval change is never "does it pass"
but **"what did it break"** — so every run is kept, with the configuration that
was in force when it ran. A pass rate with no record of the reranker, the
embedding model and the freshness policy behind it cannot be compared with
another one, and comparing them is the entire purpose.

Cases run through the *same* path a reader's question takes: retrieval, the
relevance gate, the answerer, citation verification. An eval that shortcuts any
of those is measuring something nobody experiences.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text

from ..config import settings
from ..domain.expectations import Expectation, Outcome, check
from ..domain.retrieval_spec import Intent, RetrievalSpec

S = settings.db_schema


@dataclass
class CaseResult:
    case_id: str | None
    question: str
    passed: bool
    failures: list[str]
    answer: str
    cited: list[str]
    grounded: bool
    elapsed_ms: int
    #: Set when the answerer failed rather than declined. Such a case is not a
    #: pass and not a content failure: it did not run. Counting it as failed
    #: would make a rate limit look like a regression, and the next person
    #: would go looking for the change that caused it.
    errored: bool = False


class EvaluationService:
    def __init__(self, platform, sessions, org_id) -> None:
        self.platform = platform
        self.sessions = sessions
        self.org_id = org_id

    def run(self, slug: str, *, case_ids: list[str] | None = None) -> dict:
        from ..api.extra import (
            _glossary_for,
            _instructions_for,
            _rules_for_role,
            _run_answer,
        )

        connector = self.platform.registry.get(slug)
        cases = self._cases(slug, case_ids)

        run_id = self._open_run(slug, connector)
        results: list[CaseResult] = []

        for case in cases:
            started = time.perf_counter()
            expectations = [
                Expectation(kind=e.get("kind", ""), value=e.get("value", ""))
                for e in (case["expectations"] or [])
            ]
            role = case["role"]

            spec = RetrievalSpec(
                intent=Intent.LOOKUP,
                scope_ref=f"scope:{connector.connector_id}:v{connector.version}",
                question=case["question"],
                channels=connector.retrieval.channels,
                k_per_channel=connector.retrieval.k_per_channel,
            )
            principal = (
                _principal(slug, role) if role else "group:all-staff"
            )
            evidence = self.platform.retrieval.retrieve(
                connector, spec, principal,
                role_rules=_rules_for_role(slug, role),
                glossary=_glossary_for(slug),
            )

            answer, outcome = "", None
            for chunk, result in _run_answer(
                self.platform, case["question"], evidence.citations, (),
                _instructions_for(slug), evidence.trace.synonyms,
            ):
                if result is not None:
                    outcome = result
                else:
                    answer += chunk

            grounded = bool(outcome and outcome.supported)
            errored = bool(outcome is not None and getattr(outcome, "error", None))
            # Only what the answer *cited*, not everything retrieved. An
            # expectation that a document was cited must not be satisfied by it
            # merely having been considered.
            cited = [
                evidence.citations[n - 1].title
                for n in (outcome.cited if outcome else ())
                if 1 <= n <= len(evidence.citations)
            ]

            failures = (
                [f"the answerer failed: {outcome.error}"]
                if errored
                else check(
                    expectations,
                    Outcome(answer=answer.strip(), grounded=grounded, cited=tuple(cited)),
                )
            )
            results.append(
                CaseResult(
                    case_id=case["id"], question=case["question"],
                    passed=not failures, failures=failures, errored=errored,
                    answer=answer.strip(), cited=cited, grounded=grounded,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
            )

        self._close_run(run_id, results)
        return {
            "run_id": run_id,
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [r.__dict__ for r in results],
        }

    # -- persistence -------------------------------------------------------

    def _cases(self, slug: str, case_ids: list[str] | None) -> list[dict]:
        with self.sessions() as session:
            rows = session.execute(text(f"""
                SELECT CAST(c.id AS text) AS id, c.question, c.expectations, c.role
                FROM {S}.eval_case c
                JOIN {S}.connector n ON n.id = c.connector_id
                WHERE n.slug = :s AND c.org_id = :o AND c.enabled
                  AND (CAST(:ids AS text) IS NULL
                       OR CAST(c.id AS text) = ANY(string_to_array(CAST(:ids AS text), ',')))
                ORDER BY c.created_at
            """), {
                "s": slug, "o": self.org_id,
                "ids": ",".join(case_ids) if case_ids else None,
            }).mappings().all()
        return [dict(r) for r in rows]

    def _open_run(self, slug: str, connector) -> str:
        import json

        answerer = self.platform.answering.answerer
        # Everything that could plausibly move the numbers. Recorded now rather
        # than derived later, because "what was the reranker last Tuesday" is
        # not a question the system can answer retrospectively.
        context = {
            "reranker": getattr(self.platform.reranker, "reranker_id", "?"),
            "reranker_version": getattr(self.platform.reranker, "reranker_version", "?"),
            "embedder": getattr(self.platform.embedder, "model_id", "?"),
            "answerer": answerer.name,
            "answer_model": answerer.model_id,
            "rerank_floor": connector.retrieval.rerank_floor,
            "rerank_shortlist": connector.retrieval.rerank_shortlist,
            "k_per_channel": connector.retrieval.k_per_channel,
            "expired_days": connector.retrieval.freshness.expired_days,
        }
        with self.sessions() as session:
            run_id = session.execute(text(f"""
                INSERT INTO {S}.eval_run (org_id, connector_id, context)
                SELECT :o, n.id, CAST(:ctx AS jsonb)
                FROM {S}.connector n WHERE n.slug = :s AND n.org_id = :o
                RETURNING CAST(id AS text)
            """), {"o": self.org_id, "s": slug, "ctx": json.dumps(context)}).scalar_one()
            session.commit()
        return run_id

    def _close_run(self, run_id: str, results: list[CaseResult]) -> None:
        import json

        with self.sessions() as session:
            for result in results:
                session.execute(text(f"""
                    INSERT INTO {S}.eval_result
                        (org_id, run_id, case_id, question, passed, failures,
                         answer, cited, grounded, elapsed_ms)
                    VALUES (:o, CAST(:run AS uuid),
                            CAST(NULLIF(:case, '') AS uuid), :q, :p,
                            CAST(:f AS jsonb), :a, CAST(:c AS jsonb), :g, :ms)
                """), {
                    "o": self.org_id, "run": run_id, "case": result.case_id or "",
                    "q": result.question, "p": result.passed,
                    "f": json.dumps(result.failures), "a": result.answer,
                    "c": json.dumps(result.cited), "g": result.grounded,
                    "ms": result.elapsed_ms,
                })
            session.execute(text(f"""
                UPDATE {S}.eval_run SET
                    finished_at = now(), total = :t, passed = :p, failed = :f
                WHERE id = CAST(:run AS uuid)
            """), {
                "run": run_id, "t": len(results),
                "p": sum(1 for r in results if r.passed),
                "f": sum(1 for r in results if not r.passed),
            })
            session.commit()


def _principal(slug: str, role: str | None) -> str:
    from ..api.extra import _principal_for_role

    return _principal_for_role(slug, role)
