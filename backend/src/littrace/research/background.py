"""进程内后台研究任务。

请求结束后不能继续使用 FastAPI 请求作用域的 Session，后台任务必须
重新创建自己的 Session。此方案适合单机 MVP；进程退出时任务不会自动恢复。
"""

import logging
from typing import Literal
from uuid import UUID

from littrace.database.models.research_run import ResearchRun, ResearchRunStatus
from littrace.database.session import SessionLocal
from littrace.research.executor import execute_claimed_research_run

logger = logging.getLogger(__name__)


def run_research_in_background(
    *,
    project_id: UUID,
    run_id: UUID,
    execution_mode: Literal["workflow", "agent"],
) -> None:
    """执行已经被 API 原子领取的运行，不共享请求的数据库会话。"""
    try:
        with SessionLocal() as session:
            run = session.get(ResearchRun, run_id)
            if (
                run is None
                or run.project_id != project_id
                or run.status != ResearchRunStatus.RUNNING
                or run.execution_mode != execution_mode
            ):
                logger.error("Claimed research run %s is no longer executable", run_id)
                return

            execute_claimed_research_run(
                session=session,
                project_id=project_id,
                run_id=run_id,
                run=run,
                execution_mode=execution_mode,
                persist_steps_live=True,
            )
    except Exception:
        # 执行器负责将可处理的执行失败写为 failed；这里仍记录日志，
        # 不把模型服务的原始错误文本返回给前端。
        logger.exception("Background research run %s failed", run_id)
