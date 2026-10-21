from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import PaperChunkRecord
from littrace.domain.models import ChunkSearchResult, PaperChunk
from littrace.embedding_client import create_embedding

def search_chunks_by_vector(
    *,
    session: Session,
    project_id: UUID,
    query_text: str,
    top_k: int = 8,
) -> list[ChunkSearchResult]:
    """将问题转换为向量，在指定项目中搜索语义相近的文本块。"""
    cleaned_query = query_text.strip()

    if not cleaned_query:
        raise ValueError("query_text cannot be empty")
    
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")

    # 用与论文文本块相同的 Embedding 模型转换问题。
    query_embedding = create_embedding(cleaned_query)

    # cosine_distance 越小，表示两个向量越相近。
    distance = PaperChunkRecord.embedding.cosine_distance(
        query_embedding
    ).label("distance")

    statement = (
        select(PaperChunkRecord, distance)
        # 文本块本身没有 project_id，需要连接论文表来限定项目。
        .join(Paper, Paper.id == PaperChunkRecord.paper_id)
        .where(
            Paper.project_id == project_id,
            # 旧文本块可能暂时没有向量，排除它们。
            PaperChunkRecord.embedding.is_not(None),
        )
        .order_by(
            distance.asc(),
            # 距离相同时按固定顺序排列，保证结果稳定。
            PaperChunkRecord.paper_id.asc(),
            PaperChunkRecord.page_number.asc(),
            PaperChunkRecord.chunk_index.asc(),
        )
        .limit(top_k)
    )

    rows = session.execute(statement).all()
    results: list[ChunkSearchResult] = []

    for record, row_distance in rows:
        chunk = PaperChunk(
            chunk_id=record.chunk_id,
            paper_id=str(record.paper_id),
            page_number=record.page_number,
            chunk_index=record.chunk_index,
            text=record.text,
        )

        # 向量距离越小越好。转换成 0 到 1 的相似度分数，
        # 以符合 ChunkSearchResult 对 score 非负的校验。
        similarity = max(
            0.0,
            min(1.0, 1.0 - float(row_distance) / 2.0),
        )

        results.append(
            ChunkSearchResult(
                chunk=chunk,
                score=similarity,
            )
        )

    return results