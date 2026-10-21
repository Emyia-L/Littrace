from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    func,
)
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PostgresUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base

class EvidenceDecision(str, Enum):
    """证据卡经过检查后的处理决定。"""

    # 引文定位检查通过，且论断支持检查为 supported。
    ACCEPTED = "accepted"

    # 引文定位失败，或论断支持检查为 unsupported / uncertain。
    REJECTED = "rejected"

class EvidenceRecord(Base):
    """保存一张证据卡及其检查结果。"""

    __tablename__ = "evidence_records"

    # 一张卡片对应一条独立记录。
    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # 通过 run_id 找到本次研究的问题、参数和所属项目。
    #
    # 同一次运行可以保存多张卡片，所以这里不设置 unique。
    # index=True 加快“查询某次运行全部证据”的操作。
    #
    # 删除运行记录时，同时删除其证据记录。
    run_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    # 记录 Gate 的最终处理决定。
    #
    # 不设置默认值，要求写入代码明确提供接受或拒绝结果。
    # 这个字段表示检查决定，不表示论文结论已被独立证实。
    decision: Mapped[EvidenceDecision] = mapped_column(
        SqlEnum(
            EvidenceDecision,
            name="evidence_decision",
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            validate_strings=True,
        ),
        nullable=False,
    )
    # 保存完整 EvidenceCard：
    # question、claim、relation、quote、page_number、
    # chunk_id 和 limitations。
    #
    # 后续将使用 evidence_card.model_dump(mode="json")
    # 转换成可以写入 JSONB 的字典。
    #
    # Any 表示字典中的值可以有不同类型：
    # 字符串、数字、列表等。
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True),
        nullable=False,
    )
    # 保存 CitationCheck，例如：
    # {"valid": False, "reason": "quote_not_found"}
    #
    # 每张进入本表的卡片都应完成引文检查，
    # 因此该字段不允许为空。
    citation_check: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True),
        nullable=False,
    )

    # 保存 ClaimSupportCheck，例如：
    # {
    #     "verdict": "unsupported",
    #     "reason": "引用没有提供准确率提升幅度。"
    # }
    #
    # 引文检查失败时，不再调用论断检查器，
    # 此时用数据库 NULL 表示该检查没有执行。
    claim_support_check: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )

    # 保存当次检查结果的创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )