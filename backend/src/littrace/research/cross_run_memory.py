"""从同项目的历史人工确认结果构造可追溯、有限长度的 Agent 记忆。"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.evidence import EvidenceDecision, EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview, ReviewDecision
from littrace.database.models.research_run import ResearchRun, ResearchRunStatus
from littrace.database.models.run_memory import ResearchRunMemory
from littrace.domain.models import EvidenceCard

MAX_MEMORY_ITEMS = 6
MAX_MEMORY_SUMMARY_CHARS = 700


@dataclass(frozen=True)
class CrossRunMemoryItem:
    source_evidence_id: UUID
    source_run_id: UUID
    summary: str


def load_cross_run_memory(
    *, session: Session, project_id: UUID, current_run_id: UUID,
    limit: int = MAX_MEMORY_ITEMS,
) -> list[CrossRunMemoryItem]:
    """读取过往运行中 Gate 接受且最新人工决定为确认的证据。"""

    if not 1 <= limit <= 20:
        raise ValueError("memory limit must be between 1 and 20")

    rows = session.execute(
        select(EvidenceRecord, EvidenceReview, ResearchRun)
        .join(ResearchRun, ResearchRun.id == EvidenceRecord.run_id)
        .join(EvidenceReview, EvidenceReview.evidence_id == EvidenceRecord.id)
        .where(
            ResearchRun.project_id == project_id,
            ResearchRun.id != current_run_id,
            ResearchRun.status == ResearchRunStatus.COMPLETED,
            EvidenceRecord.decision == EvidenceDecision.ACCEPTED,
        )
        .order_by(EvidenceReview.review_order.desc())
    ).all()

    # 每条证据可能有多次复核；按 review_order 倒序后的第一条才是当前决定。
    seen: set[UUID] = set()
    memories: list[CrossRunMemoryItem] = []
    for evidence, review, source_run in rows:
        if evidence.id in seen:
            continue
        seen.add(evidence.id)
        if review.decision != ReviewDecision.CONFIRMED:
            continue

        card = EvidenceCard.model_validate(evidence.evidence)
        summary = (
            f"历史问题：{source_run.question}\n"
            f"已确认论断：{card.claim}\n"
            f"原文引文：{card.quote[:320]}\n"
            f"来源块：{card.chunk_id}，第 {card.page_number} 页"
        )[:MAX_MEMORY_SUMMARY_CHARS]
        memories.append(CrossRunMemoryItem(
            source_evidence_id=evidence.id,
            source_run_id=source_run.id,
            summary=summary,
        ))
        if len(memories) >= limit:
            break
    return memories


def save_cross_run_memory(
    *, session: Session, run_id: UUID, items: list[CrossRunMemoryItem]
) -> None:
    """保存本次实际采用的记忆快照；由上层事务决定何时提交。"""

    session.add_all([
        ResearchRunMemory(
            run_id=run_id,
            source_evidence_id=item.source_evidence_id,
            summary=item.summary,
        )
        for item in items
    ])
    session.flush()


def format_memory_context(items: list[CrossRunMemoryItem]) -> str:
    """生成给规划器的只读上下文；历史证据不计入本次 Gate 结果。"""

    if not items:
        return "No confirmed evidence from earlier runs is available."
    return "\n\n".join(
        f"Memory {number} (source run {item.source_run_id}):\n{item.summary}"
        for number, item in enumerate(items, start=1)
    )
