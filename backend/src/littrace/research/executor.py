import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from littrace.database.models.research_run import (
    ResearchRun,
    ResearchRunStatus,
)
from littrace.domain.models import EvidenceGateResult
from littrace.evidence.gate import run_evidence_gate
from littrace.evidence.persistence import save_evidence_records
from littrace.retriever.hybrid_retriever import search_chunks_hybrid
from typing import Literal

from littrace.research.agent_loop import AgentStepTrace, run_agent_loop

from littrace.research.agent_step_persistence import append_agent_step, save_agent_steps
from littrace.research.cross_run_memory import (
    format_memory_context,
    load_cross_run_memory,
    save_cross_run_memory,
)
from littrace.research.model_usage import (
    ModelUsageCollector,
    activate_model_usage,
    reset_model_usage,
)
from littrace.config import get_settings


logger = logging.getLogger(__name__)
# 第一版 Agent 的服务端步数上限；模型不能自行修改。
AGENT_MAX_STEPS = 6


class ResearchRunNotFoundError(LookupError):
    """运行不存在，或不属于指定项目。"""


class ResearchRunStateError(RuntimeError):
    """当前运行状态不允许执行指定操作。"""


def claim_research_run(
    *,
    session: Session,
    project_id: UUID,
    run_id: UUID,
    execution_mode: Literal["workflow", "agent"] = "workflow",
    expected_status: ResearchRunStatus = ResearchRunStatus.PENDING,
) -> ResearchRun:
    """原子地将 pending 改为 running，供同步和后台接口共用。"""

    # 先校验再领取运行执行权：无效模式不应改变数据库状态。
    if execution_mode not in {"workflow", "agent"}:
        raise ValueError("Unsupported execution_mode")

    if expected_status not in {
        ResearchRunStatus.PENDING,
        ResearchRunStatus.QUEUED,
    }:
        raise ValueError("Unsupported expected_status")

    # 第一阶段：取得执行权，并保存 running 状态。
    try:
        # 将状态条件放进数据库 UPDATE 中。
        #
        # 只有“ID、项目、pending 状态”全部匹配才会更新。
        # 两个请求同时启动同一运行时，只有一个能成功。
        start_statement = (
            update(ResearchRun)
            .where(
                ResearchRun.id == run_id,
                ResearchRun.project_id == project_id,
                ResearchRun.status == expected_status,
            )
            .values(
                status=ResearchRunStatus.RUNNING,
                execution_mode=execution_mode,  # 与领取执行权在同一条 UPDATE 中写入
                started_at=datetime.now(UTC),
                finished_at=None,
                error_message=None,
            )
            # 返回更新后的完整运行对象。
            .returning(ResearchRun)
            # 用数据库返回值刷新 Session 中可能已有的对象。
            .execution_options(populate_existing=True)
        )

        run = session.scalar(start_statement)

        if run is None:
            # 更新失败可能因为运行不存在，也可能因为状态不允许。
            # 再查询一次，以便向调用方提供准确的异常类型。
            existing_id = session.scalar(
                select(ResearchRun.id).where(
                    ResearchRun.id == run_id,
                    ResearchRun.project_id == project_id,
                )
            )

            if existing_id is None:
                raise ResearchRunNotFoundError(
                    "Research run not found"
                )

            raise ResearchRunStateError(
                f"Only {expected_status.value} research runs can be executed"
            )

        # 先提交 running 状态。
        # 其他请求随后查询运行时，就能看到它已经开始。
        session.commit()

    except Exception:
        session.rollback()
        raise

    return run


def execute_research_run(
    *,
    session: Session,
    project_id: UUID,
    run_id: UUID,
    execution_mode: Literal["workflow", "agent"] = "workflow",
) -> ResearchRun:
    """保持原同步调用方式：先领取运行，再执行具体研究。"""
    run = claim_research_run(
        session=session,
        project_id=project_id,
        run_id=run_id,
        execution_mode=execution_mode,
    )
    return execute_claimed_research_run(
        session=session,
        project_id=project_id,
        run_id=run_id,
        run=run,
        execution_mode=execution_mode,
    )


