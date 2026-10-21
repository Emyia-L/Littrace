import json
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from openai import APIError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from littrace.api.dependencies import DatabaseSession
from littrace.api.schemas.research_run import (
    ResearchRunCreate,
    ResearchRunResponse,
)
from littrace.database.models.project import ResearchProject
from littrace.database.models.research_run import (
    ResearchRun,
    ResearchRunStatus,
)

from littrace.api.schemas.agent_step import AgentStepResponse
from littrace.database.models.agent_step import AgentStepRecord
from littrace.research.executor import (
    ResearchRunNotFoundError,
    ResearchRunStateError,
    execute_research_run,
)
from littrace.database.session import SessionLocal

from littrace.api.schemas.evidence_record import EvidenceRecordResponse
from littrace.api.schemas.evidence_source import EvidenceSourceResponse
from littrace.api.schemas.evidence_review import (
    EvidenceReviewCreate,
    EvidenceReviewResponse,
)
from littrace.api.schemas.review_queue import ReviewQueueItem
from littrace.database.models.evidence import EvidenceDecision, EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview
from littrace.database.models.paper import Paper
from littrace.database.models.paper_chunk import PaperChunkRecord
from littrace.domain.models import EvidenceCard
from littrace.research.review_selection import select_report_candidates
from littrace.research.report_draft import render_report_draft
from littrace.research.report_sources import build_report_evidence
from littrace.api.schemas.run_evaluation import (
    ModelUsageSummary,
    RunMemoryResponse,
    RunQualitySignals,
)
from littrace.database.models.run_memory import ResearchRunMemory

import logging
from datetime import UTC, datetime
from littrace.research.task_queue import get_research_queue

logger = logging.getLogger(__name__)

# URL 中包含项目 ID，表明本文件的运行记录属于指定项目。
# 下方接口使用空路径 ""，最终地址就是：
# POST /projects/{project_id}/runs
router = APIRouter(
    prefix="/projects/{project_id}/runs",
    tags=["research-runs"],
)


def _get_project_run_or_404(
    *, session: DatabaseSession, project_id: UUID, run_id: UUID
) -> ResearchRun:
    """统一校验运行归属，避免指标和记忆接口发生跨项目读取。"""

    run = session.scalar(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )
    return run

@router.post(
    "",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建一次研究运行",
)
def create_research_run(
    project_id: UUID,
    run_data: ResearchRunCreate,
    session: DatabaseSession,
) -> ResearchRunResponse:
    """为指定项目创建一条等待执行的研究运行记录。"""

    # 三个参数分别来自：
    # project_id：URL 路径，FastAPI 会校验 UUID 格式。
    # run_data：JSON 请求正文，由 Pydantic 校验。
    # session：通过依赖注入取得的数据库会话。

    # 先检查项目是否存在。
    # 如果直接插入不存在的 project_id，会触发数据库外键错误；
    # 提前检查可以向客户端返回含义明确的 404。
    project = session.get(
        ResearchProject,
        project_id,
    )

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 将已经通过校验的请求数据转换为数据库模型。
    # 此时只是创建 Python 对象，还没有保存到数据库。
    run = ResearchRun(
        project_id=project_id,
        question=run_data.question,
        top_k=run_data.top_k,
        max_cards=run_data.max_cards,

        # 初始状态由服务端决定。
        # 后续真正开始执行研究时，再更新为 RUNNING。
        status=ResearchRunStatus.PENDING,
    )

    try:
        # 将对象加入 Session，等待写入。
        session.add(run)

        # 提交时，SQLAlchemy 会执行 INSERT。
        # 模型中的 UUID 默认值会在插入时生成，
        # created_at、updated_at 由数据库提供。
        session.commit()

        # 重新从数据库读取记录，
        # 确保返回对象包含数据库生成的时间等字段。
        session.refresh(run)

    except SQLAlchemyError:
        # 数据库操作失败时，回滚当前事务，
        # 使 Session 离开可能的错误状态。
        session.rollback()

        # 继续抛出异常，由 FastAPI 返回服务器错误。
        raise

    # from_attributes=True 允许从 ORM 对象读取字段。
    # 这里会把数据库记录转换成接口的响应模型。
    return ResearchRunResponse.model_validate(run)

