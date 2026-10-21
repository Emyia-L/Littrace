from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from littrace.database.models.evidence_review import ReviewDecision


class EvidenceReviewCreate(BaseModel):
    """客户端提交的一次人工复核决定。"""

    decision: ReviewDecision
    # 说明可以为空；限制长度，避免一次提交写入过大的文本。
    note: str | None = Field(default=None, max_length=2000)


class EvidenceReviewResponse(BaseModel):
    """数据库保存后返回的复核记录。"""

    # 允许 model_validate() 从 SQLAlchemy 对象读取属性。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_order: int
    evidence_id: UUID
    decision: ReviewDecision
    note: str | None
    created_at: datetime
