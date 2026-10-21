from uuid import UUID

from sqlalchemy.orm import Session

from littrace.domain.models import ChunkSearchResult
from littrace.retriever.full_text_retriever import (
    search_chunks_by_full_text,
)
from littrace.retriever.vector_retriever import (
    search_chunks_by_vector,
)

# RRF 平滑常数：降低单个检索器排名差异的影响。
RRF_K = 60

def search_chunks_hybrid(
    *,
    session: Session,
    project_id: UUID,
    query_text: str,
    top_k: int = 8,
) -> list[ChunkSearchResult]:
    """融合关键词与语义检索的结果，返回综合排名。"""

    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")

    # 每个检索器多取一些候选，再从融合结果中选最终 top_k。
    # 两个检索器各自最多返回 100 条。
    candidate_k = min(top_k * 2, 100)

    keyword_results = search_chunks_by_full_text(
        session=session,
        project_id=project_id,
        query_text=query_text,
        top_k=candidate_k,
    )
    semantic_results = search_chunks_by_vector(
        session=session,
        project_id=project_id,
        query_text=query_text,
        top_k=candidate_k,
    )

    # 按文本块 ID 合并同一条证据在不同检索结果中的排名。
    fused_scores: dict[str, float] = {}
    chunks_by_id = {}

    for result_list in (keyword_results, semantic_results):
        for rank, result in enumerate(result_list, start=1):
            chunk_id = result.chunk.chunk_id

            # 排名越靠前，贡献越大；同一块出现在两路结果中会累加。
            fused_scores[chunk_id] = (
                fused_scores.get(chunk_id, 0.0)
                + 1.0 / (RRF_K + rank)
            )
            chunks_by_id[chunk_id] = result.chunk

    # 分数从高到低；同分时用 chunk_id 保持顺序稳定。
    ranked_chunk_ids = sorted(
        fused_scores,
        key=lambda chunk_id: (
            -fused_scores[chunk_id],
            chunk_id,
        ),
    )

    return [
        ChunkSearchResult(
            chunk=chunks_by_id[chunk_id],
            score=fused_scores[chunk_id],
        )
        for chunk_id in ranked_chunk_ids[:top_k]
    ]