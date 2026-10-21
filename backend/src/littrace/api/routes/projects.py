from fastapi import (
    APIRouter,
    HTTPException,
    status,
)

from uuid import UUID

from fastapi import Query
from pydantic import BaseModel
from openai import APIError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from littrace.api.dependencies import (
    DatabaseSession,
)
from littrace.api.schemas.project import (
    ProjectCreate,
    ProjectResponse,
)
from littrace.api.schemas.evidence_comparison import (
    ComparisonCandidateResponse,
    EvidenceComparisonCreate,
    EvidenceComparisonResponse,
)
from littrace.database.models.project import (
    ResearchProject,
)
from littrace.database.models.evidence import EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview
from littrace.database.models.evidence_comparison import EvidenceComparisonReview
from littrace.database.models.research_run import ResearchRun, ResearchRunStatus
from littrace.research.report_draft import (
    ProjectReportComparison,
    ProjectReportEvidence,
    render_project_report_draft,
)
from littrace.research.report_sources import build_report_evidence
from littrace.research.review_selection import select_report_candidates
from littrace.research.project_comparisons import list_confirmed_project_evidence
from littrace.api.schemas.project_synthesis import (
    ProjectSynthesisCreate,
    ProjectSynthesisResponse,
    SynthesisSourceResponse,
)
from littrace.api.schemas.run_evaluation import ModelUsageSummary
from littrace.database.models.project_synthesis import ProjectSynthesis
from littrace.research.project_synthesis import (
    InsufficientSynthesisEvidenceError,
    create_evidence_constrained_synthesis,
)
from littrace.research.synthesis_models import ProjectSynthesisResult


class ProjectReportDraftResponse(BaseModel):
    """项目级报告正文及数量，便于前端展示与下载。"""

    project_id: UUID
    included_evidence_count: int
    included_comparison_count: int
    markdown: str
# prefix 会自动加到本文件的所有路由前面。
#
# 下面函数中的路径是空字符串 ""，
# 最终接口地址就是 POST /projects。

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
)


def _project_synthesis_response(row: ProjectSynthesis) -> ProjectSynthesisResponse:
    """把 JSONB 快照恢复成稳定的 API 结构，并精确格式化成本。"""

    return ProjectSynthesisResponse(
        id=row.id,
        project_id=row.project_id,
        triggered_by_run_id=row.triggered_by_run_id,
        question=row.question,
        model=row.model,
        sources=[
            SynthesisSourceResponse.model_validate(source)
            for source in row.source_snapshot
        ],
        result=ProjectSynthesisResult.model_validate(row.result),
        model_usage=ModelUsageSummary(
            call_count=row.model_call_count,
            prompt_tokens=row.model_prompt_tokens,
            completion_tokens=row.model_completion_tokens,
            total_tokens=row.model_total_tokens,
            duration_ms=row.model_duration_ms,
            estimated_cost_usd=(
                f"{row.estimated_cost_microusd // 1_000_000}."
                f"{row.estimated_cost_microusd % 1_000_000:06d}"
            ),
        ),
        created_at=row.created_at,
    )

@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建研究项目"
)
def create_project(
    project_data: ProjectCreate,
    session: DatabaseSession
) -> ProjectResponse:
    """
    创建一个新的文献研究项目。

    FastAPI 会先使用 ProjectCreate 校验请求 JSON，
    校验通过后才会执行这个函数。
    """
    # ORM 对象对应数据库中的一行。
    #
    # id、status、created_at 和 updated_at 不需要
    # 从用户请求中读取，它们由 ORM 或数据库生成。

    project = ResearchProject(
        title=project_data.title,
        research_question=(
            project_data.research_question
        ),
    )

    try:
        # add() 只是把对象加入当前 Session，
        # 此时通常还没有真正提交数据库事务。
        session.add(project)

        # commit() 提交事务。
        # PostgreSQL 中此时才真正保存该项目。
        session.commit()

        # 数据库负责生成 created_at 等字段。
        # refresh() 会重新查询该行，将这些字段
        # 加载回当前 ORM 对象。
        session.refresh(project)

    except SQLAlchemyError:
        # 如果写入或提交失败，必须回滚事务。
        #
        # 否则当前 Session 会保持在失败状态，
        # 后续 SQL 无法继续执行。
        session.rollback()

        # 暂时继续抛出原始异常，让 FastAPI 返回 500。
        # 后续会增加统一异常处理和结构化错误响应。
        raise

    # from_attributes=True 允许 Pydantic
    # 从 SQLAlchemy 对象属性中读取响应字段。
    return ProjectResponse.model_validate(project)