@router.get(
    "",
    response_model=list[ResearchRunResponse],
    status_code=status.HTTP_200_OK,
    summary="查询项目的研究运行历史",
)
def list_research_runs(
    project_id: UUID,
    session: DatabaseSession,
    offset: int = Query(default=0, ge=0, description="跳过的运行数量"),
    limit: int = Query(default=20, ge=1, le=100, description="本次最多返回的运行数量"),
    run_status: ResearchRunStatus | None = Query(
        default=None,
        alias="status",
        description="可选：只查询指定状态的运行",
    ),
) -> list[ResearchRunResponse]:
    """按创建时间倒序查询运行，支持状态筛选及分页。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 先限定项目，后续筛选条件不能扩大数据范围。
    statement = select(ResearchRun).where(ResearchRun.project_id == project_id)
    if run_status is not None:
        statement = statement.where(ResearchRun.status == run_status)

    # 同一时间创建的记录再按主键排序，使分页结果保持稳定。
    records = session.scalars(
        statement
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [ResearchRunResponse.model_validate(record) for record in records]


@router.get(
    "/{run_id}",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_200_OK,
    summary="查询一次研究运行",
)
def get_research_run(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> ResearchRunResponse:
    """查询指定项目中的一条研究运行记录。"""

    # project_id 和 run_id 都来自 URL。
    # FastAPI 会先校验它们是否为合法 UUID；
    # 格式不正确时，直接返回 422。
    project = session.get(
        ResearchProject,
        project_id,
    )
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # select() 构造查询语句，此时尚未访问数据库。
    #
    # where() 中的两个条件同时成立才能匹配：
    # 1. 运行 ID 正确；
    # 2. 这条运行确实属于 URL 指定的项目。
    #
    # 这样即使知道另一个项目的运行 ID，
    # 也不能通过当前项目的 URL 取得那条记录。
    statement = select(ResearchRun).where(
        ResearchRun.id == run_id,
        ResearchRun.project_id == project_id,
    )

    # scalar() 执行查询，返回第一行中的 ORM 对象。
    # 因为 id 是主键，最多只会匹配一条记录；
    # 没有匹配记录时返回 None。
    run = session.scalar(statement)

    if run is None:
        # 运行不存在，或运行不属于当前项目，
        # 都返回相同的 404。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )

    # 复用创建接口使用的响应模型，
    # 让创建和查询返回一致的数据结构。
    return ResearchRunResponse.model_validate(run)


@router.post(
    "/{run_id}/execute",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_200_OK,
    summary="执行一次研究运行",
)
def execute_research_run_route(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
    execution_mode: Literal["workflow", "agent"] = Query(
        default="workflow",
        description="workflow 为原固定流程；agent 为动态规划循环",
    ),
) -> ResearchRunResponse:
    """同步执行一次 pending 运行，返回执行后的状态。"""

    # 先检查项目，让不存在的项目与现有创建、查询接口一样返回 404。
    # 执行器随后还会检查 run_id 是否属于这个项目。
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    try:
        # 执行器负责检索、证据检查、保存记录和状态事务。
        # 这是同步调用：请求会等待这些工作结束，成功时才返回 200。
        run = execute_research_run(
            session=session,
            project_id=project_id,
            run_id=run_id,
            execution_mode=execution_mode,  # 只传入经过 FastAPI 校验的两种模式之一
        )
    except ResearchRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        ) from exc
    except ResearchRunStateError as exc:
        # 已启动、已完成或已失败的运行不能再次启动。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except (APIError, ValueError) as exc:
        # 模型服务错误或模型输出格式错误由执行器先记为 failed。
        # 接口只返回固定摘要，避免把外部服务响应暴露给调用方。
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Research execution failed",
        ) from exc

    return ResearchRunResponse.model_validate(run)

@router.post(
    "/{run_id}/execute-async",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="后台启动一次研究运行",
)
def execute_research_run_async(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
    execution_mode: Literal["workflow", "agent"] = Query(default="workflow"),
) -> ResearchRunResponse:
    """原子地接受运行，再将 run_id 交给 Redis 队列。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    try:
        # WHERE 中限制 pending：重复点击只有第一次能改变状态。
        statement = (
            update(ResearchRun)
            .where(
                ResearchRun.id == run_id,
                ResearchRun.project_id == project_id,
                ResearchRun.status == ResearchRunStatus.PENDING,
            )
            .values(
                status=ResearchRunStatus.QUEUED,
                execution_mode=execution_mode,
                error_message=None,
                started_at=None,   # 尚未被 Worker 领取，不算开始执行。
                finished_at=None,
            )
            .returning(ResearchRun)
            .execution_options(populate_existing=True)
        )
        run = session.scalar(statement)
        if run is None:
            existing_id = session.scalar(
                select(ResearchRun.id).where(
                    ResearchRun.id == run_id,
                    ResearchRun.project_id == project_id,
                )
            )
            session.rollback()
            if existing_id is None:
                raise HTTPException(status_code=404, detail="Research run not found")
            raise HTTPException(status_code=409, detail="Only pending runs can be queued")
        # Worker 使用独立 Session，必须先提交，才能看见 queued 状态。
        session.commit()

    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail="无法更新研究运行状态") from exc
    
    try:
        get_research_queue().enqueue(
            # 字符串路径使 Worker 能从自己的进程导入这个函数。
            "littrace.research.jobs.execute_queued_research_run",
            str(run_id),                 # JSON 序列化只传字符串，不传 ORM 对象。
            job_id=str(run_id),          # 同一运行使用固定 RQ Job ID。
            unique=True,
            job_timeout=3600,            # 暂定最多运行一小时；后续按实测调整。
            result_ttl=86400,
            failure_ttl=604800,
        )
    except Exception as exc:
        logger.exception("研究运行 %s 入队失败", run_id)

        try:
            # Redis 可能已收到任务但确认响应丢失；只补偿仍在排队的运行，
            # 不能覆盖已经由 Worker 领取的 running 状态。
            session.execute(
                update(ResearchRun)
                .where(
                    ResearchRun.id == run_id,
                    ResearchRun.project_id == project_id,
                    ResearchRun.status == ResearchRunStatus.QUEUED,
                )
                .values(
                    status=ResearchRunStatus.FAILED,
                    error_message="后台任务提交失败，请创建重试运行",
                    finished_at=datetime.now(UTC),
                )
            )
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logger.exception("研究运行 %s 入队失败后的状态补偿也失败", run_id)

        raise HTTPException(
            status_code=503,
            detail="后台任务提交失败，请刷新运行状态",
        ) from exc

    return ResearchRunResponse.model_validate(run)


