from datetime import datetime
from uuid import UUID
from pgvector.sqlalchemy import Vector

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from sqlalchemy.dialects.postgresql import (
    TSVECTOR,
    UUID as PostgresUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base

class PaperChunkRecord(Base):
    """
    持久化到 PostgreSQL 的论文文本块。

    名称使用 PaperChunkRecord，是为了和 domain 中的
    Pydantic 模型 PaperChunk 区分：

    PaperChunk：
        负责程序内部的数据校验和传递。

    PaperChunkRecord：
        负责数据库表映射和持久化。
    """

    __tablename__ = "paper_chunks"

    __table_args__ = (
        # 同一篇论文、同一页中的块序号不能重复。
        UniqueConstraint(
            "paper_id",
            "page_number",
            "chunk_index",
            name=(
                "uq_paper_chunks_"
                "paper_page_index"
            ),
        ),
        # 除了 Pydantic 校验之外，再由数据库保证
        # 页码和块序号不可能小于 1。
        # 除了 Pydantic 校验之外，再由数据库保证
        # 页码和块序号不可能小于 1。
        CheckConstraint(
            "page_number >= 1",
            name="ck_paper_chunks_page_positive",
        ),
        CheckConstraint(
            "chunk_index >= 1",
            name="ck_paper_chunks_index_positive",
        ),

        # 给搜索字段建立 GIN 索引。
        #
        # 可以把它理解成数据库维护的一份倒排目录：
        # 某个词项出现在哪些文本块中。
        #
        # PostgreSQL 可以据此筛选匹配记录，
        # 而不是每次都逐条处理所有文本块。
        Index(
            "ix_paper_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),

    )
    # 继续使用 Day 1 已建立的可读 chunk ID：
    #
    # {paper_uuid}-p{page_number}-c{chunk_index}
    #
    # 后续 Evidence Card 会直接引用这个主键。
    chunk_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    # 论文被删除时，其文本块也应由数据库自动删除。
    paper_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "papers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Text 没有固定长度限制，适合保存论文原文。
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # PostgreSQL 根据 text 自动生成搜索词项。
    #
    # 业务代码上传文本块时只需要填写 text，
    # 不需要手工填写 search_vector。
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            # 这里是 SQL 表达式，不是 Python 函数调用。
            #
            # to_tsvector() 把正文转换成可搜索的词项。
            # 明确指定 english，保证生成规则不受
            # 数据库默认语言配置变化的影响。
            "to_tsvector('english'::regconfig, text)",

            # persisted=True 表示生成结果存入数据库。
            # 查询时可以直接使用，无需每次重新转换正文。
            persisted=True,
        ),
        nullable=False,
    )

    # 保存当前文本块的语义向量，之后可用于按“含义相近”检索。
    #
    # Vector(...) 中的数字必须与你刚才测试打印的“向量维度”完全一致。
    # 例如，测试输出 1024 就写 Vector(1024)；如果输出是其他数字，
    # 请使用实际数字。
    #
    # 暂时允许为空：数据库里已经有旧文本块，它们还没有对应向量。
    # 后续会逐步为旧数据补向量。
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024),  # 把 1024 替换为你测试得到的实际维度（若不同）
        nullable=True,
    )

    # 当前文本块规范化后的 SHA-256。
    #
    # 它不设置 unique，因为论文中的页眉、页脚等内容
    # 可能在多个块中重复出现。
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # 文本块被视为不可变数据。
    # 如果以后修改切块策略，采用删除后重新生成，
    # 因此当前只需要 created_at，不需要 updated_at。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
