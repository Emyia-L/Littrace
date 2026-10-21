from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base


class AgentStepRecord(Base):
    """保存一次研究运行中的一轮 Agent 动作及其观察。"""

    __tablename__ = "agent_steps"

    __table_args__ = (
        CheckConstraint(
            "step_number BETWEEN 1 AND 20",
            name="ck_agent_steps_number_range",
        ),
        CheckConstraint(
            "action_kind IN ('search', 'extract_evidence', 'synthesize_project', 'finish')",
            name="ck_agent_steps_action_kind",
        ),
        CheckConstraint(
            "observation_summary IS NULL OR "
            "char_length(observation_summary) <= 5000",
            name="ck_agent_steps_observation_length",
        ),
        # 同一次运行的第 N 步只能保存一次。
        # 该唯一约束形成的索引也可用于按 run_id、step_number 查询，
        # 因此这里不再为 run_id 单独建一个重复索引。
        UniqueConstraint(
            "run_id",
            "step_number",
            name="uq_agent_steps_run_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # 通过研究运行间接归属某个项目；删除运行时一并删除轨迹。
    run_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # 下一步会由已校验的 Pydantic action.model_dump(mode="json") 生成。
    # 不直接存未经校验的模型原始回复。
    action_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True),
        nullable=False,
    )

    # search / extract_evidence 的工具观察；finish 没有工具观察。
    observation_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 这是“保存到数据库的时间”，暂不冒充工具实际执行时间。
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
