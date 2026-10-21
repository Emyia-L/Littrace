"""在 Linux/Ubuntu 环境启动研究任务 Worker。"""

from rq import Worker

from littrace.research.task_queue import (
    RESEARCH_QUEUE_NAME,
    get_redis_connection,
)

def main() -> None:
    worker = Worker(
        [RESEARCH_QUEUE_NAME],
        connection=get_redis_connection(for_worker=True),
        # 必须与 task_queue.py 创建 Queue 时的 serializer="json" 一致。
        serializer="json",
    )
    worker.work()

if __name__ == "__main__":
    main()