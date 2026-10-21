import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

import littrace.research.agent_loop as agent_loop
from littrace.domain.models import (
    ChunkSearchResult,
    EvidenceGateResult,
    PaperChunk,
)
from littrace.research.agent_actions import (
    AgentDecision,
    ExtractEvidenceAction,
    FinishAction,
    SearchAction,
    SynthesizeProjectAction,
)


@pytest.fixture(autouse=True)
def no_confirmed_synthesis_sources(monkeypatch):
    """普通循环测试默认没有可综合的历史人工确认证据。"""

    monkeypatch.setattr(
        agent_loop,
        "get_synthesis_readiness",
        lambda **kwargs: (
            {
                "eligible": False,
                "confirmed_evidence_count": 0,
                "paper_count": 0,
            },
            [],
        ),
    )

from littrace.domain.models import (
    AcceptedEvidence,
    CitationCheck,
    ClaimSupportCheck,
    EvidenceCard,
)


def test_loop_searches_extracts_then_finishes(monkeypatch):
    chunk = PaperChunk(
        chunk_id="chunk-1",
        paper_id="paper-1",
        page_number=1,
        chunk_index=1,
        text="This paper addresses cross-modal alignment.",
    )
    search_result = ChunkSearchResult(chunk=chunk, score=1.0)

    decisions = iter(
        [
            AgentDecision(
                action=SearchAction(
                    kind="search",
                    strategy="hybrid",
                    query="cross-modal alignment",
                    top_k=5,
                )
            ),
            AgentDecision(
                action=ExtractEvidenceAction(
                    kind="extract_evidence",
                    chunk_ids=["chunk-1"],
                    max_cards=2,
                )
            ),
            AgentDecision(
                action=FinishAction(
                    kind="finish",
                    reason="The evidence has been checked.",
                )
            ),
        ]
    )
    observations = []

    def fake_planner(**kwargs):
        # 保存每轮实际看到的状态，便于检查“观察 → 再规划”。
        observations.append(
            {
                "summary": kwargs["observation_summary"],
                "ids": set(kwargs["observed_chunk_ids"]),
                "remaining": kwargs["remaining_steps"],
                "accepted": kwargs["accepted_count"],
                "rejected": kwargs["rejected_count"],
                "recent": [
                    action.kind
                    for action in kwargs["recent_actions"]
                ],
            }
        )
        return next(decisions)

    monkeypatch.setattr(agent_loop, "choose_next_action", fake_planner)
    monkeypatch.setattr(
        agent_loop, "execute_search_action", lambda **kwargs: [search_result]
    )
    monkeypatch.setattr(
        agent_loop, "resolve_evidence_chunks", lambda **kwargs: [chunk]
    )
    monkeypatch.setattr(
        agent_loop,
        "execute_extract_evidence_action",
            lambda **kwargs: EvidenceGateResult(
            accepted_details=[
                AcceptedEvidence(
                    evidence=EvidenceCard(
                        question="What problem does this paper address?",
                        claim="The paper addresses cross-modal alignment.",
                        relation="supports",
                        quote="This paper addresses cross-modal alignment.",
                        page_number=1,
                        chunk_id="chunk-1",
                    ),
                    citation_check=CitationCheck(valid=True),
                    claim_support_check=ClaimSupportCheck(
                        verdict="supported",
                        reason="The quote directly states the claim.",
                    ),
                )
            ]
        ),
    )

    session = MagicMock()
    result = agent_loop.run_agent_loop(
        session=session,
        project_id=uuid4(),
        question="What problem does this paper address?",
        max_top_k=8,
        max_cards=3,
        max_steps=3,
    )

    assert result.stop_reason == "finish"
    assert result.steps_used == 3
    assert [item["remaining"] for item in observations] == [3, 2, 1]
    assert observations[1]["ids"] == {"chunk-1"}
    assert json.loads(observations[1]["summary"])["tool"] == "search"
    assert json.loads(observations[2]["summary"])["tool"] == "extract_evidence"
    # 一次结束综合候选读取事务，另两次来自搜索和提取前的读取。
    assert session.commit.call_count == 3

    assert [step.action.kind for step in result.steps] == [
        "search",
        "extract_evidence",
        "finish",
    ]
    assert result.steps[0].observation_summary == observations[1]["summary"]
    assert result.steps[1].observation_summary == observations[2]["summary"]
    assert result.steps[2].observation_summary is None
    # 搜索前、提取前为 0；提取成功后的下一轮为 1。
    assert [item["accepted"] for item in observations] == [0, 0, 1]
    assert [item["rejected"] for item in observations] == [0, 0, 0]
    assert [item["recent"] for item in observations] == [
        [],
        ["search"],
        ["search", "extract_evidence"],
    ]


def test_loop_stops_when_step_budget_is_used(monkeypatch):
    monkeypatch.setattr(
        agent_loop,
        "choose_next_action",
        lambda **kwargs: AgentDecision(
            action=SearchAction(
                kind="search",
                strategy="keyword",
                query="another search",
            )
        ),
    )
    search_tool = MagicMock(return_value=[])
    monkeypatch.setattr(agent_loop, "execute_search_action", search_tool)

    result = agent_loop.run_agent_loop(
        session=MagicMock(),
        project_id=uuid4(),
        question="Research question",
        max_top_k=8,
        max_cards=3,
        max_steps=2,
    )

    assert result.stop_reason == "step_limit"
    assert result.steps_used == 2
    assert len(result.steps) == 2
    assert [step.action.kind for step in result.steps] == ["search", "search"]
    # 同一请求虽然被规划器选了两次，实际检索只执行一次。
    search_tool.assert_called_once()
    skipped_observation = json.loads(result.steps[1].observation_summary)
    assert skipped_observation["skipped"] is True
    assert skipped_observation["reason"] == "identical_search_already_executed"

