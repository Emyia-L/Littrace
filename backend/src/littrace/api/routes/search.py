from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    status,
)

from littrace.api.dependencies import DatabaseSession
from littrace.database.models.project import (
    ResearchProject,
)
from littrace.domain.models import ChunkSearchResult
from littrace.retriever.full_text_retriever import (
    search_chunks_by_full_text,
)

from littrace.retriever.vector_retriever import (
    search_chunks_by_vector,
)
from littrace.retriever.hybrid_retriever import (
    search_chunks_hybrid,
)

# 路由前缀包含项目 ID，明确每次搜索的数据范围。
router = APIRouter(
    prefix="/projects/{project_id}/search",
    tags=["search"],
)

@router.get(
    "",
    response_model=list[ChunkSearchResult],
    status_code=status.HTTP_200_OK,
    summary="搜索项目中的论文文本块",
)
def search_project_chunks(
    project_id: UUID,
    session: DatabaseSession,
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="英文关键词，例如 active perception",
    ),
    top_k: int = Query(
        default=8,
        ge=1,
        le=100,
        description="最多返回的匹配文本块数量",
    ),
) -> list[ChunkSearchResult]:
    """
    在指定研究项目中执行全文检索。

    返回带正文、页码、块 ID 和相关性分数的结果，
    按分数从高到低排列。
    """
    # q 和 top_k 来自 URL 查询参数。
    #
    # 例如：
    # ?q=OmniAgent&top_k=3
    #
    # Query(...) 中的 ... 表示 q 必须提供。
    # min_length、max_length、ge 和 le
    # 会由 FastAPI 自动校验，失败时返回 422。
    cleaned_query = q.strip()

    # min_length=1 会允许一个空格，
    # 因此需要额外拒绝完全由空白组成的查询。
    if not cleaned_query:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail="Search query cannot be blank",
        )

    # 检索函数只负责查询匹配块。
    # HTTP 路由负责区分“项目不存在”
    # 和“项目存在但没有匹配结果”。
    project = session.get(
        ResearchProject,
        project_id,
    )

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 复用上一小步的数据库检索函数。
    # session 由 FastAPI 的依赖注入提供。
    results = search_chunks_by_full_text(
        session=session,
        project_id=project_id,
        query_text=cleaned_query,
        top_k=top_k,
    )

    # results 已经是 Pydantic 模型列表。
    # FastAPI 会按照 response_model 转换成 JSON。
    #
    # 没有命中时返回 []，状态码仍然是 200。
    return results

@router.get(
    "/semantic",
    response_model=list[ChunkSearchResult],
    status_code=status.HTTP_200_OK,
    summary="语义搜索项目中的论文文本块",
)
def search_project_chunks_semantically(
    project_id: UUID,
    session: DatabaseSession,
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="用自然语言描述要查找的内容",
    ),
    top_k: int = Query(
        default=8,
        ge=1,
        le=100,
        description="最多返回的文本块数量",
    ),
) -> list[ChunkSearchResult]:
    """按问题含义查找项目中的相近论文文本块。"""

    cleaned_query = q.strip()
    if not cleaned_query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Search query cannot be blank",
        )

    # 先确认项目存在，区分“无搜索结果”和“项目不存在”。
    project = session.get(ResearchProject, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 检索器负责生成查询向量并在当前项目内排序。
    return search_chunks_by_vector(
        session=session,
        project_id=project_id,
        query_text=cleaned_query,
        top_k=top_k,
    )

@router.get(
    "/hybrid",
    response_model=list[ChunkSearchResult],
    status_code=status.HTTP_200_OK,
    summary="混合搜索项目中的论文文本块",
)
def search_project_chunks_hybrid(
    project_id: UUID,
    session: DatabaseSession,
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="描述要查找的关键词或内容",
    ),
    top_k: int = Query(
        default=8,
        ge=1,
        le=100,
        description="最多返回的文本块数量",
    ),
) -> list[ChunkSearchResult]:
    """融合关键词和语义检索，查找项目中的论文文本块。"""

    cleaned_query = q.strip()
    if not cleaned_query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Search query cannot be blank",
        )

    # 保证项目不存在时返回 404，而不是误报“没有搜索结果”。
    project = session.get(ResearchProject, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    return search_chunks_hybrid(
        session=session,
        project_id=project_id,
        query_text=cleaned_query,
        top_k=top_k,
    )

