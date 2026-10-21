"""把报告候选证据关联到数据库中的论文原文位置。"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.evidence import EvidenceRecord
from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import PaperChunkRecord
from littrace.domain.models import EvidenceCard
from littrace.research.report_draft import ReportEvidence


def build_report_evidence(
    session: Session,
    project_id: UUID,
    selected: Sequence[EvidenceRecord],
) -> list[ReportEvidence]:
    """为已筛选的证据查找本项目中的论文、文件名和真实页码。

    调用方负责先筛出本项目、本次运行中可进入报告的证据。
    此函数还会核对文本块归属和页码，避免错误来源进入报告。
    """
    if not selected:
        return []

    # EvidenceRecord.evidence 是 JSONB 字典，先恢复为已定义的证据卡模型。
    cards = {
        record.id: EvidenceCard.model_validate(record.evidence)
        for record in selected
    }

    # 一次查询全部文本块及所属论文，避免为每张证据单独查数据库。
    rows = session.execute(
        select(
            PaperChunkRecord.chunk_id,
            PaperChunkRecord.page_number,
            Paper.original_filename,
        )
        .join(Paper, Paper.id == PaperChunkRecord.paper_id)
        .where(
            Paper.project_id == project_id,
            PaperChunkRecord.chunk_id.in_([
                card.chunk_id for card in cards.values()
            ]),
        )
    ).all()
    sources = {row.chunk_id: row for row in rows}

    result: list[ReportEvidence] = []
    for record in selected:
        card = cards[record.id]
        source = sources.get(card.chunk_id)
        # 不能从 chunk_id 的字符串猜文件或页码，必须以数据库为准。
        if source is None or source.page_number != card.page_number:
            raise ValueError(f"Evidence source is invalid: {record.id}")

        result.append(ReportEvidence(
            evidence_id=record.id,
            claim=card.claim,
            quote=card.quote,
            original_filename=source.original_filename,
            page_number=source.page_number,
        ))

    # 沿用候选证据的顺序，供后续 Markdown 排版函数使用。
    return result