def stream_run_events(project_id: UUID, run_id: UUID, after_step: int = 0):
    """读取已提交状态和步骤，产生 Server-Sent Events。

    每次轮询使用新的 Session，以免 ORM 缓存遮蔽后台任务的提交。
    客户端重连可传 after_step，避免重新接收已有的步骤。
    """
    last_step = after_step
    last_status = None
    while True:
        with SessionLocal() as session:
            run = session.scalar(
                select(ResearchRun).where(
                    ResearchRun.id == run_id,
                    ResearchRun.project_id == project_id,
                )
            )
            if run is None:
                yield 'event: error\ndata: {"detail":"Research run not found"}\n\n'
                return
            steps = session.scalars(
                select(AgentStepRecord)
                .where(
                    AgentStepRecord.run_id == run_id,
                    AgentStepRecord.step_number > last_step,
                )
                .order_by(AgentStepRecord.step_number)
            ).all()
            # Session 关闭前转为普通 JSON 数据。
            run_data = ResearchRunResponse.model_validate(run).model_dump(mode="json")
            step_data = [
                AgentStepResponse.model_validate(step).model_dump(mode="json")
                for step in steps
            ]

        for step in step_data:
            last_step = step["step_number"]
            yield f'id: {last_step}\nevent: step\ndata: {json.dumps(step, ensure_ascii=False)}\n\n'
        if run_data["status"] != last_status:
            last_status = run_data["status"]
            yield f'event: status\ndata: {json.dumps(run_data, ensure_ascii=False)}\n\n'
        if last_status in {"completed", "failed"}:
            return
        yield ": keep-alive\n\n"
        time.sleep(1)


