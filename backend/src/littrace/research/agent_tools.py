from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import PaperChunkRecord
from littrace.domain.models import (
    ChunkSearchResult,
    EvidenceGateResult,
    PaperChunk,
)
from littrace.evidence.gate import run_evidence_gate
from littrace.research.agent_actions import (
    MAX_EVIDENCE_CHUNKS,
    ExtractEvidenceAction,
    SearchAction,
    SynthesizeProjectAction,
)
from littrace.database.models.project_synthesis import ProjectSynthesis
from littrace.research.project_comparisons import (
    ConfirmedProjectEvidence,
    list_confirmed_project_evidence,
)
from littrace.research.project_synthesis import (
    create_evidence_constrained_synthesis,
)
from littrace.retriever.full_text_retriever import (
    search_chunks_by_full_text,
)
from littrace.retriever.hybrid_retriever import search_chunks_hybrid
from littrace.retriever.vector_retriever import search_chunks_by_vector

def execute_search_action(
        *,
        session: Session,
        project_id: UUID,
        action: SearchAction,
        max_top_k: int,
) -> list[ChunkSearchResult]:
    """执行模型选择的检索动作，返回可追溯的文本块。"""

    if not 1 <= max_top_k <= 100:
        raise ValueError("max_top_k must be between 1 and 100")

    # action.top_k 是模型提出的数量；
    # max_top_k 来自当前 ResearchRun，由程序决定。
    # 即使模型要求更多，也不能超过本次运行允许的数量。
    effective_top_k = min(action.top_k, max_top_k)

    # 只允许调用这三种已知工具。模型不能通过输出任意名称
    # 来指定 Python 函数或访问其他项目资源。
    if action.strategy == "keyword":
        search_function = search_chunks_by_full_text
    elif action.strategy == "semantic":
        search_function = search_chunks_by_vector
    elif action.strategy == "hybrid":
        search_function = search_chunks_hybrid
    else:
        raise ValueError("Unsupported search strategy")

    # project_id 由程序传入，而不是从模型生成的 action 中读取。
    # 三个检索器都会据此将结果限定在当前项目的论文中。
    # 本函数不提交事务；Session 的事务仍由上层运行器管理。
    return search_function(
        session=session,
        project_id=project_id,
        query_text=action.query,
        top_k=effective_top_k,
    )

def resolve_evidence_chunks(
        *,
        session: Session,
        project_id: UUID,
        action: ExtractEvidenceAction,
        observed_chunk_ids: set[str],
) -> list[PaperChunk]:
    """核对证据动作选择的块，并按模型指定的顺序返回原文。"""

    selected_ids = action.chunk_ids

    # 同一块重复提交会浪费后续模型输入，也会让结果难以解释。
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("chunk_ids must be unique")

    # observed_chunk_ids 必须由上层运行器根据本次检索结果维护，
    # 不能从模型返回的 JSON 中读取。
    # 模型只能选择它已经见过的块，不能凭空指定其他块的 ID。
    if not set(selected_ids).issubset(observed_chunk_ids):
        raise ValueError("Selected chunks were not observed in this run")

    # 再从数据库读取原文，并通过 papers 表核对项目归属。
    # 即使上层错误地把其他项目的 ID 放进 observed_chunk_ids，
    # 这里也不会把其他项目的论文正文交给模型。
    records = session.scalars(
        select(PaperChunkRecord)
        .join(Paper, Paper.id == PaperChunkRecord.paper_id)
        .where(
            Paper.project_id == project_id,
            PaperChunkRecord.chunk_id.in_(selected_ids),
        )
    ).all()

    records_by_id = {record.chunk_id: record for record in records}

    # 块可能在检索后被删除，也可能属于其他项目；
    # 只要有一个块无法在当前项目找到，就拒绝整个动作。
    if len(records_by_id) != len(selected_ids):
        raise ValueError("Selected chunks are unavailable in this project")

    chunks: list[PaperChunk] = []
    for chunk_id in selected_ids:
        record = records_by_id[chunk_id]

        # 使用数据库中的原文，而不是模型或请求传来的正文。
        chunks.append(
            PaperChunk(
                chunk_id=record.chunk_id,
                paper_id=str(record.paper_id),
                page_number=record.page_number,
                chunk_index=record.chunk_index,
                text=record.text,
            )
        )

    return chunks

def execute_extract_evidence_action(
    *,
    question: str,
    action: ExtractEvidenceAction,
    chunks: list[PaperChunk],
    max_cards: int,
) -> EvidenceGateResult:
    """对已核验的文本块运行 Evidence Gate。"""

    if not question.strip():
        raise ValueError("question cannot be empty")

    # max_cards 来自 ResearchRun，是服务端允许的上限。
    if not 1 <= max_cards <= 10:
        raise ValueError("max_cards must be between 1 and 10")

    # 与动作模型的限制共同控制一次调用的文本量。
    if len(chunks) > MAX_EVIDENCE_CHUNKS:
        raise ValueError("Too many evidence chunks")

    # 上一步 resolve_evidence_chunks 会按 action.chunk_ids 顺序返回块。
    # 再核对一次，防止调用方误传其他块或打乱顺序。
    if [chunk.chunk_id for chunk in chunks] != action.chunk_ids:
        raise ValueError("Resolved chunks do not match the action")

    # 模型可以要求更少的卡片，但不能超过本次运行的上限。
    effective_max_cards = min(action.max_cards, max_cards)

    # Evidence Gate 负责提取、核对引文、检查论断支持关系。
    # API 错误继续抛给未来的运行器，由运行器更新运行状态。
    return run_evidence_gate(
        question=question,
        chunks=chunks,
        max_cards=effective_max_cards,
    )


def get_synthesis_readiness(
    *,
    session: Session,
    project_id: UUID,
    question: str,
) -> tuple[dict[str, int | bool], list[ConfirmedProjectEvidence]]:
    """统计当前问题已有多少条、多少篇人工确认证据可用于综合。"""

    normalized_question = " ".join(question.split()).casefold()
    candidates = [
        item
        for item in list_confirmed_project_evidence(session, project_id)
        if " ".join(item.question.split()).casefold() == normalized_question
    ]
    paper_count = len({item.paper_id for item in candidates})
    return (
        {
            "eligible": len(candidates) >= 2 and paper_count >= 2,
            "confirmed_evidence_count": len(candidates),
            "paper_count": paper_count,
        },
        candidates,
    )


def execute_synthesize_project_action(
    *,
    session: Session,
    project_id: UUID,
    run_id: UUID,
    question: str,
    action: SynthesizeProjectAction,
    candidates: list[ConfirmedProjectEvidence],
) -> ProjectSynthesis:
    """执行受约束综合工具；模型动作不能指定项目、运行或任意证据 ID。"""

    return create_evidence_constrained_synthesis(
        session=session,
        project_id=project_id,
        question=question,
        candidates=candidates,
        max_sources=action.max_sources,
        max_points=action.max_points,
        triggered_by_run_id=run_id,
    )
