from decimal import Decimal
from types import SimpleNamespace

import pytest

from littrace.research.model_usage import (
    ModelUsageCollector,
    activate_model_usage,
    reset_model_usage,
    tracked_model_call,
)


def test_tracked_model_call_collects_tokens_and_estimated_cost():
    collector = ModelUsageCollector()
    token = activate_model_usage(collector)
    try:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1_000,
                completion_tokens=500,
                total_tokens=1_500,
            )
        )
        returned = tracked_model_call(
            component="agent_planner",
            model="test-model",
            call=lambda: response,
        )
    finally:
        reset_model_usage(token)

    assert returned is response
    assert collector.totals() == {
        "model_call_count": 1,
        "model_prompt_tokens": 1_000,
        "model_completion_tokens": 500,
        "model_total_tokens": 1_500,
        "model_duration_ms": collector.calls[0].duration_ms,
    }
    # 1000 × $2/百万 + 500 × $6/百万 = 5000 micro-USD = $0.005。
    assert collector.estimated_cost_microusd(
        input_price_per_million_usd=Decimal("2"),
        output_price_per_million_usd=Decimal("6"),
    ) == 5_000


def test_failed_model_call_is_counted_without_leaking_error_content(caplog):
    collector = ModelUsageCollector()
    token = activate_model_usage(collector)
    try:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            tracked_model_call(
                component="claim_validator",
                model="test-model",
                call=lambda: (_ for _ in ()).throw(
                    RuntimeError("provider unavailable secret-request-body")
                ),
            )
    finally:
        reset_model_usage(token)

    assert len(collector.calls) == 1
    assert collector.calls[0].succeeded is False
    assert collector.calls[0].total_tokens == 0
    assert "component=claim_validator" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "secret-request-body" not in caplog.text


def test_active_collector_can_be_restored_after_nested_scope():
    from littrace.research.model_usage import get_active_model_usage_collector

    outer = ModelUsageCollector()
    outer_token = activate_model_usage(outer)
    try:
        assert get_active_model_usage_collector() is outer
        inner = ModelUsageCollector()
        inner_token = activate_model_usage(inner)
        try:
            assert get_active_model_usage_collector() is inner
        finally:
            reset_model_usage(inner_token)
        assert get_active_model_usage_collector() is outer
    finally:
        reset_model_usage(outer_token)
