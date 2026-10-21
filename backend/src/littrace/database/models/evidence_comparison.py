from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Identity,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base


class ComparisonDecision(str, Enum):
    """人工对两篇论文观点关系作出的项目级判断。"""

    MUTUALLY_SUPPORT = "mutually_support"
    CONFLICTING = "conflicting"
    INCONCLUSIVE = "inconclusive"


class EvidenceComparisonReview(Base):
    """保存一对已确认论断的人工比较决定；每次修改都追加新记录。"""

    __tablename__ = "evidence_comparison_reviews"
    __table_args__ = (
        CheckConstraint(
            "left_evidence_id <> right_evidence_id",
            name="ck_comparison_distinct_evidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("research_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    left_evidence_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("evidence_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    right_evidence_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("evidence_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 用递增序号判断同一证据对的最近一次人工比较，保留完整修改历史。
    review_order: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), nullable=False, unique=True,
    )
    decision: Mapped[ComparisonDecision] = mapped_column(
        SqlEnum(
            ComparisonDecision,
            name="comparison_decision",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        ),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
