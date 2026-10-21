from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from littrace.database.models.research_run import (
    ResearchRunStatus,
)

from typing import Literal

class ResearchRunCreate(BaseModel):
    """创建一次研究运行时，客户端可以提交的数据。"""

    model_config = ConfigDict(
        # 校验前清除字符串两端的空白。
        # 例如 "  论文有哪些局限？  " 会变成 "论文有哪些局限？"。
        # 纯空格会变成空字符串，再被 min_length=1 拒绝。
        str_strip_whitespace=True,

        # 拒绝没有在本模型中声明的字段。
        # 例如把 top_k 拼成 topk，接口会指出错误，
        # 避免忽略拼写错误后继续使用默认值。
        extra="forbid",
    )

    # 本次研究要回答的问题。
    # 这里与现有 EvidenceRequest 的长度限制保持一致。
    question: str = Field(
        min_length=1,
        max_length=1000,
        description="本次研究需要回答的问题",
    )

    # 单次检索提供多少个候选文本块。
    # 后续接入动态 Agent 时，检索工具仍可使用这个参数。
    top_k: int = Field(
        default=8,
        ge=1,
        le=100,
        description="单次检索最多返回的候选文本块数量",
    )

    # 当前证据提取步骤最多生成多少张卡片。
    # 它不是 Agent 的最大运行轮数；
    # Agent 的轮次和调用预算会另行设计。
    max_cards: int = Field(
        default=3,
        ge=1,
        le=10,
        description="一次证据提取最多生成的证据卡数量",
    )

class ResearchRunResponse(BaseModel):
    """创建或查询研究运行时，返回给客户端的数据。"""

    # 只有正常结束的 Agent 运行才有值。
    agent_stop_reason: Literal["finish", "step_limit"] | None
    agent_steps_used: int | None
    model_call_count: int
    model_prompt_tokens: int
    model_completion_tokens: int
    model_total_tokens: int
    model_duration_ms: int
    estimated_cost_microusd: int

    model_config = ConfigDict(
        # 允许从 SQLAlchemy 对象的属性读取数据。
        #
        # 后续路由可以调用：
        # ResearchRunResponse.model_validate(run)
        #
        # Pydantic 会读取 run.id、run.question、run.status 等属性，
        # 并转换成符合本模型定义的响应数据。
        from_attributes=True,
    )

    # 运行自身的 ID，以及它所属的项目 ID。
    id: UUID
    project_id: UUID

    # 普通运行为空；重试运行指向它的直接来源。
    retry_of_run_id: UUID | None

    # 返回实际保存的问题和参数，方便客户端确认本次运行配置。
    question: str
    top_k: int
    max_cards: int

    # 复用数据库模型中的状态枚举，保持取值一致。
    # 转换成 JSON 时会输出 "pending"、"running" 等字符串。
    status: ResearchRunStatus

    # pending 或旧记录为 null；执行开始后为 workflow 或 agent。
    execution_mode: Literal["workflow", "agent"] | None

    # 字段允许为 None，转换成 JSON 时表示为 null。
    # 后续业务代码负责写入适合返回给用户的错误说明。
    error_message: str | None

    created_at: datetime
    updated_at: datetime

    # 等待开始时，这两个时间可以为空。
    # 执行逻辑负责在开始、完成或失败时更新它们。
    started_at: datetime | None
    finished_at: datetime | None
