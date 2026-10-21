"""研究运行的成本、质量信号和跨运行记忆响应结构。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ModelUsageSummary(BaseModel):
    """一次运行中全部聊天模型调用的聚合用量。"""

    call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: int
    # 字符串可避免 JSON 浮点数破坏金额精度，例如 "0.001235"。
    estimated_cost_usd: str


class RunQualitySignals(BaseModel):
    """可解释的质量信号；不把不同含义的指标压成一个神秘总分。"""

    run_id: UUID
    accepted_evidence_count: int
    rejected_evidence_count: int
    gate_acceptance_rate: float | None
    human_confirmed_count: int
    human_dismissed_count: int
    human_pending_count: int
    unique_evidence_chunk_count: int
    cross_run_memory_count: int
    warnings: list[str]
    model_usage: ModelUsageSummary


class RunMemoryResponse(BaseModel):
    """本次运行实际送给规划器的一条历史记忆快照。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_evidence_id: UUID
    source_run_id: UUID
    summary: str
    created_at: datetime
