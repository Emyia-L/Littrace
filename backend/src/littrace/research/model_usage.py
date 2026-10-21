"""收集一次研究运行中的模型调用用量，不记录提示词或模型回复正文。"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, TypeVar
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class ModelCallUsage:
    """一条模型调用的最小遥测信息。"""

    component: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: int
    succeeded: bool


@dataclass
class ModelUsageCollector:
    """只在当前执行上下文中累计调用；不会跨请求共享状态。"""

    calls: list[ModelCallUsage] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        return {
            "model_call_count": len(self.calls),
            "model_prompt_tokens": sum(item.prompt_tokens for item in self.calls),
            "model_completion_tokens": sum(item.completion_tokens for item in self.calls),
            "model_total_tokens": sum(item.total_tokens for item in self.calls),
            "model_duration_ms": sum(item.duration_ms for item in self.calls),
        }

    def estimated_cost_microusd(
        self,
        *,
        input_price_per_million_usd: Decimal,
        output_price_per_million_usd: Decimal,
    ) -> int:
        """换算为百万分之一美元；token × 每百万 token 美元价格恰为该单位。"""

        amount = (
            Decimal(self.totals()["model_prompt_tokens"]) * input_price_per_million_usd
            + Decimal(self.totals()["model_completion_tokens"]) * output_price_per_million_usd
        )
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


_active_collector: ContextVar[ModelUsageCollector | None] = ContextVar(
    "littrace_model_usage_collector",
    default=None,
)


def activate_model_usage(collector: ModelUsageCollector) -> Token:
    """为当前同步执行链启用收集器，并返回用于恢复上下文的 token。"""

    return _active_collector.set(collector)


def reset_model_usage(token: Token) -> None:
    """恢复先前上下文，防止用量串到下一次运行。"""

    _active_collector.reset(token)


def get_active_model_usage_collector() -> ModelUsageCollector | None:
    """读取当前收集器，供会建立子调用范围的复合工具汇总用量。"""

    return _active_collector.get()


ResponseT = TypeVar("ResponseT")


def _token_count(value: Any) -> int:
    """兼容不同 OpenAI 协议供应商缺失 usage 字段的情况。"""

    return value if isinstance(value, int) and value >= 0 else 0


def tracked_model_call(
    *,
    component: str,
    model: str,
    call: Callable[[], ResponseT],
) -> ResponseT:
    """执行模型请求并把 token、耗时和成败写入当前收集器。"""

    started = perf_counter()
    response: ResponseT | None = None
    succeeded = False
    try:
        response = call()
        succeeded = True
        return response
    finally:
        collector = _active_collector.get()
        if collector is not None:
            usage = getattr(response, "usage", None) if response is not None else None
            prompt_tokens = _token_count(getattr(usage, "prompt_tokens", None))
            completion_tokens = _token_count(getattr(usage, "completion_tokens", None))
            reported_total = _token_count(getattr(usage, "total_tokens", None))
            collector.calls.append(ModelCallUsage(
                component=component,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=reported_total or prompt_tokens + completion_tokens,
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
                succeeded=succeeded,
            ))
