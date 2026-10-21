"""记录一次 Agent 运行实际使用过的跨运行记忆来源。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base


class ResearchRunMemory(Base):
    """把当前运行与一条过往人工确认的证据关联起来。"""

    __tablename__ = "research_run_memories"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "source_evidence_id",
            name="uq_research_run_memories_run_evidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_evidence_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("evidence_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 保存当时送给规划器的有界摘要；原文仍以 evidence_records 为准。
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
