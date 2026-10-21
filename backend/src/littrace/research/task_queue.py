"""研究任务使用的 Redis 连接与 RQ 队列。"""

from redis import Redis
from rq import Queue

from littrace.config import get_settings

# API 入队和 Worker 监听时必须使用同一个队列名。
RESEARCH_QUEUE_NAME = "littrace_research"

def get_redis_connection(
        *,
        for_worker: bool = False
) -> Redis:
    """根据配置创建 Redis 客户端；创建对象本身尚不会执行研究任务。"""
    """API 使用短读超时；Worker 让 RQ 管理阻塞等待的超时。"""
    redis_url = get_settings().redis_url
    if redis_url is None:
        raise RuntimeError("REDIS_URL 未配置，无法使用研究任务队列")

    url = redis_url.get_secret_value()

    if for_worker:
        # 不传 socket_timeout：RQ 会为阻塞取任务设置合适的值。
        return Redis.from_url(url, socket_connect_timeout=3)

    return Redis.from_url(
        url,
        socket_connect_timeout=3,
        socket_timeout=5,
    )

def get_research_queue() -> Queue:
    """取得专门运行研究任务的队列。"""

    return Queue(
        RESEARCH_QUEUE_NAME,
        connection=get_redis_connection(),
        # 后续任务只传 run_id 等字符串。JSON 避开 RQ 默认的 pickle 反序列化风险。
        # Worker 启动时也必须指定相同的 JSON 序列化方式。
        serializer="json",
    )