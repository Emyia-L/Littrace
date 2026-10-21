from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Identity,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base


class ReviewDecision(str, Enum):
    """人工对一条证据作出的决定，与 Evidence Gate 的决定分开保存。"""

    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class EvidenceReview(Base):
    """每次复核新增一行，使之后的决定不会覆盖先前记录。"""

    __tablename__ = "evidence_reviews"
    __table_args__ = (
        UniqueConstraint("review_order", name="uq_evidence_reviews_review_order"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    # 数据库分配递增序号，用来确定同一证据的复核先后。
    # UUID 仍是对外使用的记录 ID；序号不参与 URL 定位。
    review_order: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        nullable=False,
    )
    # 只保存关联 ID；引文和模型检查结果仍由 evidence_records 管理。
    evidence_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("evidence_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    decision: Mapped[ReviewDecision] = mapped_column(
        SqlEnum(
            ReviewDecision,
            name="review_decision",
            values_callable=lambda enum_type: [item.value for item in enum_type],
            validate_strings=True,
        ),
        nullable=False,
    )
    # 复核人可以解释原因；第一版允许留空。
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
