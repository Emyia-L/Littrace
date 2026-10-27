"""查询 Embedding 的精确匹配 Redis 缓存；Redis 故障不能阻断检索。"""

import json
import logging
import math
from functools import lru_cache
from hashlib import sha256

from redis import Redis, RedisError

from littrace.config import get_settings


logger = logging.getLogger(__name__)

# paper_chunks.embedding 使用 Vector(1024)，不能把其他维度的缓存值交给检索器。
EMBEDDING_DIMENSIONS = 1024
CACHE_KEY_PREFIX = "littrace:query-embedding:v1:"


@lru_cache(maxsize=4)
def _redis_client(url: str) -> Redis:
    """每个进程复用连接池；较短超时避免 Redis 故障拖慢模型请求。"""

    return Redis.from_url(
        url,
        socket_connect_timeout=0.3,
        socket_timeout=0.3,
    )


def _cache_client() -> Redis | None:
    settings = get_settings()
    if not settings.embedding_cache_enabled:
        return None

    # 独立缓存地址优先；未提供时复用现有任务队列的 Redis 地址。
    address = settings.embedding_cache_redis_url or settings.redis_url
    if address is None:
        return None
    return _redis_client(address.get_secret_value())


def _cache_key(text: str) -> str:
    """完整输入、模型和服务地址共同决定向量；哈希避免泄露查询原文。"""

    settings = get_settings()
    material = json.dumps(
        {
            "base_url": settings.embedding_base_url.rstrip("/"),
            "model": settings.embedding_model,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return CACHE_KEY_PREFIX + sha256(material.encode("utf-8")).hexdigest()


def _valid_vector(value: object) -> bool:
    """缓存数据可能损坏或来自旧版本；只接受有限的 1024 维数值。"""

    if not isinstance(value, list) or len(value) != EMBEDDING_DIMENSIONS:
        return False

    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return False
        try:
            if not math.isfinite(item):
                return False
        except (OverflowError, TypeError, ValueError):
            return False
    return True


def get_cached_embedding(text: str) -> list[float] | None:
    """命中时返回向量；未命中、坏数据或 Redis 故障均返回 None。"""

    try:
        client = _cache_client()
        if client is None:
            return None
        raw = client.get(_cache_key(text))
    except (RedisError, ValueError):
        # 不打印连接异常正文，其中可能包含 Redis URL 或其他敏感信息。
        logger.warning("Embedding cache read failed; falling back to model")
        return None

    if raw is None:
        logger.debug("Embedding cache miss")
        return None

    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Embedding cache value is invalid; falling back to model")
        return None

    if not _valid_vector(value):
        logger.warning("Embedding cache vector is invalid; falling back to model")
        return None

    logger.debug("Embedding cache hit")
    return [float(item) for item in value]


def put_cached_embedding(text: str, vector: list[float]) -> None:
    """只缓存有效向量，并给键设置 TTL；失败不影响已生成的结果。"""

    if not _valid_vector(vector):
        return

    try:
        client = _cache_client()
        if client is None:
            return
        client.set(
            _cache_key(text),
            json.dumps(vector, separators=(",", ":")),
            ex=get_settings().embedding_cache_ttl_seconds,
        )
    except (RedisError, ValueError):
        logger.warning("Embedding cache write failed; using model result")
