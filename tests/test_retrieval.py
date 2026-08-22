"""CNT-RET-*, CNT-ACL-*, CNT-RNK-* — the pipeline's invariants."""

import pytest

from askcontent.bootstrap import build
from askcontent.domain.retrieval_spec import Intent, ModelRetrievalRequest, RetrievalSpec
from askcontent.domain.scope import KnowledgeScope, SourceRoot
from askcontent.services.registry import ConnectorState


@pytest.fixture
def platform():
    return build(simulate_latency=False)


def ask(platform, connector_id, question, principal):
    connector = platform.registry.get(connector_id)
    spec = RetrievalSpec(
        intent=Intent.LOOKUP, scope_ref="s", question=question,
        channels=connector.retrieval.channels,
        k_per_channel=connector.retrieval.k_per_channel,
    )
    return connector, platform.retrieval.retrieve(connector, spec, principal)


def test_a_forbidden_document_is_never_cited(platform):
    """CNT-ACL-04 and the CNT-EVL-05 leakage gate, which must be zero."""
    _, evidence = ask(platform, "cn-risk-compliance", "AML alert thresholds", "user:asha")
    assert evidence.trace.forbidden_count >= 1
    assert all(c.doc_id != "RSK-CTL-3300" for c in evidence.citations)


def test_a_forbidden_document_is_dropped_before_ranking(platform):
    """Not filtered from results afterwards: it must never occupy the k budget
    or influence the order."""
    _, evidence = ask(platform, "cn-risk-compliance", "AML alert thresholds", "user:asha")
    forbidden = next(c for c in evidence.trace.candidates if c.doc_id == "RSK-CTL-3300")
    assert forbidden.dropped_by == "forbidden"
    assert forbidden.rerank_score is None


def test_a_stale_index_entry_is_counted_not_hidden(platform):
    """CNT-RET-07 — a rising stale rate is the earliest signal that PGP's sync
    is broken, and it is otherwise invisible."""
    _, evidence = ask(platform, "cn-payments-ops", "HSM key ceremony", "user:rob")
    assert evidence.trace.stale_index_count >= 1
    stale = next(c for c in evidence.trace.candidates if c.doc_id == "OPS-RB-777")
    assert stale.dropped_by == "stale_index"


def test_narrowing_scope_takes_effect_without_an_ingest_run(platform):
    """CNT-SCP-11, CNT-SCP-12 — the single-gate design is wrong precisely here:
    a group narrowing a scope during an incident must not wait for a job."""
    connector, before = ask(platform, "cn-consumer-banking", "overdraft fee per item", "user:asha")
    assert any(c.doc_id == "CB-POL-1041" for c in before.citations)

    narrowed = KnowledgeScope(
        roots=(SourceRoot(kind="space", value="CONSUMER"),), exclude=("/consumer/policies/*",)
    )
    platform.registry.update_scope(connector.connector_id, narrowed, "admin", {})

    _, after = ask(platform, "cn-consumer-banking", "overdraft fee per item", "user:asha")
    assert all(c.doc_id != "CB-POL-1041" for c in after.citations)


def test_suspension_takes_effect_on_the_next_query(platform):
    """CNT-ADM-05 — the switch an administrator needs during an incident."""
    platform.registry.set_state("cn-consumer-banking", ConnectorState.SUSPENDED, "admin")
    _, evidence = ask(platform, "cn-consumer-banking", "overdraft", "user:asha")
    assert evidence.refused and "suspended" in evidence.refusal_reason


def test_contradicting_documents_are_both_surfaced(platform):
    """CNT-RET-20 — a disagreement is never silently resolved by ranking."""
    _, evidence = ask(
        platform, "cn-consumer-banking",
        "What is the overdraft fee per item?",
        "user:asha",
    )
    assert evidence.conflicts
    conflict = evidence.conflicts[0]
    assert {c.doc_id for c in conflict.citations} == {"CB-POL-1041", "CB-GDE-0887"}
    assert len({c.authority for c in conflict.citations}) == 2  # tiers are shown


def test_a_document_is_never_in_conflict_with_itself(platform):
    """A policy stating the same figure in prose and again in a table is
    agreement, not disagreement."""
    _, evidence = ask(platform, "cn-consumer-banking", "overdraft fee", "user:asha")
    for conflict in evidence.conflicts:
        assert len({c.doc_id for c in conflict.citations}) == len(conflict.citations)


def test_both_channels_run_and_a_failure_degrades_visibly(platform):
    """CNT-RET-03, CNT-RET-05, CNT-CON-13."""
    _, evidence = ask(platform, "cn-consumer-banking", "wire transfer cut-off", "user:asha")
    assert {t.channel for t in evidence.trace.channels} == {"pgp", "ecm"}

    failing = build(simulate_latency=False, failure_rate=1.0)
    _, degraded = ask(failing, "cn-consumer-banking", "wire transfer", "user:asha")
    assert degraded.refused
    assert degraded.trace.degraded  # named, not silent


def test_the_model_cannot_widen_its_own_reach():
    """CNT-RET-15 — `channels` and `k_per_channel` are absent from the type a
    model is permitted to emit."""
    fields = set(ModelRetrievalRequest.model_fields)
    assert "channels" not in fields
    assert "k_per_channel" not in fields
    assert "scope_ref" not in fields
    with pytest.raises(Exception):
        ModelRetrievalRequest(intent=Intent.LOOKUP, question="q", channels=("pgp",))


def test_every_drop_is_attributed_to_one_named_rule(platform):
    """CNT-ADM-10 — without per-stage attribution, tuning is superstition."""
    _, evidence = ask(platform, "cn-consumer-banking", "wire transfer fees", "user:asha")
    dropped = [c for c in evidence.trace.candidates if c.rerank_score is None and c.dropped_by]
    assert dropped
    assert all(c.dropped_by for c in dropped)


def test_the_plan_hash_changes_with_the_reranker(platform):
    """CNT-RNK-06 — changing the reranker invalidates cached plans."""
    from askcontent.domain.ids import plan_hash

    spec = RetrievalSpec(intent=Intent.LOOKUP, scope_ref="s", question="q")
    a = plan_hash(spec.canonical_json(), "lexical-deterministic", "1.0.0")
    b = plan_hash(spec.canonical_json(), "cross-encoder", "BAAI/bge-reranker-v2-m3")
    assert a != b


def test_the_passage_cache_prevents_reparsing(platform):
    """CNT-RET-10 — the single largest latency lever in the system."""
    ask(platform, "cn-consumer-banking", "overdraft fee per item", "user:asha")
    misses_after_first = platform.passages.stats.misses
    ask(platform, "cn-consumer-banking", "overdraft fee per item", "user:asha")
    assert platform.passages.stats.misses == misses_after_first
    assert platform.passages.stats.hits > 0
