#研究项目表
from datetime import datetime
from enum import Enum
from uuid import uuid4, UUID

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    String,
    Text,
    func,
)

from sqlalchemy.dialects.postgresql import (
    UUID as PostgresUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base

class ProjectStatus(str, Enum):
    """
    项目状态枚举类，表示研究项目的不同状态。
    """
    DRAFT = "draft"  # 草稿状态
    PAPERS_UPLOADED = "papers_uploaded"
    READY_FOR_SCREENING = "ready_for_screening"

class ResearchProject(Base):
    """
    一个文献研究项目。

    每个项目包含一个研究问题，
    后续可以关联多篇论文。
    """

    __tablename__ = "research_projects"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    research_question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[ProjectStatus] = mapped_column(
        SqlEnum(
            ProjectStatus,
            name="project_status",
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
        ),
        default=ProjectStatus.DRAFT,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

"""
research_projects
├─ id
├─ title
├─ research_question
├─ status
├─ created_at
└─ updated_at
"""