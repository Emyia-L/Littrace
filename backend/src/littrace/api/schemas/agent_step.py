from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from littrace.research.agent_actions import AgentAction


class AgentStepResponse(BaseModel):
    """向客户端展示一轮已保存的 Agent 动作与观察。"""

    # 允许 model_validate(record) 从 SQLAlchemy 的 AgentStepRecord 读取字段。
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    step_number: int = Field(ge=1, le=20)
    action_kind: Literal[
        "search", "extract_evidence", "synthesize_project", "finish"
    ]

    # 数据库中是 JSONB 字典；这里复用已有的 AgentAction，
    # 根据 kind 将它校验为具体的 SearchAction 等动作类型。
    action_payload: AgentAction

    # finish 不调用工具，因此没有观察内容，对应 JSON null。
    observation_summary: str | None
    recorded_at: datetime
