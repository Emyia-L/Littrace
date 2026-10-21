from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from openai import APIError

from littrace.api.dependencies import DatabaseSession
from littrace.api.schemas.evidence import EvidenceRequest
from littrace.database.models.project import ResearchProject
from littrace.domain.models import EvidenceGateResult
from littrace.evidence.gate import run_evidence_gate
from littrace.retriever.hybrid_retriever import search_chunks_hybrid


router = APIRouter(
    prefix="/projects/{project_id}/evidence",
    tags=["evidence"],
)


@router.post(
    "",
    response_model=EvidenceGateResult,
    status_code=status.HTTP_200_OK,
    summary="为研究问题提取并验证论文证据",
)
def extract_project_evidence(
    project_id: UUID,
    request: EvidenceRequest,
    session: DatabaseSession,
) -> EvidenceGateResult:
    """从项目论文中检索候选文本块，并生成经过引用验证的证据卡。"""

    # 先检查项目是否存在，避免对不存在的项目调用模型服务。
    project = session.get(ResearchProject, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    try:
        # 混合检索会结合关键词和语义，挑选与问题相关的文本块。
        search_results = search_chunks_hybrid(
            session=session,
            project_id=project_id,
            query_text=request.question,
            top_k=request.top_k,
        )

        # 没有候选文本时，不调用大模型，直接返回空结果。
        if not search_results:
            return EvidenceGateResult()

        # 检索分数不需要传给证据提取器，只传正文和页码等来源信息。
        candidate_chunks = [
            result.chunk
            for result in search_results
        ]

        # Evidence Gate 调用聊天模型生成证据卡，
        # 并检查每条引用是否确实存在于对应文本块中。
        return run_evidence_gate(
            question=request.question,
            chunks=candidate_chunks,
            max_cards=request.max_cards,
        )

    except APIError as exc:
        # 包括查询向量服务或聊天模型服务的 API 错误。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="An AI model service failed; please retry",
        ) from exc
    except ValueError as exc:
        # 例如模型返回内容无法解析或不符合预期结构。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Evidence generation failed",
        ) from exc