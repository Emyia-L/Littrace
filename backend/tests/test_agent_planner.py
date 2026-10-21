import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import littrace.research.agent_planner as planner


def install_fake_model(monkeypatch, action_data):
    """用假客户端替换真实模型客户端，让测试只检查决策器逻辑。"""
    fake_client = MagicMock()
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=json.dumps({"action": action_data})
                ),
            )
        ]
    )
    fake_client.__enter__.return_value.chat.completions.create.return_value = (
        fake_response
    )

    # monkeypatch 只在当前测试期间替换函数，测试结束后会自动恢复。
    monkeypatch.setattr(planner, "create_model_client", lambda: fake_client)
    monkeypatch.setattr(
        planner,
        "get_settings",
        lambda: SimpleNamespace(deepseek_model="test-model"),
    )
    return fake_client


@pytest.mark.parametrize(
    ("action_data", "expected_kind"),
    [
        (
            {
                "kind": "search",
                "strategy": "hybrid",
                "query": "active perception",
                "top_k": 3,
            },
            "search",
        ),
        (
            {
                "kind": "extract_evidence",
                "chunk_ids": ["chunk-a"],
                "max_cards": 2,
            },
            "extract_evidence",
        ),
        (
            {"kind": "finish", "reason": "Enough evidence was found."},
            "finish",
        ),
        (
            {
                "kind": "synthesize_project",
                "max_sources": 10,
                "max_points": 4,
            },
            "synthesize_project",
        ),
    ],
)
def test_choose_next_action_accepts_three_action_types(
    monkeypatch, action_data, expected_kind
):
    fake_client = install_fake_model(monkeypatch, action_data)

    decision = planner.choose_next_action(
        question="What problem does the paper solve?",
        observation_summary="The search found one relevant chunk.",
        observed_chunk_ids={"chunk-a"},
        remaining_steps=3,
        accepted_count=2,
        rejected_count=1,
        recent_actions=[],
        cross_run_memory="A prior human-confirmed finding.",
        synthesis_readiness={
            "eligible": True,
            "confirmed_evidence_count": 4,
            "paper_count": 2,
        },
    )

    assert decision.action.kind == expected_kind

    # 顺便确认观察到的 ID 确实传给了模型，而非只保存在本地。
    request = (
        fake_client.__enter__
        .return_value.chat.completions.create.call_args.kwargs
    )
    payload = json.loads(request["messages"][1]["content"])
    assert payload["observed_chunk_ids"] == ["chunk-a"]
    assert payload["verified_evidence"] == {
        "accepted_count": 2,
        "rejected_count": 1,
    }

    assert payload["recent_actions"] == []
    assert payload["cross_run_memory"] == "A prior human-confirmed finding."
    assert payload["synthesis_readiness"]["eligible"] is True


def test_choose_next_action_rejects_unobserved_chunk(monkeypatch):
    install_fake_model(
        monkeypatch,
        {
            "kind": "extract_evidence",
            "chunk_ids": ["invented-chunk"],
            "max_cards": 2,
        },
    )

    with pytest.raises(ValueError, match="unobserved"):
        planner.choose_next_action(
            question="What problem does the paper solve?",
            observation_summary="The search found one relevant chunk.",
            observed_chunk_ids={"chunk-a"},
            remaining_steps=3,
            accepted_count=0, rejected_count=0,
            recent_actions=[]
        )

    
