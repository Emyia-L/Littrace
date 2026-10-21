"""RQ 执行的研究任务：只接收运行 ID，不接收数据库 Session 或论文正文。"""

import logging
from uuid import UUID

from littrace.database.models.research_run import ResearchRun, ResearchRunStatus
from littrace.database.session import SessionLocal
from littrace.research.executor import (
    ResearchRunNotFoundError,
    ResearchRunStateError,
    claim_research_run,
    execute_claimed_research_run,
)

logger = logging.getLogger(__name__)

def execute_queued_research_run(run_id: str) -> None:
    """由独立 Worker 调用；每个任务创建自己的数据库 Session。"""
    run_uuid = UUID(run_id)

    with SessionLocal() as session:
        queued_run = session.get(ResearchRun, run_uuid)

        # 记录已删除、已被其他 Worker 领取，或重复投递时，不再调用模型。
        if queued_run is None or queued_run.status != ResearchRunStatus.QUEUED:
            logger.info("研究运行 %s 不再处于 queued，跳过重复任务", run_id)
            return

        mode = queued_run.execution_mode
        if mode not in {"workflow", "agent"}:
            # 后续 API 入队时必须先把选择的模式写入数据库。
            raise ValueError("Queued research run has no valid execution_mode")

        project_id = queued_run.project_id

        try:
            # UPDATE 的状态条件是最终的并发保护；上面的读取本身不是锁。
            claimed_run = claim_research_run(
                session=session,
                project_id=project_id,
                run_id=run_uuid,
                execution_mode=mode,
                expected_status=ResearchRunStatus.QUEUED,
            )
        except (ResearchRunNotFoundError, ResearchRunStateError):
            logger.info("研究运行 %s 已被其他 Worker 领取或删除", run_id)
            return

        # 复用现有 Agent/Workflow 执行器、证据保存及逐步持久化逻辑。
        execute_claimed_research_run(
            session=session,
            project_id=project_id,
            run_id=run_uuid,
            run=claimed_run,
            execution_mode=mode,
            persist_steps_live=True,
        )