@router.get(
    "/{run_id}/metrics",
    response_model=RunQualitySignals,
    summary="查询研究运行的成本与质量信号",
)
def get_research_run_metrics(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> RunQualitySignals:
    """聚合模型成本、Evidence Gate 和人工复核结果。

    这些字段各自保留明确含义。接口不计算单一“质量分”，因为例如
    Gate 通过率高，既可能表示证据质量好，也可能表示 Gate 规则过松。
    """

    run = _get_project_run_or_404(
        session=session,
        project_id=project_id,
        run_id=run_id,
    )
    evidence_records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id == run_id)
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()

    accepted = [
        record
        for record in evidence_records
        if record.decision == EvidenceDecision.ACCEPTED
    ]
    rejected_count = len(evidence_records) - len(accepted)
    evaluated_count = len(evidence_records)
    acceptance_rate = (
        round(len(accepted) / evaluated_count, 4)
        if evaluated_count
        else None
    )

    # 同一证据允许多次复核；review_order 最大的一条才代表当前结论。
    latest_reviews: dict[UUID, EvidenceReview] = {}
    if accepted:
        reviews = session.scalars(
            select(EvidenceReview)
            .where(EvidenceReview.evidence_id.in_([row.id for row in accepted]))
            .order_by(EvidenceReview.review_order)
        ).all()
        for review in reviews:
            latest_reviews[review.evidence_id] = review

    confirmed_count = sum(
        review.decision.value == "confirmed"
        for review in latest_reviews.values()
    )
    dismissed_count = sum(
        review.decision.value == "dismissed"
        for review in latest_reviews.values()
    )
    pending_count = len(accepted) - confirmed_count - dismissed_count

    # chunk_id 来自已经过领域模型校验的证据卡。
    unique_chunks = {
        EvidenceCard.model_validate(record.evidence).chunk_id
        for record in accepted
    }
    memory_count = len(session.scalars(
        select(ResearchRunMemory).where(ResearchRunMemory.run_id == run_id)
    ).all())

    warnings: list[str] = []
    if run.status in {
        ResearchRunStatus.PENDING,
        ResearchRunStatus.QUEUED,
        ResearchRunStatus.RUNNING,
    }:
        warnings.append("run_not_finished")
    if run.status == ResearchRunStatus.COMPLETED and not accepted:
        warnings.append("no_accepted_evidence")
    if run.agent_stop_reason == "step_limit":
        warnings.append("agent_step_limit_reached")
    if accepted and confirmed_count == 0:
        warnings.append("evidence_not_human_confirmed")
    if evaluated_count and rejected_count > len(accepted):
        warnings.append("high_gate_rejection_rate")
    if run.model_call_count > 0 and run.model_total_tokens == 0:
        warnings.append("provider_did_not_report_token_usage")

    return RunQualitySignals(
        run_id=run.id,
        accepted_evidence_count=len(accepted),
        rejected_evidence_count=rejected_count,
        gate_acceptance_rate=acceptance_rate,
        human_confirmed_count=confirmed_count,
        human_dismissed_count=dismissed_count,
        human_pending_count=pending_count,
        unique_evidence_chunk_count=len(unique_chunks),
        cross_run_memory_count=memory_count,
        warnings=warnings,
        model_usage=ModelUsageSummary(
            call_count=run.model_call_count,
            prompt_tokens=run.model_prompt_tokens,
            completion_tokens=run.model_completion_tokens,
            total_tokens=run.model_total_tokens,
            duration_ms=run.model_duration_ms,
            # 数据库以 micro-USD 整数保存；整数拆分可避免浮点舍入误差。
            estimated_cost_usd=(
                f"{run.estimated_cost_microusd // 1_000_000}."
                f"{run.estimated_cost_microusd % 1_000_000:06d}"
            ),
        ),
    )


