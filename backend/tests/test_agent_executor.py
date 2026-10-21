from types import SimpleNamespace
from unittest.mock import ANY, MagicMock
from uuid import uuid4

import pytest

import littrace.research.executor as executor
from littrace.domain.models import EvidenceGateResult
from littrace.research.agent_loop import AgentLoopResult

from littrace.research.agent_actions import FinishAction
from littrace.research.agent_loop import AgentStepTrace


def test_agent_mode_calls_loop_and_reuses_evidence_saving(monkeypatch):
    project_id = uuid4()
    run_id = uuid4()

    # 模拟数据库依次返回“刚开始的运行”和“已完成的运行”。
    pending_run = SimpleNamespace(
        question="论文解决什么问题？",
        top_k=5,
        max_cards=2,
    )
    completed_run = object()
    session = MagicMock()
    session.scalar.side_effect = [pending_run, completed_run]

    loop_result = AgentLoopResult(
        evidence=EvidenceGateResult(),
        stop_reason="finish",
        steps_used=1,
        finish_reason="Evidence checked.",
        steps=[
            AgentStepTrace(
                step_number=1,
                action=FinishAction(
                    kind="finish",
                    reason="Evidence checked.",
                ),
                observation_summary=None,
            )
        ],
    )
    fake_loop = MagicMock(return_value=loop_result)
    fake_save = MagicMock()
    fake_save_steps = MagicMock()
    monkeypatch.setattr(executor, "save_agent_steps", fake_save_steps)

    # 只替换执行器所调用的名称，不访问真实模型或数据库。
    monkeypatch.setattr(executor, "run_agent_loop", fake_loop)
    monkeypatch.setattr(executor, "save_evidence_records", fake_save)

    result = executor.execute_research_run(
        session=session,
        project_id=project_id,
        run_id=run_id,
        execution_mode="agent",
    )

    assert result is completed_run
    fake_loop.assert_called_once_with(
        session=session,
        project_id=project_id,
        question="论文解决什么问题？",
        max_top_k=5,
        max_cards=2,
        max_steps=6,
        on_step=ANY,
        cross_run_memory="No confirmed evidence from earlier runs is available.",
        current_run_id=run_id,
    )
    fake_save.assert_called_once_with(
        session=session,
        run_id=run_id,
        result=loop_result.evidence,
    )
    # 依次提交 running、记忆快照/关闭读取事务、证据与 completed。
    assert session.commit.call_count == 3
    fake_save_steps.assert_called_once_with(
    session=session,
    run_id=run_id,
    steps=loop_result.steps,
)


def test_invalid_mode_does_not_claim_run(monkeypatch):
    session = MagicMock()

    with pytest.raises(ValueError, match="execution_mode"):
        executor.execute_research_run(
            session=session,
            project_id=uuid4(),
            run_id=uuid4(),
            execution_mode="unknown",
        )

    # 参数不合法时，连领取执行权的数据库操作都不应开始。
    session.scalar.assert_not_called()


def test_background_agent_commits_each_step_before_finishing(monkeypatch):
    """后台模式逐步提交轨迹，不能等到 completed 才统一写入。"""
    project_id = uuid4()
    run_id = uuid4()
    session = MagicMock()
    completed_run = object()
    session.scalar.return_value = completed_run
    run = SimpleNamespace(question="问题", top_k=3, max_cards=1)
    step = AgentStepTrace(
        step_number=1,
        action=FinishAction(kind="finish", reason="已完成"),
        observation_summary=None,
    )
    loop_result = AgentLoopResult(
        evidence=EvidenceGateResult(),
        stop_reason="finish",
        steps_used=1,
        steps=[step],
    )

    def fake_loop(**kwargs):
        kwargs["on_step"](step)
        return loop_result

    append = MagicMock()
    batch_save = MagicMock()
    monkeypatch.setattr(executor, "run_agent_loop", fake_loop)
    monkeypatch.setattr(executor, "append_agent_step", append)
    monkeypatch.setattr(executor, "save_agent_steps", batch_save)
    monkeypatch.setattr(executor, "save_evidence_records", MagicMock())

    result = executor.execute_claimed_research_run(
        session=session,
        project_id=project_id,
        run_id=run_id,
        run=run,
        execution_mode="agent",
        persist_steps_live=True,
    )
    assert result is completed_run
    append.assert_called_once_with(session=session, run_id=run_id, step=step)
    batch_save.assert_not_called()
    # 记忆快照、实时步骤、证据与 completed 状态各提交一次。
    assert session.commit.call_count == 3


def test_background_worker_uses_own_session(monkeypatch):
    """后台任务不会复用 HTTP 请求作用域中的数据库会话。"""
    from contextlib import nullcontext
    import littrace.research.background as background
    from littrace.database.models.research_run import ResearchRunStatus

    project_id = uuid4()
    run_id = uuid4()
    run = SimpleNamespace(
        project_id=project_id,
        status=ResearchRunStatus.RUNNING,
        execution_mode="agent",
    )
    worker_session = MagicMock()
    worker_session.get.return_value = run
    fake_execute = MagicMock()
    monkeypatch.setattr(background, "SessionLocal", lambda: nullcontext(worker_session))
    monkeypatch.setattr(background, "execute_claimed_research_run", fake_execute)

    background.run_research_in_background(
        project_id=project_id,
        run_id=run_id,
        execution_mode="agent",
    )
    fake_execute.assert_called_once_with(
        session=worker_session,
        project_id=project_id,
        run_id=run_id,
        run=run,
        execution_mode="agent",
        persist_steps_live=True,
    )
