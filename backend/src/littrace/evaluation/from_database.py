"""将现有研究运行转换为离线评测快照；只读，不触发 Agent。"""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.agent_step import AgentStepRecord
from littrace.database.models.evidence import EvidenceRecord
from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import PaperChunkRecord
from littrace.database.models.research_run import ResearchRun
from littrace.domain.models import EvidenceCard
from littrace.evaluation.offline import (
    EvalCase,
    EvaluatedEvidence,
    EvaluatedStep,
    EvalRun,
    ObservedChunk,
    normalize_quote,
)


def load_eval_run(session: Session, case: EvalCase, run_id: UUID) -> EvalRun:
    """按 run_id 读取轨迹和证据，并验证运行确实针对标注的论文集合。"""

    run = session.get(ResearchRun, run_id)
    if run is None:
        raise ValueError(f"Research run {run_id} does not exist")
    if run.execution_mode not in {"workflow", "agent"}:
        raise ValueError("Run has not selected workflow or agent mode")
    if normalize_quote(run.question) != normalize_quote(case.question):
        raise ValueError("Run question differs from evaluation case")

    # 同一个问题若在不同论文集合上运行，分数不可直接比较。
    papers = session.scalars(select(Paper).where(Paper.project_id == run.project_id)).all()
    if {paper.content_hash for paper in papers} != set(case.paper_sha256):
        raise ValueError("Project PDF hashes differ from evaluation case corpus")
    paper_hash_by_id = {paper.id: paper.content_hash for paper in papers}

    steps = session.scalars(
        select(AgentStepRecord)
        .where(AgentStepRecord.run_id == run_id)
        .order_by(AgentStepRecord.step_number)
    ).all()
    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id == run_id)
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()

    # 搜索观察只记录 ID；从原有文本块表取完整正文，才能检查 gold 引文。
    observed_ids: set[str] = set()
    evaluated_steps: list[EvaluatedStep] = []
    for step in steps:
        observation = json.loads(step.observation_summary) if step.observation_summary else {}
        if step.action_kind == "search":
            observed_ids.update(match["chunk_id"] for match in observation.get("matches", []))
        evaluated_steps.append(EvaluatedStep(
            kind=step.action_kind,
            skipped_duplicate_search=(
                step.action_kind == "search"
                and observation.get("reason") == "identical_search_already_executed"
            ),
            empty_search=(
                step.action_kind == "search"
                and observation.get("returned_count") == 0
                and not observation.get("skipped", False)
            ),
        ))

    cards = [(record, EvidenceCard.model_validate(record.evidence)) for record in records]
    needed_ids = observed_ids | {card.chunk_id for _, card in cards}
    chunks = session.scalars(
        select(PaperChunkRecord).where(PaperChunkRecord.chunk_id.in_(needed_ids))
    ).all() if needed_ids else []
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}

    observed_chunks = [
        ObservedChunk(
            paper_sha256=paper_hash_by_id[chunk.paper_id],
            page_number=chunk.page_number,
            text=chunk.text,
        )
        for chunk_id in sorted(observed_ids)
        if (chunk := chunk_by_id.get(chunk_id)) is not None
        and chunk.paper_id in paper_hash_by_id
    ]

    evidence = []
    for record, card in cards:
        chunk = chunk_by_id.get(card.chunk_id)
        # 仅当前项目的块可用于给证据定位；其他项目的块一律视为来源缺失。
        valid_chunk = chunk if chunk is not None and chunk.paper_id in paper_hash_by_id else None
        check = record.claim_support_check or {}
        evidence.append(EvaluatedEvidence(
            decision=record.decision.value,
            paper_sha256=paper_hash_by_id[valid_chunk.paper_id] if valid_chunk else None,
            page_number=card.page_number,
            quote=card.quote,
            citation_valid=bool(record.citation_check.get("valid")),
            support_verdict=check.get("verdict"),
            source_text=(
                valid_chunk.text
                if valid_chunk is not None and valid_chunk.page_number == card.page_number
                else None
            ),
        ))

    elapsed_ms = None
    if run.started_at is not None and run.finished_at is not None:
        elapsed_ms = max(0, round((run.finished_at - run.started_at).total_seconds() * 1000))

    return EvalRun(
        run_id=run.id,
        execution_mode=run.execution_mode,
        status=run.status.value,
        agent_stop_reason=run.agent_stop_reason,
        model_call_count=run.model_call_count,
        model_total_tokens=run.model_total_tokens,
        model_duration_ms=run.model_duration_ms,
        elapsed_ms=elapsed_ms,
        estimated_cost_microusd=run.estimated_cost_microusd,
        observed_chunks=observed_chunks,
        evidence=evidence,
        steps=evaluated_steps,
    )
