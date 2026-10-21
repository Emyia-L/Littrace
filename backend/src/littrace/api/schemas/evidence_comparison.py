from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from littrace.database.models.evidence_comparison import ComparisonDecision


class ComparisonCandidateResponse(BaseModel):
    """可供人工跨论文比较的一条 Gate 接受且已确认的证据。"""

    evidence_id: UUID
    run_id: UUID
    question: str
    relation: str
    claim: str
    quote: str
    paper_id: UUID
    original_filename: str
    page_number: int


class EvidenceComparisonCreate(BaseModel):
    """提交两条证据之间的人工比较决定。"""

    left_evidence_id: UUID
    right_evidence_id: UUID
    decision: ComparisonDecision
    note: str | None = Field(default=None, max_length=2000)


class EvidenceComparisonResponse(BaseModel):
    """返回已持久化的人工比较记录。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_order: int
    project_id: UUID
    left_evidence_id: UUID
    right_evidence_id: UUID
    decision: ComparisonDecision
    note: str | None
    created_at: datetime
