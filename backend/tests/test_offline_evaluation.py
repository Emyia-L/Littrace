"""评测本身也必须可测试：防止把“未记录”错误地算成 0 分。"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from littrace.evaluation.offline import (
    EvalCase,
    EvaluatedEvidence,
    EvaluatedStep,
    EvalRun,
    GoldQuote,
    ObservedChunk,
    evaluate_run,
)
from littrace.evaluation.from_database import load_eval_run


PAPER_HASH = "a" * 64
GOLD_TEXT = "OmniAgent dynamically modulates auditory and visual attention"
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def make_case(with_gold: bool = True) -> EvalCase:
    return EvalCase(
        case_id="omni-01",
        question="论文解决什么问题？",
        paper_sha256=[PAPER_HASH],
        gold_quotes=[GoldQuote(
            paper_sha256=PAPER_HASH, page_number=3, quote=GOLD_TEXT,
        )] if with_gold else [],
    )


def make_run(**changes) -> EvalRun:
    data = dict(
        run_id=RUN_ID,
        execution_mode="agent",
        status="completed",
        agent_stop_reason="finish",
        model_call_count=4,
        model_total_tokens=1200,
        model_duration_ms=900,
        elapsed_ms=1500,
        estimated_cost_microusd=50,
    )
    data.update(changes)
    return EvalRun(**data)


def test_agent_retrieval_and_accepted_evidence_are_scored_separately():
    run = make_run(
        observed_chunks=[ObservedChunk(
            paper_sha256=PAPER_HASH, page_number=3,
            text=f"The paper states: {GOLD_TEXT} in its method section.",
        )],
        evidence=[EvaluatedEvidence(
            decision="accepted", paper_sha256=PAPER_HASH, page_number=3,
            quote=f"The paper states: {GOLD_TEXT} in its method section.",
            citation_valid=True, support_verdict="supported",
            source_text=f"The paper states: {GOLD_TEXT} in its method section.",
        )],
    )

    result = evaluate_run(make_case(), run)

    assert result.observed_gold_recall == 1.0
    assert result.accepted_gold_recall == 1.0
    assert result.citation_invariant_violations == 0
    assert result.model_total_tokens == 1200


def test_search_can_find_gold_without_producing_gold_evidence():
    result = evaluate_run(make_case(), make_run(
        observed_chunks=[ObservedChunk(
            paper_sha256=PAPER_HASH, page_number=3, text=GOLD_TEXT,
        )],
        steps=[EvaluatedStep(kind="search", skipped_duplicate_search=True)],
    ))

    assert result.observed_gold_recall == 1.0
    assert result.accepted_gold_recall == 0.0
    assert result.duplicate_search_count == 1


def test_workflow_has_no_observed_search_score_and_empty_gold_has_no_recall():
    workflow = evaluate_run(make_case(), make_run(execution_mode="workflow"))
    no_gold = evaluate_run(make_case(with_gold=False), make_run())

    assert workflow.observed_gold_recall is None
    assert workflow.accepted_gold_recall == 0.0
    assert no_gold.observed_gold_recall is None
    assert no_gold.accepted_gold_recall is None


@pytest.mark.parametrize("change", [
    {"citation_valid": False},
    {"support_verdict": "unsupported"},
    {"source_text": None},
    {"source_text": "An unrelated passage"},
])
def test_accepting_evidence_that_breaks_gate_invariants_is_flagged(change):
    evidence = dict(
        decision="accepted", paper_sha256=PAPER_HASH, page_number=3,
        quote=GOLD_TEXT, citation_valid=True, support_verdict="supported",
        source_text=GOLD_TEXT,
    )
    evidence.update(change)
    result = evaluate_run(make_case(), make_run(
        evidence=[EvaluatedEvidence(**evidence)],
    ))

    assert result.citation_invariant_violations == 1
    assert result.accepted_gold_recall == 0.0


def test_case_rejects_gold_from_another_corpus():
    with pytest.raises(ValidationError, match="gold quote must belong"):
        EvalCase(
            case_id="bad", question="q", paper_sha256=[PAPER_HASH],
            gold_quotes=[GoldQuote(
                paper_sha256="b" * 64, page_number=1, quote=GOLD_TEXT,
            )],
        )


def test_database_adapter_reads_existing_steps_and_evidence_without_running_agent():
    paper_id = UUID("00000000-0000-0000-0000-000000000002")
    project_id = UUID("00000000-0000-0000-0000-000000000003")
    chunk_id = "paper-p3-c1"
    now = datetime.now(UTC)
    run = SimpleNamespace(
        id=RUN_ID, project_id=project_id, question=make_case().question,
        execution_mode="agent", status=SimpleNamespace(value="completed"),
        agent_stop_reason="finish", model_call_count=2, model_total_tokens=300,
        model_duration_ms=100, estimated_cost_microusd=5,
        started_at=now, finished_at=now + timedelta(seconds=2),
    )
    paper = SimpleNamespace(id=paper_id, content_hash=PAPER_HASH)
    chunk = SimpleNamespace(
        chunk_id=chunk_id, paper_id=paper_id, page_number=3, text=GOLD_TEXT,
    )
    step = SimpleNamespace(
        action_kind="search",
        observation_summary=json.dumps({
            "matches": [{"chunk_id": chunk_id}], "returned_count": 1,
        }),
    )
    record = SimpleNamespace(
        decision=SimpleNamespace(value="accepted"),
        evidence={
            "question": make_case().question, "claim": "A claim",
            "relation": "supports", "quote": GOLD_TEXT,
            "page_number": 3, "chunk_id": chunk_id, "limitations": [],
        },
        citation_check={"valid": True},
        claim_support_check={"verdict": "supported", "reason": "quote supports claim"},
    )

    session = MagicMock()
    session.get.return_value = run
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [paper]),
        SimpleNamespace(all=lambda: [step]),
        SimpleNamespace(all=lambda: [record]),
        SimpleNamespace(all=lambda: [chunk]),
    ]

    snapshot = load_eval_run(session, make_case(), RUN_ID)
    result = evaluate_run(make_case(), snapshot)

    assert result.observed_gold_recall == 1.0
    assert result.accepted_gold_recall == 1.0
    assert result.elapsed_ms == 2000
    assert session.scalars.call_count == 4
