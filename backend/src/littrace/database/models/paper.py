#论文表
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import (
    UUID as PostgresUUID,
)
from littrace.database.models.paper_chunk import (
    PaperChunkRecord,
)
from littrace.ingestion.chunker import chunk_pages
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base


class PaperStatus(str, Enum):
    """
    论文在导入流程中的状态。

    uploaded：文件已经保存，但尚未开始解析。
    parsing：正在提取 PDF 文本。
    parsed：PDF 已经成功解析。
    failed：PDF 解析失败。
    """

    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"


class Paper(Base):
    """
    上传到某个研究项目中的论文。

    当前只保存文件信息和解析状态。
    DOI、作者、摘要等元数据将在后续阶段增加。
    """

    __tablename__ = "papers"

    # 同一个项目不能重复导入内容完全相同的文件。
    #
    # content_hash 是文件内容的 SHA-256，
    # 因此即使文件名不同，只要内容相同也能识别出来。
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "content_hash",
            name="uq_papers_project_content_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # 每篇论文必须属于一个研究项目。
    #
    # ondelete="CASCADE" 表示将来删除项目时，
    # PostgreSQL 会同时删除该项目的论文记录。
    project_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "research_projects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 用户上传时的原始文件名，用于页面显示。
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # 文件在本地存储目录中的相对位置。
    #
    # 数据库不保存 D:\... 这样的绝对路径，
    # 否则项目换一台电脑后路径就会失效。
    storage_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # PDF 原始字节内容的 SHA-256 哈希。
    # SHA-256 的十六进制结果固定为 64 个字符。
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[PaperStatus] = mapped_column(
        SqlEnum(
            PaperStatus,
            name="paper_status",
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
        ),
        default=PaperStatus.UPLOADED,
        nullable=False,
    )

    # 上传完成但尚未解析时，页数还不知道，
    # 因此允许暂时为 NULL。
    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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