@router.get(
    "",
    response_model=list[ProjectResponse],
    summary="分页查询研究项目",
)
def list_projects(
    session: DatabaseSession,
    offset: int = Query(default=0, ge=0, description="跳过的项目数"),
    limit: int = Query(default=20, ge=1, le=100, description="最多返回的项目数"),
) -> list[ProjectResponse]:
    """供前端项目选择页使用；排序稳定，避免分页时同一时间的记录乱序。"""
    projects = session.scalars(
        select(ResearchProject)
        .order_by(ResearchProject.created_at.desc(), ResearchProject.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [ProjectResponse.model_validate(project) for project in projects]

@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="查询单个研究项目",
)
def get_project(
    project_id: UUID,
    session: DatabaseSession,
) -> ProjectResponse:
    """
    根据项目 ID 查询研究项目。

    FastAPI 会自动把 URL 中的 project_id
    从字符串转换为 UUID。

    如果传入的字符串不是合法 UUID，
    FastAPI 会自动返回 422，不会进入此函数。
    """
    # Session.get() 专门用于根据主键查询。
    #
    # 第一个参数是 ORM 模型；
    # 第二个参数是要查询的主键值。
    project = session.get(
        ResearchProject,
        project_id,
    )
     # 找不到记录时，session.get() 会返回 None。
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    return ProjectResponse.model_validate(project)


@router.get(
    "/{project_id}/report-draft",
    response_model=ProjectReportDraftResponse,
    summary="生成项目级证据综述草稿",
)
def get_project_report_draft(
    project_id: UUID,
    session: DatabaseSession,
) -> ProjectReportDraftResponse:
    """汇总项目所有已完成运行中最新人工确认的证据。"""
    project = session.get(ResearchProject, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 只纳入正常完成的运行，避免将失败运行留下的部分结果误当作完整结果。
    runs = session.scalars(
        select(ResearchRun)
        .where(
            ResearchRun.project_id == project_id,
            ResearchRun.status == ResearchRunStatus.COMPLETED,
        )
        .order_by(ResearchRun.created_at, ResearchRun.id)
    ).all()
    run_by_id = {run.id: run for run in runs}

    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.run_id.in_(run_by_id.keys()))
        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    ).all() if run_by_id else []

    reviews = session.scalars(
        select(EvidenceReview).where(
            EvidenceReview.evidence_id.in_([record.id for record in records])
        )
    ).all() if records else []

    # 与单次运行报告使用同一筛选规则：Gate 接受且最后决定为确认。
    selected = select_report_candidates(records, reviews)
    try:
        # 来源仍从数据库核对，不使用模型返回或 chunk_id 猜测文件与页码。
        sources = build_report_evidence(session, project_id, selected)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project report source is invalid",
        ) from exc

    # 两个列表都保持 selected 顺序，因此可安全配对并补上所属运行信息。
    items = [
        ProjectReportEvidence(
            evidence_id=record.id,
            question=run_by_id[record.run_id].question,
            relation=str(record.evidence["relation"]),
            claim=source.claim,
            quote=source.quote,
            original_filename=source.original_filename,
            page_number=source.page_number,
            run_id=record.run_id,
        )
        for record, source in zip(selected, sources, strict=True)
    ]
    # 只带入两条证据当前都仍经过人工确认的比较记录。
    selected_ids = {record.id for record in selected}
    comparison_rows = session.scalars(
        select(EvidenceComparisonReview)
        .where(
            EvidenceComparisonReview.project_id == project_id,
            EvidenceComparisonReview.left_evidence_id.in_(selected_ids),
            EvidenceComparisonReview.right_evidence_id.in_(selected_ids),
        )
        .order_by(EvidenceComparisonReview.review_order.desc())
    ).all() if selected_ids else []
    latest_comparisons: list[EvidenceComparisonReview] = []
    seen_pairs: set[tuple[UUID, UUID]] = set()
    for comparison in comparison_rows:
        pair = _comparison_pair_key(
            comparison.left_evidence_id,
            comparison.right_evidence_id,
        )
        if pair not in seen_pairs:
            latest_comparisons.append(comparison)
            seen_pairs.add(pair)

    report_comparisons = [
        ProjectReportComparison(
            left_evidence_id=row.left_evidence_id,
            right_evidence_id=row.right_evidence_id,
            decision=row.decision.value,
            note=row.note,
        )
        for row in latest_comparisons
    ]
    markdown = render_project_report_draft(
        project.title,
        project.research_question,
        items,
        report_comparisons,
    )
    return ProjectReportDraftResponse(
        project_id=project.id,
        included_evidence_count=len(items),
        included_comparison_count=len(report_comparisons),
        markdown=markdown,
    )


def _comparison_pair_key(left_id: UUID, right_id: UUID) -> tuple[UUID, UUID]:
    """比较关系没有左右方向；统一 ID 顺序以识别同一证据对。"""
    return tuple(sorted((left_id, right_id), key=str))


