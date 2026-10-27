"""查询向量缓存测试：不依赖真实 Redis、数据库或模型服务。"""

import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError

import littrace.embedding_cache as cache
import littrace.embedding_client as embedding_client


class FakeRedis:
    """只实现本功能用到的 GET / SET，方便检查是否真正命中。"""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.last_ttl: int | None = None

    def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self.values.get(key)

    def set(self, key: str, value: str, *, ex: int) -> bool:
        self.set_calls += 1
        self.last_ttl = ex
        self.values[key] = value.encode("utf-8")
        return True


@pytest.fixture
def cache_settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    settings = SimpleNamespace(
        embedding_cache_enabled=True,
        embedding_cache_redis_url=None,
        redis_url=SecretStr("redis://test-redis/0"),
        embedding_cache_ttl_seconds=86400,
        embedding_base_url="https://embedding.example/v1",
        embedding_model="test-embedding-model",
    )
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    return settings


def test_same_query_hits_cache_and_skips_second_model_call(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(cache, "_redis_client", lambda url: redis)
    calls: list[list[str]] = []

    def fake_create_embeddings(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.25] * 1024]

    monkeypatch.setattr(embedding_client, "create_embeddings", fake_create_embeddings)

    first = embedding_client.create_embedding("这篇论文解决什么问题？")
    second = embedding_client.create_embedding("这篇论文解决什么问题？")

    assert first == second == [0.25] * 1024
    assert calls == [["这篇论文解决什么问题？"]]
    assert redis.get_calls == 2
    assert redis.set_calls == 1
    assert redis.last_ttl == 86400
    # 缓存键不包含研究问题原文，避免暴露查询内容。
    assert "这篇论文" not in next(iter(redis.values))


def test_cache_key_changes_with_text_model_and_provider(
    cache_settings: SimpleNamespace,
) -> None:
    original = cache._cache_key("same question")
    assert original != cache._cache_key("another question")

    cache_settings.embedding_model = "new-model"
    assert original != cache._cache_key("same question")

    cache_settings.embedding_model = "test-embedding-model"
    cache_settings.embedding_base_url = "https://other-provider.example/v1"
    assert original != cache._cache_key("same question")


def test_invalid_cache_value_is_refreshed_from_model(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(cache, "_redis_client", lambda url: redis)
    key = cache._cache_key("question")
    redis.values[key] = json.dumps([0.1, 0.2]).encode("utf-8")
    calls = 0

    def fake_create_embeddings(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[0.5] * 1024]

    monkeypatch.setattr(embedding_client, "create_embeddings", fake_create_embeddings)

    assert embedding_client.create_embedding("question") == [0.5] * 1024
    assert calls == 1
    assert json.loads(redis.values[key]) == [0.5] * 1024


def test_redis_failure_falls_back_to_model(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    class BrokenRedis:
        def get(self, key: str) -> bytes | None:
            raise ConnectionError("Redis unavailable")

        def set(self, key: str, value: str, *, ex: int) -> None:
            raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(cache, "_redis_client", lambda url: BrokenRedis())
    calls = 0

    def fake_create_embeddings(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[0.75] * 1024]

    monkeypatch.setattr(embedding_client, "create_embeddings", fake_create_embeddings)

    assert embedding_client.create_embedding("question") == [0.75] * 1024
    assert calls == 1


def test_cache_can_be_disabled_without_connecting_to_redis(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    cache_settings.embedding_cache_enabled = False
    monkeypatch.setattr(
        cache,
        "_redis_client",
        lambda url: pytest.fail("disabled cache must not create Redis client"),
    )
    calls = 0

    def fake_create_embeddings(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[0.1] * 1024]

    monkeypatch.setattr(embedding_client, "create_embeddings", fake_create_embeddings)

    embedding_client.create_embedding("question")
    embedding_client.create_embedding("question")
    assert calls == 2


def test_blank_query_is_rejected_before_cache_access(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    monkeypatch.setattr(
        cache,
        "_redis_client",
        lambda url: pytest.fail("blank query must not access Redis"),
    )
    with pytest.raises(ValueError, match="空文本"):
        embedding_client.create_embedding("   ")


def test_missing_redis_configuration_disables_cache(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    cache_settings.redis_url = None
    monkeypatch.setattr(
        cache,
        "_redis_client",
        lambda url: pytest.fail("no Redis URL must not create a client"),
    )
    assert cache.get_cached_embedding("question") is None
    cache.put_cached_embedding("question", [0.1] * 1024)


def test_corrupt_nonfinite_cache_value_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    cache_settings: SimpleNamespace,
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(cache, "_redis_client", lambda url: redis)
    redis.values[cache._cache_key("question")] = json.dumps(
        [float("nan")] * 1024
    ).encode("utf-8")
    assert cache.get_cached_embedding("question") is None