def execute_claimed_research_run(
    *,
    session: Session,
    project_id: UUID,
    run_id: UUID,
    run: ResearchRun,
    execution_mode: Literal["workflow", "agent"],
    persist_steps_live: bool = False,
) -> ResearchRun:
    """执行已领取的运行；后台模式可逐步提交 Agent 轨迹。"""
    question = run.question
    top_k = run.top_k
    max_cards = run.max_cards

    # 只收集本次 Agent 已完成的步骤；成功时仍沿用原有的统一保存路径。
    # 若之后失败，这份列表可用于保存失败前的轨迹。
    completed_agent_steps: list[AgentStepTrace] = []
    usage_collector = ModelUsageCollector()
    usage_token = activate_model_usage(usage_collector)

    def usage_values() -> dict[str, int]:
        """生成可直接写入 research_runs 的用量字段。"""

        values = usage_collector.totals()
        settings = get_settings()
        values["estimated_cost_microusd"] = usage_collector.estimated_cost_microusd(
            input_price_per_million_usd=settings.llm_input_cost_per_million_usd,
            output_price_per_million_usd=settings.llm_output_cost_per_million_usd,
        )
        return values

    # 第二阶段：执行研究，保存证据和 completed 状态。
    try:
        # Workflow 没有 Agent 停止类型；默认保持数据库 NULL。
        agent_stop_reason: str | None = None
        agent_steps_used: int | None = None
        # Workflow 没有 Agent 结果，因此后面不会写 agent_steps。
        agent_result = None
        memory_context = "No confirmed evidence from earlier runs is available."
        if execution_mode == "agent":
            # 只读取过往运行中最新人工决定为 confirmed 的证据。
            # 保存快照后先提交，使失败运行也能解释当时采用了哪些记忆。
            memory_items = load_cross_run_memory(
                session=session,
                project_id=project_id,
                current_run_id=run_id,
            )
            save_cross_run_memory(session=session, run_id=run_id, items=memory_items)
            session.commit()
            memory_context = format_memory_context(memory_items)
            # Agent 自行在“搜索、提取证据、结束”之间选择下一步。
            # run_agent_loop 返回的证据继续交给下方原有的保存逻辑。
            def record_step(step: AgentStepTrace) -> None:
                completed_agent_steps.append(step)
                if persist_steps_live:
                    # 每一步单独提交，使 SSE/轮询可在运行中看到新轨迹。
                    append_agent_step(session=session, run_id=run_id, step=step)
                    session.commit()

            agent_result = run_agent_loop(
                session=session,
                project_id=project_id,
                question=question,
                max_top_k=top_k,
                max_cards=max_cards,
                max_steps=AGENT_MAX_STEPS,
                on_step=record_step,
                cross_run_memory=memory_context,
                current_run_id=run_id,
            )
            # 运行摘要中的步数必须与实际轨迹数量一致。
            # 否则不能把一份不完整的审计记录标记为 completed。
            if len(agent_result.steps) != agent_result.steps_used:
                raise ValueError("Agent trace length does not match steps_used")
            result = agent_result.evidence
            agent_stop_reason = agent_result.stop_reason
            agent_steps_used = agent_result.steps_used

            logger.info(
                "Agent run %s stopped by %s after %s steps",
                run_id,
                agent_result.stop_reason,
                agent_result.steps_used,
            )
        else:
            # 原 Workflow 完全保留，现有 API 默认仍走这条路径。
            search_results = search_chunks_hybrid(
                session=session,
                project_id=project_id,
                query_text=question,
                top_k=top_k,
            )

            # 结束检索的只读事务，避免模型调用期间占用数据库连接。
            session.commit()

            if search_results:
                candidate_chunks = [
                    item.chunk for item in search_results
                ]
                result = run_evidence_gate(
                    question=question,
                    chunks=candidate_chunks,
                    max_cards=max_cards,
                )
            else:
                result = EvidenceGateResult()

        # 下方原有的 save_evidence_records(...)、completed 状态更新、
        # commit 以及失败处理全部保持不变。

        # 保存函数只执行 flush，尚未提交事务。
        save_evidence_records(
            session=session,
            run_id=run_id,
            result=result,
        )

        if agent_result is not None and not persist_steps_live:
            # 与上方证据保存一样，此函数只 flush、不 commit。
            # 下方完成状态的 commit 会同时提交证据和全部步骤。
            save_agent_steps(
                session=session,
                run_id=run_id,
                steps=agent_result.steps,
            )

        finish_statement = (
            update(ResearchRun)
            .where(
                ResearchRun.id == run_id,
                ResearchRun.project_id == project_id,
                ResearchRun.status == ResearchRunStatus.RUNNING,
            )
            .values(
                status=ResearchRunStatus.COMPLETED,
                finished_at=datetime.now(UTC),
                error_message=None,
                agent_stop_reason=agent_stop_reason,
                agent_steps_used=agent_steps_used,
                **usage_values(),
            )
            .returning(ResearchRun)
            .execution_options(populate_existing=True)
        )

        completed_run = session.scalar(finish_statement)

        if completed_run is None:
            raise ResearchRunStateError(
                "Research run is no longer running"
            )

        # 证据记录和 completed 状态在同一事务中提交。
        session.commit()

    except Exception:
        # 第三阶段：失败处理。
        # 先撤销第二阶段尚未提交的证据和状态修改。
        session.rollback()

        try:
            # 使用新的事务记录失败状态。
            #
            # 只更新仍处于 running 的记录，
            # 避免覆盖已经完成或被其他操作改变的状态。
            if execution_mode == "agent" and completed_agent_steps and not persist_steps_live:
                try:
                    # 轨迹与 failed 状态将由下方同一个 commit 提交。
                    # SAVEPOINT 保证轨迹校验或写入失败时，仍可尝试保存状态。
                    with session.begin_nested():
                        save_agent_steps(
                            session=session,
                            run_id=run_id,
                            steps=completed_agent_steps,
                        )
                except Exception:
                    logger.exception(
                        "Could not save partial agent steps for run %s",
                        run_id,
                    )

            session.execute(
                update(ResearchRun)
                .where(
                    ResearchRun.id == run_id,
                    ResearchRun.project_id == project_id,
                    ResearchRun.status == ResearchRunStatus.RUNNING,
                )
                .values(
                    status=ResearchRunStatus.FAILED,
                    finished_at=datetime.now(UTC),

                    # 保存固定摘要。
                    # 原始异常继续抛给调用方处理，
                    # 不直接把外部服务的原始响应存入此字段。
                    error_message="Research execution failed.",
                    **usage_values(),
                )
            )

            session.commit()

        except SQLAlchemyError:
            # 数据库本身不可用时，失败状态也可能无法保存。
            # 记录这次错误，同时保留最初的研究执行异常。
            session.rollback()
            logger.exception(
                "Could not save failed state for research run %s",
                run_id,
            )

        raise

    finally:
        # 无论成功还是失败，都恢复上下文，避免下一个请求继承本次计数器。
        reset_model_usage(usage_token)

    return completed_run