@router.get(
    "/{project_id}/comparison-candidates",
    response_model=list[ComparisonCandidateResponse],
    summary="列出可跨论文比较的已确认论断",
)
def list_project_comparison_candidates(
    project_id: UUID,
    session: DatabaseSession,
) -> list[ComparisonCandidateResponse]:
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    return [
        ComparisonCandidateResponse.model_validate(item.__dict__)
        for item in list_confirmed_project_evidence(session, project_id)
    ]


@router.post(
    "/{project_id}/syntheses",
    response_model=ProjectSynthesisResponse,
    status_code=status.HTTP_201_CREATED,
    summary="生成一次有证据约束的跨论文综合",
)
def create_project_synthesis(
    project_id: UUID,
    request: ProjectSynthesisCreate,
    session: DatabaseSession,
) -> ProjectSynthesisResponse:
    """只使用已人工确认且来源可核验的跨论文证据生成综合。"""

    project = session.get(ResearchProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    question = request.question or project.research_question
    candidates = list_confirmed_project_evidence(session, project_id)
    # 结束候选读取事务，模型调用期间不占用数据库事务快照。
    session.commit()
    try:
        row = create_evidence_constrained_synthesis(
            session=session,
            project_id=project_id,
            question=question,
            candidates=candidates,
            max_sources=request.max_sources,
            max_points=request.max_points,
        )
    except InsufficientSynthesisEvidenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="An AI model service failed; please retry",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cross-paper synthesis failed validation",
        ) from exc
    return _project_synthesis_response(row)


@router.get(
    "/{project_id}/syntheses",
    response_model=list[ProjectSynthesisResponse],
    summary="查询项目的跨论文自动综合历史",
)
def list_project_syntheses(
    project_id: UUID,
    session: DatabaseSession,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[ProjectSynthesisResponse]:
    """返回最近综合；每次结果都保留当时使用的证据快照。"""

    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    rows = session.scalars(
        select(ProjectSynthesis)
        .where(ProjectSynthesis.project_id == project_id)
        .order_by(ProjectSynthesis.created_at.desc(), ProjectSynthesis.id.desc())
        .limit(limit)
    ).all()
    return [_project_synthesis_response(row) for row in rows]


@router.get(
    "/{project_id}/comparisons",
    response_model=list[EvidenceComparisonResponse],
    summary="查询项目的人工跨论文比较结果",
)
def list_project_comparisons(
    project_id: UUID,
    session: DatabaseSession,
) -> list[EvidenceComparisonResponse]:
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")

    # 多次复核会追加历史记录；界面默认显示每对证据的最新决定。
    rows = session.scalars(
        select(EvidenceComparisonReview)
        .where(EvidenceComparisonReview.project_id == project_id)
        .order_by(EvidenceComparisonReview.review_order.desc())
    ).all()
    latest: list[EvidenceComparisonReview] = []
    seen: set[tuple[UUID, UUID]] = set()
    for row in rows:
        pair = _comparison_pair_key(row.left_evidence_id, row.right_evidence_id)
        if pair not in seen:
            latest.append(row)
            seen.add(pair)
    return [EvidenceComparisonResponse.model_validate(row) for row in latest]


@router.post(
    "/{project_id}/comparisons",
    response_model=EvidenceComparisonResponse,
    status_code=status.HTTP_201_CREATED,
    summary="保存一次人工跨论文比较",
)
def create_project_comparison(
    project_id: UUID,
    comparison: EvidenceComparisonCreate,
    session: DatabaseSession,
) -> EvidenceComparisonResponse:
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    if comparison.left_evidence_id == comparison.right_evidence_id:
        raise HTTPException(status_code=422, detail="Choose two different evidence cards")

    candidates = {
        item.evidence_id: item
        for item in list_confirmed_project_evidence(session, project_id)
    }
    left = candidates.get(comparison.left_evidence_id)
    right = candidates.get(comparison.right_evidence_id)
    if left is None or right is None:
        raise HTTPException(
            status_code=409,
            detail="Both evidence cards must be confirmed report candidates in this project",
        )
    if left.question.strip() != right.question.strip():
        raise HTTPException(
            status_code=422,
            detail="Evidence cards must belong to the same research question",
        )
    if left.paper_id == right.paper_id:
        raise HTTPException(
            status_code=422,
            detail="Cross-paper comparison requires evidence from two different papers",
        )

    # Each save appends a record; a later correction never erases the previous judgment.
    row = EvidenceComparisonReview(
        project_id=project_id,
        left_evidence_id=left.evidence_id,
        right_evidence_id=right.evidence_id,
        decision=comparison.decision,
        note=comparison.note,
    )
    try:
        session.add(row)
        session.commit()
        session.refresh(row)
    except SQLAlchemyError:
        session.rollback()
        raise
    return EvidenceComparisonResponse.model_validate(row)

