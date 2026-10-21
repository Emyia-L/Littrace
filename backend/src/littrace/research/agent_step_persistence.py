from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from littrace.database.models.agent_step import AgentStepRecord
from littrace.database.models.research_run import ResearchRun, ResearchRunStatus
from littrace.research.agent_actions import (
    ExtractEvidenceAction,
    FinishAction,
    SearchAction,
    SynthesizeProjectAction,
)
from littrace.research.agent_loop import AgentStepTrace


def save_agent_steps(
    *,
    session: Session,
    run_id: UUID,
    steps: list[AgentStepTrace],
) -> list[AgentStepRecord]:
    """按顺序写入一次 Agent 运行的轨迹；提交或回滚由调用方负责。"""

    run = session.get(ResearchRun, run_id)
    if run is None:
        raise ValueError("Research run not found")
    if run.execution_mode != "agent":
        raise ValueError("Agent steps require an agent research run")
    if len(steps) > 20:
        raise ValueError("Too many agent steps")

    # 本版在运行结束后一次性保存，不支持对同一运行追加第二批轨迹。
    existing_id = session.scalar(
        select(AgentStepRecord.id)
        .where(AgentStepRecord.run_id == run_id)
        .limit(1)
    )
    if existing_id is not None:
        raise ValueError("Agent steps already saved")

    records: list[AgentStepRecord] = []

    for expected_number, step in enumerate(steps, start=1):
        # 必须是 1、2、3……；不能重复、跳号或改变执行顺序。
        if step.step_number != expected_number:
            raise ValueError("Agent step numbers must be consecutive")

        action = step.action
        if not isinstance(
            action,
            (
                SearchAction,
                ExtractEvidenceAction,
                SynthesizeProjectAction,
                FinishAction,
            ),
        ):
            raise TypeError("Unsupported agent action")

        observation = step.observation_summary

        if isinstance(action, FinishAction):
            # finish 不调用工具，也只能是轨迹中的最后一步。
            if observation is not None:
                raise ValueError("Finish action cannot have an observation")
            if expected_number != len(steps):
                raise ValueError("Finish action must be the last step")
        elif observation is None or not observation.strip():
            raise ValueError("Tool action requires an observation")

        if observation is not None and len(observation) > 5000:
            raise ValueError("Agent observation is too long")

        records.append(
            AgentStepRecord(
                run_id=run_id,
                step_number=step.step_number,
                action_kind=action.kind,

                # action 是已通过 Pydantic 校验的对象；
                # 只保存声明过的字段，不保存模型原始回复。
                action_payload=action.model_dump(mode="json"),
                observation_summary=observation,
            )
        )

    # 全部检查完毕后才写入，避免发现后面一步不合法时
    # 前面几步已经被加入 Session。
    if records:
        session.add_all(records)
        session.flush()  # 发送 INSERT，但仍可 rollback。

    return records


def append_agent_step(
    *,
    session: Session,
    run_id: UUID,
    step: AgentStepTrace,
) -> AgentStepRecord:
    """追加一个已完成步骤；事务提交由调用方负责。

    后台执行每完成一步就调用此函数，前端因此能在研究运行中
    读取轨迹。数据库的 (run_id, step_number) 唯一约束防止重复。
    """
    run = session.get(ResearchRun, run_id)
    if run is None or run.execution_mode != "agent" or run.status != ResearchRunStatus.RUNNING:
        raise ValueError("Agent run is not running")

    last_step = session.scalar(
        select(AgentStepRecord)
        .where(AgentStepRecord.run_id == run_id)
        .order_by(AgentStepRecord.step_number.desc())
        .limit(1)
    )
    if last_step is not None and last_step.action_kind == "finish":
        raise ValueError("Finish action must be the last step")
    if step.step_number != (last_step.step_number if last_step else 0) + 1 or step.step_number > 20:
        raise ValueError("Agent step numbers must be consecutive")

    action = step.action
    if not isinstance(
        action,
        (SearchAction, ExtractEvidenceAction, SynthesizeProjectAction, FinishAction),
    ):
        raise TypeError("Unsupported agent action")
    observation = step.observation_summary
    if isinstance(action, FinishAction):
        if observation is not None:
            raise ValueError("Finish action cannot have an observation")
    elif observation is None or not observation.strip():
        raise ValueError("Tool action requires an observation")
    if observation is not None and len(observation) > 5000:
        raise ValueError("Agent observation is too long")

    record = AgentStepRecord(
        run_id=run_id,
        step_number=step.step_number,
        action_kind=action.kind,
        action_payload=action.model_dump(mode="json"),
        observation_summary=observation,
    )
    session.add(record)
    session.flush()
    return record
