import json
from dataclasses import dataclass, field
from typing import Callable, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from littrace.domain.models import EvidenceGateResult
from littrace.research.agent_actions import (
    ExtractEvidenceAction,
    FinishAction,
    SearchAction,
    SynthesizeProjectAction,
)
from littrace.research.agent_observations import summarize_search_results
from littrace.research.agent_planner import choose_next_action
from littrace.research.agent_tools import (
    execute_extract_evidence_action,
    execute_search_action,
    execute_synthesize_project_action,
    get_synthesis_readiness,
    resolve_evidence_chunks,
)

@dataclass(frozen=True)
class AgentStepTrace:
    """记录一轮 Agent 动作及其结果，包括被安全跳过的动作。"""

    step_number: int
    action: SearchAction | ExtractEvidenceAction | SynthesizeProjectAction | FinishAction

    # search、extract_evidence 执行后有观察；
    # finish 不调用工具，因此为 None。
    observation_summary: str | None

@dataclass
class AgentLoopResult:
    """循环的最终结果；区分模型主动结束与步数耗尽。"""

    evidence: EvidenceGateResult
    stop_reason: Literal["finish", "step_limit"]
    steps_used: int
    finish_reason: str | None = None
    # default_factory 保证每次运行得到独立列表，不会共用轨迹。
    steps: list[AgentStepTrace] = field(default_factory=list)
    # 只有 synthesize_project 工具成功完成时才会加入 ID。
    synthesis_ids: list[UUID] = field(default_factory=list)