@router.get(
    "/{run_id}/memory",
    response_model=list[RunMemoryResponse],
    summary="查询本次 Agent 使用的跨运行记忆",
)
def list_research_run_memory(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> list[RunMemoryResponse]:
    """返回可追溯的记忆快照及其原始运行，不返回隐藏模型上下文。"""

    _get_project_run_or_404(
        session=session,
        project_id=project_id,
        run_id=run_id,
    )
    rows = session.execute(
        select(ResearchRunMemory, EvidenceRecord.run_id.label("source_run_id"))
        .join(
            EvidenceRecord,
            EvidenceRecord.id == ResearchRunMemory.source_evidence_id,
        )
        .where(ResearchRunMemory.run_id == run_id)
        .order_by(ResearchRunMemory.created_at, ResearchRunMemory.id)
    ).all()
    return [
        RunMemoryResponse(
            id=memory.id,
            source_evidence_id=memory.source_evidence_id,
            source_run_id=source_run_id,
            summary=memory.summary,
            created_at=memory.created_at,
        )
        for memory, source_run_id in rows
    ]


@router.get(
    "/{run_id}/events",
    summary="订阅研究运行状态及 Agent 步骤",
)
def get_research_run_events(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
    after_step: int = Query(default=0, ge=0, le=20),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """浏览器可使用 EventSource；连接关闭后可按步骤序号重连。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    if session.scalar(
        select(ResearchRun.id).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    ) is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    # EventSource 自动重连会发送最后收到的 id；显式 after_step 也可用于
    # 页面刷新后恢复。只接受有限范围的数字，避免把不可信 Header 当作 SQL。
    if (
        last_event_id is not None
        and len(last_event_id) <= 2
        and last_event_id.isascii()
        and last_event_id.isdecimal()
    ):
        after_step = max(after_step, min(int(last_event_id), 20))
    return StreamingResponse(
        stream_run_events(project_id, run_id, after_step),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.get(
    "/{run_id}/evidence",
    response_model=list[EvidenceRecordResponse],
    status_code=status.HTTP_200_OK,
    summary="查询一次研究运行保存的证据",
)
def list_research_run_evidence(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> list[EvidenceRecordResponse]:
    """按运行 ID 返回已保存的证据，包括接受和拒绝的记录。"""

    # 与本文件其他接口保持一致：项目不存在时返回 404。
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 同时检查运行 ID 和项目 ID，防止通过别的项目路径读取证据。
    # 这里只需要确认运行存在，因此查询 id 即可。
    existing_run_id = session.scalar(
        select(ResearchRun.id).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if existing_run_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )

    # 查询属于这次运行的全部证据。
    # created_at 相同时再按 id 排序，让返回顺序保持稳定。
    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id == run_id)
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()

    # 数据库的 JSONB 字典在这里转换为 EvidenceCard、
    # CitationCheck 和 ClaimSupportCheck 等明确的响应类型。
    return [
        EvidenceRecordResponse.model_validate(record)
        for record in records
    ]


@router.get(
    "/{run_id}/evidence/{evidence_id}/source",
    response_model=EvidenceSourceResponse,
    summary="查询已接受证据的原文位置",
)
def get_evidence_source(
    project_id: UUID,
    run_id: UUID,
    evidence_id: UUID,
    session: DatabaseSession,
) -> EvidenceSourceResponse:
    """通过证据引用的文本块，查找论文及其原始 PDF。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # 同时限定项目、运行和证据。不同项目即使知道证据 UUID 也无法读取。
    record = session.scalar(
        select(EvidenceRecord)
        .join(ResearchRun, ResearchRun.id == EvidenceRecord.run_id)
        .where(
            EvidenceRecord.id == evidence_id,
            EvidenceRecord.run_id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence record not found")
    if record.decision != EvidenceDecision.ACCEPTED:
        raise HTTPException(
            status_code=409,
            detail="Only accepted evidence has a verified source",
        )

    # JSONB 中存的是证据卡；由原有领域模型校验后再读取定位字段。
    card = EvidenceCard.model_validate(record.evidence)
    source = session.execute(
        select(
            Paper.id.label("paper_id"),
            Paper.original_filename,
            PaperChunkRecord.page_number,
        )
        .join(PaperChunkRecord, PaperChunkRecord.paper_id == Paper.id)
        .where(
            PaperChunkRecord.chunk_id == card.chunk_id,
            Paper.project_id == project_id,
        )
    ).one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Evidence source not found")
    if source.page_number != card.page_number:
        raise HTTPException(
            status_code=409,
            detail="Evidence page does not match source",
        )

    # PDF 文件读取接口会单独检查磁盘文件是否仍然存在。
    return EvidenceSourceResponse(
        evidence_id=record.id,
        paper_id=source.paper_id,
        original_filename=source.original_filename,
        page_number=source.page_number,
        chunk_id=card.chunk_id,
        quote=card.quote,
        file_url=f"/projects/{project_id}/papers/{source.paper_id}/file",
    )


@router.post(
    "/{run_id}/evidence/{evidence_id}/reviews",
    response_model=EvidenceReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="提交一次人工证据复核",
)
def create_evidence_review(
    project_id: UUID,
    run_id: UUID,
    evidence_id: UUID,
    review_data: EvidenceReviewCreate,
    session: DatabaseSession,
) -> EvidenceReviewResponse:
    """仅对本项目、本次运行中已通过 Gate 的证据追加复核。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # 证据必须同时匹配 URL 中的运行和项目，不能借用其他项目的 ID。
    evidence = session.scalar(
        select(EvidenceRecord)
        .join(ResearchRun, ResearchRun.id == EvidenceRecord.run_id)
        .where(
            EvidenceRecord.id == evidence_id,
            EvidenceRecord.run_id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence record not found")
    if evidence.decision != EvidenceDecision.ACCEPTED:
        raise HTTPException(
            status_code=409,
            detail="Only accepted evidence can be reviewed",
        )

    # 每次调用都插入新行。之前的人工意见及 Gate 记录保持原样。
    review = EvidenceReview(
        evidence_id=evidence.id,
        decision=review_data.decision,
        note=review_data.note,
    )
    try:
        session.add(review)
        session.commit()
        # created_at 由数据库生成，刷新后才能返回完整响应。
        session.refresh(review)
    except SQLAlchemyError:
        session.rollback()
        raise
    return EvidenceReviewResponse.model_validate(review)


@router.get(
    "/{run_id}/evidence/{evidence_id}/reviews",
    response_model=list[EvidenceReviewResponse],
    summary="查询证据的人工复核历史",
)
def list_evidence_reviews(
    project_id: UUID,
    run_id: UUID,
    evidence_id: UUID,
    session: DatabaseSession,
) -> list[EvidenceReviewResponse]:
    """读取一条证据的全部复核；没有记录时返回空列表。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # 三个 ID 必须沿同一条关系链匹配，避免跨项目读取复核意见。
    existing_id = session.scalar(
        select(EvidenceRecord.id)
        .join(ResearchRun, ResearchRun.id == EvidenceRecord.run_id)
        .where(
            EvidenceRecord.id == evidence_id,
            EvidenceRecord.run_id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if existing_id is None:
        raise HTTPException(status_code=404, detail="Evidence record not found")

    # 序号由数据库递增分配，即使 created_at 相同也保留提交顺序。
    records = session.scalars(
        select(EvidenceReview)
        .where(EvidenceReview.evidence_id == evidence_id)
        .order_by(EvidenceReview.review_order)
    ).all()
    return [EvidenceReviewResponse.model_validate(record) for record in records]


@router.get(
    "/{run_id}/review-queue",
    response_model=list[ReviewQueueItem],
    summary="查询一次运行的待复核证据清单",
)
def list_review_queue(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> list[ReviewQueueItem]:
    """合并已接受的证据和人工复核历史，供人工复核页面使用。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # 先核对运行归属；存在但尚无可复核证据时返回 []。
    existing_run_id = session.scalar(
        select(ResearchRun.id).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if existing_run_id is None:
        raise HTTPException(status_code=404, detail="Research run not found")

    evidence_records = session.scalars(
        select(EvidenceRecord)
        .where(
            EvidenceRecord.run_id == run_id,
            EvidenceRecord.decision == EvidenceDecision.ACCEPTED,
        )
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()
    if not evidence_records:
        return []

    # 一次查出所有相关复核，避免每张证据都单独访问数据库。
    reviews = session.scalars(
        select(EvidenceReview)
        .where(EvidenceReview.evidence_id.in_([row.id for row in evidence_records]))
        .order_by(EvidenceReview.review_order)
    ).all()
    reviews_by_evidence: dict[UUID, list[EvidenceReviewResponse]] = {
        row.id: [] for row in evidence_records
    }
    for review in reviews:
        reviews_by_evidence[review.evidence_id].append(
            EvidenceReviewResponse.model_validate(review)
        )

    return [
        ReviewQueueItem(
            evidence=EvidenceRecordResponse.model_validate(row),
            reviews=reviews_by_evidence[row.id],
        )
        for row in evidence_records
    ]


@router.get(
    "/{run_id}/report-candidates",
    response_model=list[EvidenceRecordResponse],
    summary="查询可进入报告的证据",
)
def list_report_candidates(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> list[EvidenceRecordResponse]:
    """返回 Gate 接受、且最终人工复核为确认的证据。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # URL 中的运行必须属于项目，避免通过已知的 run_id 跨项目读取。
    existing_run_id = session.scalar(
        select(ResearchRun.id).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if existing_run_id is None:
        raise HTTPException(status_code=404, detail="Research run not found")

    evidence_records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id == run_id)
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()
    if not evidence_records:
        return []

    # 一次取出这些证据的复核记录，再交给独立规则筛选。
    reviews = session.scalars(
        select(EvidenceReview).where(
            EvidenceReview.evidence_id.in_([row.id for row in evidence_records])
        )
    ).all()
    selected = select_report_candidates(evidence_records, reviews)
    return [EvidenceRecordResponse.model_validate(row) for row in selected]


@router.get(
    "/{run_id}/report-draft",
    response_class=PlainTextResponse,
    summary="读取带原文来源的 Markdown 报告草稿",
)
def get_report_draft(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> PlainTextResponse:
    """串联候选筛选、来源核对和 Markdown 排版。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # run_id 必须属于 URL 中的项目，报告不能读取别的项目的数据。
    run = session.scalar(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")

    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id == run_id)
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all()
    reviews = session.scalars(
        select(EvidenceReview).where(
            EvidenceReview.evidence_id.in_([record.id for record in records])
        )
    ).all()

    # 未经最终人工确认的证据不会进入草稿正文。
    selected = select_report_candidates(records, reviews)
    try:
        # 每条候选证据必须能核对到本项目的论文及页码。
        items = build_report_evidence(session, project_id, selected)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Report source is invalid",
        ) from exc

    markdown = render_report_draft(run.question, items)
    return PlainTextResponse(markdown, media_type="text/markdown")

@router.get(
    "/{run_id}/steps",
    response_model=list[AgentStepResponse],
    status_code=status.HTTP_200_OK,
    summary="查询一次 Agent 运行的执行轨迹",
)
def list_research_run_steps(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> list[AgentStepResponse]:
    """按步骤编号返回这次运行已保存的 Agent 轨迹。"""

    # 先确认 URL 中的项目存在，与同文件其他接口保持一致。
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 必须同时匹配 run_id 和 project_id：
    # 知道别的项目的运行 ID，也不能借当前项目的路径读取它。
    existing_run_id = session.scalar(
        select(ResearchRun.id).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if existing_run_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )

    # step_number 是 Agent 执行时的步骤顺序。
    # 运行存在但尚未产生轨迹时，查询结果为空列表。
    records = session.scalars(
        select(AgentStepRecord)
        .where(AgentStepRecord.run_id == run_id)
        .order_by(AgentStepRecord.step_number)
    ).all()

    # 将 ORM 记录转成经过 Pydantic 校验的接口响应。
    return [
        AgentStepResponse.model_validate(record)
        for record in records
    ]


@router.post(
    "/{run_id}/retry",
    response_model=ResearchRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="从失败运行创建一次重试",
)
def retry_research_run(
    project_id: UUID,
    run_id: UUID,
    session: DatabaseSession,
) -> ResearchRunResponse:
    """复制失败运行的输入，创建新的待执行运行并保留来源。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 外键只保证来源存在；此处还要核对它是否属于当前项目。
    source = session.scalar(
        select(ResearchRun).where(
            ResearchRun.id == run_id,
            ResearchRun.project_id == project_id,
        )
    )
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research run not found",
        )
    if source.status != ResearchRunStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed research runs can be retried",
        )

    # 每次重试使用独立 ID，从头执行。只复制输入参数，
    # 不复制来源的证据、轨迹、错误、时间或已执行步数。
    retry = ResearchRun(
        project_id=project_id,
        retry_of_run_id=source.id,
        question=source.question,
        top_k=source.top_k,
        max_cards=source.max_cards,
        status=ResearchRunStatus.PENDING,
    )
    try:
        session.add(retry)
        session.commit()
        session.refresh(retry)
    except SQLAlchemyError:
        session.rollback()
        raise

    # 本接口仅创建记录；调用方随后选择 execution_mode 并执行。
    return ResearchRunResponse.model_validate(retry)
