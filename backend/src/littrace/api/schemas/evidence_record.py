from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from littrace.database.models.evidence import EvidenceDecision
from littrace.domain.models import (
    CitationCheck,
    ClaimSupportCheck,
    EvidenceCard,
)

class EvidenceRecordResponse(BaseModel):
    """向客户端返回一条已保存的证据及其检查结果。"""

    # 允许 model_validate(record) 从 SQLAlchemy 对象读取字段。
    # record.evidence 等 JSONB 字典会继续转换为下方的 Pydantic 类型。
    model_config = ConfigDict(from_attributes=True)

    # id 标识这条证据记录；run_id 标识它属于哪次研究运行。
    id: UUID
    run_id: UUID

    # accepted 或 rejected，沿用数据库模型中的枚举。
    decision: EvidenceDecision

    # 复用已有领域模型，避免在 API 层重新定义证据卡结构。
    evidence: EvidenceCard
    citation_check: CitationCheck

    # 如果引文定位检查未通过，论断支持检查不会执行，
    # 数据库中此字段为 NULL，接口中对应 JSON null。
    claim_support_check: ClaimSupportCheck | None

    created_at: datetime