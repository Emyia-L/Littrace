"""为项目级人工跨论文比较提供已确认的证据候选。"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.evidence import EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview
from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import PaperChunkRecord
from littrace.database.models.research_run import ResearchRun, ResearchRunStatus
from littrace.domain.models import EvidenceCard
from littrace.research.review_selection import select_report_candidates


@dataclass(frozen=True)
class ConfirmedProjectEvidence:
    """人工比较界面需要展示的一条证据及其已核验来源。"""

    evidence_id: UUID
    run_id: UUID
    question: str
    relation: str
    claim: str
    quote: str
    paper_id: UUID
    original_filename: str
    page_number: int


def list_confirmed_project_evidence(
    session: Session,
    project_id: UUID,
) -> list[ConfirmedProjectEvidence]:
    """限定项目与已完成运行，复用人工报告候选筛选规则，并验证原文页码。"""
    runs = session.scalars(
        select(ResearchRun)
        .where(
            ResearchRun.project_id == project_id,
            ResearchRun.status == ResearchRunStatus.COMPLETED,
        )
        .order_by(ResearchRun.created_at, ResearchRun.id)
    ).all()
    run_by_id = {run.id: run for run in runs}
    if not run_by_id:
        return []

    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id.in_(run_by_id.keys()))
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()
    if not records:
        return []

    reviews = session.scalars(
        select(EvidenceReview).where(
            EvidenceReview.evidence_id.in_([record.id for record in records])
        )
    ).all()
    selected = select_report_candidates(records, reviews)
    if not selected:
        return []

    # Resolve file, paper, and page from persisted chunks rather than trusting a model citation.
    cards = {
        record.id: EvidenceCard.model_validate(record.evidence)
        for record in selected
    }
    rows = session.execute(
        select(
            PaperChunkRecord.chunk_id,
            PaperChunkRecord.page_number,
            Paper.id.label("paper_id"),
            Paper.original_filename,
        )
        .join(Paper, Paper.id == PaperChunkRecord.paper_id)
        .where(
            Paper.project_id == project_id,
            PaperChunkRecord.chunk_id.in_([card.chunk_id for card in cards.values()]),
        )
    ).all()
    source_by_chunk = {row.chunk_id: row for row in rows}

    result: list[ConfirmedProjectEvidence] = []
    for record in selected:
        card = cards[record.id]
        source = source_by_chunk.get(card.chunk_id)
        if source is None or source.page_number != card.page_number:
            # Fail closed: questionable source locations must not be offered for comparison.
            continue
        result.append(ConfirmedProjectEvidence(
            evidence_id=record.id,
            run_id=record.run_id,
            question=run_by_id[record.run_id].question,
            relation=card.relation,
            claim=card.claim,
            quote=card.quote,
            paper_id=source.paper_id,
            original_filename=source.original_filename,
            page_number=source.page_number,
        ))
    return result
