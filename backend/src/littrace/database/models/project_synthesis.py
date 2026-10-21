"""跨论文自动比较与综合的持久化模型。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base


class ProjectSynthesis(Base):
    """保存一次有证据约束的项目级综合及其输入快照。"""

    __tablename__ = "project_syntheses"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 由 Agent 工具创建时记录触发它的研究运行；人工按钮创建时为空。
    triggered_by_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    # source_snapshot 保存当时参与综合的论断、引文、论文及页码。
    # 即使后续人工意见改变，也仍能解释本次模型看到了什么。
    source_snapshot: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    # result 同时保留通过和未通过二次支持检查的比较点。
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    model_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    model_completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    model_total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    model_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