def run_agent_loop(
        *,
        session: Session,
        project_id: UUID,
        question: str,
        max_top_k: int,
        max_cards: int,
        max_steps: int = 6,
        on_step: Callable[[AgentStepTrace], None] | None = None,
        cross_run_memory: str = "No confirmed evidence from earlier runs is available.",
        current_run_id: UUID | None = None,
) -> AgentLoopResult:
    """重复执行“规划 → 行动 → 观察”，直到结束或用完步数。"""

    if not question.strip():
        raise ValueError("question cannot be empty")
    if not 1 <= max_top_k <= 100:
        raise ValueError("max_top_k must be between 1 and 100")
    if not 1 <= max_cards <= 10:
        raise ValueError("max_cards must be between 1 and 10")
    if not 1 <= max_steps <= 20:
        raise ValueError("max_steps must be between 1 and 20")

    # 这些状态仅属于本次循环，不能由模型直接修改。
    observed_chunk_ids: set[str] = set()
    # 相同策略、查询和实际 top_k 的搜索，在一次运行中只执行一次。
    completed_searches: set[tuple[str, str, int]] = set()
    step_traces: list[AgentStepTrace] = []
    synthesis_ids: list[UUID] = []
    synthesis_readiness, synthesis_candidates = get_synthesis_readiness(
        session=session,
        project_id=project_id,
        question=question,
    )
    # 候选读取是只读事务；规划模型调用前及时释放。
    session.commit()

    def record_step(step: AgentStepTrace) -> None:
        """更新本次运行的轨迹，并通知可选的外部观察者。"""
        step_traces.append(step)
        # 回调发生在步骤完成之后；未传入时保持原有执行方式。
        if on_step is not None:
            on_step(step)

    collected_evidence = EvidenceGateResult()
    observation_summary = (
        "No tool has run yet. Search the project before extracting evidence."
    )

    for step_number in range(1, max_steps + 1):
        # 模型只选择动作；真正执行什么、最多执行多少步由程序控制。
        decision = choose_next_action(
            question=question,
            observation_summary=observation_summary,
            observed_chunk_ids=set(observed_chunk_ids),
            remaining_steps=max_steps - step_number + 1,
            # 每轮都从本次运行已收集的 Gate 结果重新计算；
            # 不依赖模型记忆，也不依赖最近一条观察文本。
            accepted_count=len(collected_evidence.accepted_details),
            rejected_count=len(collected_evidence.rejected),
            # step_traces 是本次运行已完成的动作。
            # 切片只取最近 3 个，并创建新列表，不修改原始轨迹。
            recent_actions=[
                step.action for step in step_traces[-3:]
            ],
            cross_run_memory=cross_run_memory,
            synthesis_readiness=synthesis_readiness,
        )
        action = decision.action

        if isinstance(action, SearchAction):
            # 用服务端限制后的 top_k 判断重复，而非模型请求的原始值。
            search_key = (
                action.strategy,
                action.query,
                min(action.top_k, max_top_k),
            )
            if search_key in completed_searches:
                # 保留轨迹并反馈给下一轮规划器，但不重复调用检索工具。
                # 被跳过的选择仍消耗一个步数，防止无限重试。
                observation_summary = json.dumps(
                    {
                        "tool": "search",
                        "skipped": True,
                        "reason": "identical_search_already_executed",
                        "strategy": action.strategy,
                        "query": action.query,
                    },
                    ensure_ascii=False,
                )
                record_step(
                    AgentStepTrace(
                        step_number=step_number,
                        action=action,
                        observation_summary=observation_summary,
                    )
                )
                continue

            results = execute_search_action(
                session=session,
                project_id=project_id,
                action=action,
                max_top_k=max_top_k
            )

            # 检索打开的只读事务到此结束，释放数据库连接；
            # 下一轮规划可能是耗时的模型请求。
            # 因此调用本函数前，调用方不应在 session 中留有待提交的写入。
            session.commit()

            observation_summary, visible_ids = summarize_search_results(
                action=action,
                results=results,
            )
            observed_chunk_ids.update(visible_ids)
            # 工具和观察整理都成功后，才记录为已完成的搜索。
            completed_searches.add(search_key)

            record_step(
                AgentStepTrace(
                    step_number=step_number,
                    action=action,
                    observation_summary=observation_summary,
                )
            )
            continue

        if isinstance(action, SynthesizeProjectAction):
            # eligibility 来自服务端数据库统计，而不是模型自己的声明。
            if not synthesis_readiness["eligible"]:
                observation_summary = json.dumps(
                    {
                        "tool": "synthesize_project",
                        "skipped": True,
                        "reason": "insufficient_confirmed_cross_paper_evidence",
                        **synthesis_readiness,
                    },
                    ensure_ascii=False,
                )
            elif current_run_id is None:
                # 测试或错误调用没有提供运行 ID 时，不创建不可追溯的综合记录。
                observation_summary = json.dumps(
                    {
                        "tool": "synthesize_project",
                        "skipped": True,
                        "reason": "current_run_id_required",
                    },
                    ensure_ascii=False,
                )
            elif synthesis_ids:
                observation_summary = json.dumps(
                    {
                        "tool": "synthesize_project",
                        "skipped": True,
                        "reason": "synthesis_already_completed_in_this_run",
                        "synthesis_id": str(synthesis_ids[-1]),
                    },
                    ensure_ascii=False,
                )
            else:
                synthesis = execute_synthesize_project_action(
                    session=session,
                    project_id=project_id,
                    run_id=current_run_id,
                    question=question,
                    action=action,
                    candidates=synthesis_candidates,
                )
                synthesis_ids.append(synthesis.id)
                accepted_count = len(synthesis.result.get("accepted", []))
                rejected_count = len(synthesis.result.get("rejected", []))
                observation_summary = json.dumps(
                    {
                        "tool": "synthesize_project",
                        "synthesis_id": str(synthesis.id),
                        "accepted_point_count": accepted_count,
                        "rejected_point_count": rejected_count,
                        "source_count": len(synthesis.source_snapshot),
                    },
                    ensure_ascii=False,
                )

            record_step(AgentStepTrace(
                step_number=step_number,
                action=action,
                observation_summary=observation_summary,
            ))
            continue

        if isinstance(action, ExtractEvidenceAction):
            # 先用数据库原文核对 ID，不能使用模型提供的正文。
            chunks = resolve_evidence_chunks(
                session=session,
                project_id=project_id,
                action=action,
                observed_chunk_ids=observed_chunk_ids,
            )
            session.commit()

            gate_result = execute_extract_evidence_action(
                question=question,
                action=action,
                chunks=chunks,
                max_cards=max_cards,
            )
            # 用完整的证据及检查结果生成指纹，只去除完全相同的记录。
            # 每次从已收集结果构造集合，因此既能处理同一轮内的重复，
            # 也能处理不同提取轮次之间的重复。
            known_accepted = {
                detail.model_dump_json()
                for detail in collected_evidence.accepted_details
            }
            known_rejected = {
                detail.model_dump_json()
                for detail in collected_evidence.rejected
            }

            new_accepted = []
            new_rejected = []
            duplicate_count = 0

            for detail in gate_result.accepted_details:
                fingerprint = detail.model_dump_json()
                if fingerprint in known_accepted:
                    duplicate_count += 1
                    continue

                known_accepted.add(fingerprint)
                new_accepted.append(detail)

            for detail in gate_result.rejected:
                fingerprint = detail.model_dump_json()
                if fingerprint in known_rejected:
                    duplicate_count += 1
                    continue

                known_rejected.add(fingerprint)
                new_rejected.append(detail)

            # 只有新记录进入运行结果；executor 最终保存的也是这个结果。
            collected_evidence.accepted_details.extend(new_accepted)
            collected_evidence.rejected.extend(new_rejected)

            # 把证据检查结果反馈给规划器，供它决定继续检索还是结束。
            # 限制数量和长度，避免观察上下文无限增长。
            rejection_reasons = []
            for rejected in new_rejected[:3]:
                reason = rejected.citation_check.reason
                if not reason and rejected.claim_support_check is not None:
                    reason = rejected.claim_support_check.reason
                rejection_reasons.append(
                    (reason or "No detailed reason")[:160]
                )

            observation_summary = json.dumps(
                {
                    "tool": "extract_evidence",
                    "selected_chunk_ids": action.chunk_ids,
                    "accepted_count": len(new_accepted),
                    "rejected_count": len(new_rejected),
                    "duplicate_count": duplicate_count,
                    "accepted_claims": [
                        detail.evidence.claim[:160]
                        for detail in new_accepted[:3]
                    ],
                    "rejection_reasons": rejection_reasons,
                },
                ensure_ascii=False,
            )
            record_step(
                AgentStepTrace(
                    step_number=step_number,
                    action=action,
                    observation_summary=observation_summary,
                )
            )
            continue

        if isinstance(action, FinishAction):

            record_step(
                AgentStepTrace(
                    step_number=step_number,
                    action=action,
                    observation_summary=None,
                )
            )
    
            return AgentLoopResult(
                evidence=collected_evidence,
                stop_reason="finish",
                steps_used=step_number,
                finish_reason=action.reason,
                steps=step_traces,
                synthesis_ids=synthesis_ids,
            )
        # 理论上 AgentDecision 已限制动作类型；这里作为最后一道防线。

        raise TypeError(f"Unsupported agent action: {type(action)!r}")

    # 不要求模型一定在预算内输出 finish：程序会强制停止。

    return AgentLoopResult(
        evidence=collected_evidence,
        stop_reason="step_limit",
        steps_used=max_steps,
        steps=step_traces,
        synthesis_ids=synthesis_ids,
    )