def test_loop_does_not_collect_identical_evidence_twice(monkeypatch):
    """两轮提取返回同一条证据时，最终只收集一次。"""
    chunk = PaperChunk(
        chunk_id="chunk-1",
        paper_id="paper-1",
        page_number=1,
        chunk_index=1,
        text="This paper addresses cross-modal alignment.",
    )
    detail = AcceptedEvidence(
        evidence=EvidenceCard(
            question="What problem does this paper address?",
            claim="The paper addresses cross-modal alignment.",
            relation="supports",
            quote="This paper addresses cross-modal alignment.",
            page_number=1,
            chunk_id="chunk-1",
        ),
        citation_check=CitationCheck(valid=True),
        claim_support_check=ClaimSupportCheck(
            verdict="supported",
            reason="The quote directly states the claim.",
        ),
    )

    # 搜索一次，同一块提取两次，最后由模型结束。
    decisions = iter([
        AgentDecision(action=SearchAction(
            kind="search", strategy="keyword", query="alignment"
        )),
        AgentDecision(action=ExtractEvidenceAction(
            kind="extract_evidence", chunk_ids=["chunk-1"]
        )),
        AgentDecision(action=ExtractEvidenceAction(
            kind="extract_evidence", chunk_ids=["chunk-1"]
        )),
        AgentDecision(action=FinishAction(
            kind="finish", reason="Evidence checked."
        )),
    ])

    monkeypatch.setattr(
        agent_loop,
        "choose_next_action",
        lambda **kwargs: next(decisions),
    )
    monkeypatch.setattr(
        agent_loop,
        "execute_search_action",
        lambda **kwargs: [ChunkSearchResult(chunk=chunk, score=1.0)],
    )
    monkeypatch.setattr(
        agent_loop,
        "resolve_evidence_chunks",
        lambda **kwargs: [chunk],
    )
    monkeypatch.setattr(
        agent_loop,
        "execute_extract_evidence_action",
        lambda **kwargs: EvidenceGateResult(
            accepted_details=[detail]
        ),
    )

    result = agent_loop.run_agent_loop(
        session=MagicMock(),
        project_id=uuid4(),
        question="What problem does this paper address?",
        max_top_k=8,
        max_cards=3,
        max_steps=4,
    )

    assert result.stop_reason == "finish"
    assert len(result.evidence.accepted_details) == 1

    first_observation = json.loads(result.steps[1].observation_summary)
    second_observation = json.loads(result.steps[2].observation_summary)
    assert first_observation["accepted_count"] == 1
    assert second_observation["accepted_count"] == 0
    assert second_observation["duplicate_count"] == 1


def test_completed_step_is_emitted_before_later_planner_failure(monkeypatch):
    """第二轮规划失败时，第一轮已完成步骤仍已交给回调。"""
    planner_calls = 0

    def fake_planner(**kwargs):
        nonlocal planner_calls
        planner_calls += 1
        if planner_calls == 1:
            return AgentDecision(
                action=SearchAction(
                    kind="search",
                    strategy="keyword",
                    query="alignment",
                )
            )
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(agent_loop, "choose_next_action", fake_planner)
    monkeypatch.setattr(
        agent_loop,
        "execute_search_action",
        lambda **kwargs: [],
    )

    emitted_steps = []
    with pytest.raises(RuntimeError, match="planner unavailable"):
        agent_loop.run_agent_loop(
            session=MagicMock(),
            project_id=uuid4(),
            question="What does the paper address?",
            max_top_k=8,
            max_cards=3,
            max_steps=2,
            on_step=emitted_steps.append,
        )

    # 循环未正常返回，但已完成的搜索步骤没有随异常丢失。
    assert len(emitted_steps) == 1
    assert emitted_steps[0].step_number == 1
    assert emitted_steps[0].action.kind == "search"
    assert json.loads(emitted_steps[0].observation_summary)["tool"] == "search"


def test_loop_can_choose_evidence_constrained_project_synthesis(monkeypatch):
    run_id = uuid4()
    synthesis_id = uuid4()
    candidates = [object(), object()]
    monkeypatch.setattr(
        agent_loop,
        "get_synthesis_readiness",
        lambda **kwargs: (
            {
                "eligible": True,
                "confirmed_evidence_count": 2,
                "paper_count": 2,
            },
            candidates,
        ),
    )
    decisions = iter([
        AgentDecision(action=SynthesizeProjectAction(
            kind="synthesize_project", max_sources=8, max_points=3,
        )),
        AgentDecision(action=FinishAction(
            kind="finish", reason="Cross-paper synthesis completed.",
        )),
    ])
    monkeypatch.setattr(
        agent_loop, "choose_next_action", lambda **kwargs: next(decisions)
    )
    synthesis_tool = MagicMock(return_value=SimpleNamespace(
        id=synthesis_id,
        result={"accepted": [{"title": "A"}], "rejected": []},
        source_snapshot=[{}, {}],
    ))
    monkeypatch.setattr(
        agent_loop, "execute_synthesize_project_action", synthesis_tool
    )

    result = agent_loop.run_agent_loop(
        session=MagicMock(),
        project_id=uuid4(),
        current_run_id=run_id,
        question="共同问题？",
        max_top_k=8,
        max_cards=3,
        max_steps=2,
    )

    assert result.synthesis_ids == [synthesis_id]
    assert [step.action.kind for step in result.steps] == [
        "synthesize_project", "finish",
    ]
    observation = json.loads(result.steps[0].observation_summary)
    assert observation["accepted_point_count"] == 1
    assert observation["source_count"] == 2
    synthesis_tool.assert_called_once()
