from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import (
    PaperChunkRecord,
)
from littrace.domain.models import (
    ChunkSearchResult,
    PaperChunk,
)

def search_chunks_by_full_text(
        *,
        session: Session,
        project_id: UUID,
        query_text: str,
        top_k: int = 8,
) -> list[ChunkSearchResult]:
    """
    在指定研究项目中搜索相关文本块。

    Args:
        session: 调用方提供的数据库会话。
        project_id: 限定论文所属的研究项目。
        query_text: 用于匹配正文的关键词。
        top_k: 最多返回多少个匹配块。

    Returns:
        按相关性从高到低排列的检索结果。
        没有匹配结果时返回空列表。
    """

    # 去除查询首尾的空白。
    cleaned_query = query_text.strip()

    if not cleaned_query:
        raise ValueError("query_text cannot be empty")

    if not 1 <= top_k <= 100:
        raise ValueError(
            "top_k must be between 1 and 100"
        )

    # plainto_tsquery() 在数据库中把普通文本
    # 转换成全文检索条件。
    #
    # 使用 english，与 search_vector 的生成规则一致。
    # 多个有效词项默认使用 AND 连接：
    # active perception 表示块中需要匹配两个词项。
    #
    # func 构造 SQL 函数表达式，此时还没有执行查询。
    search_query = func.plainto_tsquery(
        "english",
        cleaned_query,
    )

    # ts_rank_cd() 根据词项匹配及位置关系计算相关性。
    # label() 给查询结果中的分数列起一个名字。
    rank = func.ts_rank_cd(
        PaperChunkRecord.search_vector,
        search_query,
    ).label("score")

    statement = (
        # 同时选择 ORM 对象和分数，
        # 后面每行结果都可以解包为 record, score。
        select(
            PaperChunkRecord,
            rank,
        )

        # paper_chunks 没有直接保存 project_id。
        # 因此连接 papers 表，获得论文所属项目信息。
        .join(
            Paper,
            Paper.id == PaperChunkRecord.paper_id,
        )

        .where(
            # 限制在当前项目的论文范围内搜索。
            Paper.project_id == project_id,

            # @@ 是 PostgreSQL 的全文匹配运算符。
            # bool_op() 告诉 SQLAlchemy：
            # 这个表达式的结果是布尔值。
            PaperChunkRecord.search_vector
            .bool_op("@@")(search_query),
        )

        .order_by(
            # 优先返回相关性较高的文本块。
            rank.desc(),

            # 同分时使用明确的顺序，保证分页或
            # 重复查询时结果排序稳定。
            PaperChunkRecord.paper_id.asc(),
            PaperChunkRecord.page_number.asc(),
            PaperChunkRecord.chunk_index.asc(),
        )

        # 限制返回数量，控制后续模型的上下文大小。
        .limit(top_k)
    )

    # 因为查询同时选择了“对象”和“分数”，
    # 所以这里使用 execute()，而不是 scalars()。
    rows = session.execute(statement).all()

    results: list[ChunkSearchResult] = []

    for record, score in rows:
        # 将数据库对象转换成程序内部的领域模型。
        #
        # 转换后的结果不依赖 ORM 会话，
        # 可以传给证据提取器或 Agent 工具。
        chunk = PaperChunk(
            chunk_id=record.chunk_id,
            paper_id=str(record.paper_id),
            page_number=record.page_number,
            chunk_index=record.chunk_index,
            text=record.text,
        )

        results.append(
            ChunkSearchResult(
                chunk=chunk,
                score=float(score),
            )
        )

    # 检索只读取数据，因此不需要 commit()。
    # Session 的关闭由调用方负责。
    return results
