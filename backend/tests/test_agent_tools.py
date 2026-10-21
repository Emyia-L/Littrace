from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

import littrace.research.agent_tools as agent_tools
from littrace.research.agent_actions import SearchAction
from pydantic import ValidationError

from littrace.domain.models import EvidenceGateResult, PaperChunk
from littrace.research.agent_actions import (
    ExtractEvidenceAction,
    SearchAction,
)


@pytest.mark.parametrize("strategy", ["keyword", "semantic", "hybrid"])
@pytest.mark.parametrize(
    ("requested_top_k", "expected_top_k"),
    [
        (20, 8),  # 模型要求 20，但本次运行最多允许 8。
        (3, 3),   # 模型只要求 3，应保留它的选择。
    ],
)
def test_search_action_uses_selected_tool_and_limit(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
    requested_top_k: int,
    expected_top_k: int,
) -> None:
    """检查三种策略的分发及 top_k 上限。"""

    expected_project_id = uuid4()
    calls: list[tuple[str, str, int]] = []

    def make_fake_retriever(tool_name: str):
        # 返回一个模拟检索函数，记录究竟调用了哪个工具。
        def fake_search(*, session, project_id, query_text, top_k):
            assert session is test_session
            assert project_id == expected_project_id
            calls.append((tool_name, query_text, top_k))
            return []

        return fake_search

    # 替换 agent_tools 已导入的三个函数名。
    # 测试不会访问 PostgreSQL，也不会调用 Embedding 服务。
    tool_names = {
        "keyword": "search_chunks_by_full_text",
        "semantic": "search_chunks_by_vector",
        "hybrid": "search_chunks_hybrid",
    }
    for tool_name, function_name in tool_names.items():
        monkeypatch.setattr(
            agent_tools,
            function_name,
            make_fake_retriever(tool_name),
        )

    action = SearchAction(
        kind="search",
        strategy=strategy,
        query="  paper methods  ",
        top_k=requested_top_k,
    )

    with Session() as test_session:
        results = agent_tools.execute_search_action(
            session=test_session,
            project_id=expected_project_id,
            action=action,
            max_top_k=8,
        )

    assert results == []
    # 只能调用选中的一个工具；查询两端空白应已被清除。
    assert calls == [(strategy, "paper methods", expected_top_k)]


@pytest.mark.parametrize("invalid_limit", [0, 101])
def test_search_action_rejects_invalid_run_limit(
    monkeypatch: pytest.MonkeyPatch,
    invalid_limit: int,
) -> None:
    """程序提供的运行上限无效时，不应启动检索。"""

    monkeypatch.setattr(
        agent_tools,
        "search_chunks_by_full_text",
        lambda **kwargs: pytest.fail("无效上限不应调用检索器"),
    )

    action = SearchAction(
        kind="search",
        strategy="keyword",
        query="paper methods",
    )

    with Session() as session:
        with pytest.raises(ValueError, match="max_top_k"):
            agent_tools.execute_search_action(
                session=session,
                project_id=uuid4(),
                action=action,
                max_top_k=invalid_limit,
            )

def test_extract_evidence_passes_ordered_chunks_and_card_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """传入已核验的块，并遵守本次运行的卡片上限。"""

    first = PaperChunk(
        chunk_id="paper-001-p1-c1",
        paper_id="paper-001",
        page_number=1,
        chunk_index=1,
        text="First source text.",
    )
    second = PaperChunk(
        chunk_id="paper-001-p1-c2",
        paper_id="paper-001",
        page_number=1,
        chunk_index=2,
        text="Second source text.",
    )
    action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=[second.chunk_id, first.chunk_id],
        max_cards=5,
    )
    expected_result = EvidenceGateResult()

    def fake_gate(*, question, chunks, max_cards):
        assert question == "What does the paper show?"
        assert chunks == [second, first]  # 顺序与 action 一致。
        assert max_cards == 2  # 运行上限低于模型要求的 5。
        return expected_result

    # 替换 agent_tools 中已导入的名称，避免调用真实模型。
    monkeypatch.setattr(agent_tools, "run_evidence_gate", fake_gate)

    result = agent_tools.execute_extract_evidence_action(
        question="What does the paper show?",
        action=action,
        chunks=[second, first],
        max_cards=2,
    )
    assert result is expected_result


def test_extract_evidence_rejects_bad_inputs_before_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """参数无效时，Evidence Gate 不应被调用。"""

    monkeypatch.setattr(
        agent_tools,
        "run_evidence_gate",
        lambda **kwargs: pytest.fail("无效输入不应调用模型"),
    )

    chunk = PaperChunk(
        chunk_id="paper-001-p1-c1",
        paper_id="paper-001",
        page_number=1,
        chunk_index=1,
        text="Source text.",
    )
    action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=[chunk.chunk_id],
    )

    with pytest.raises(ValueError, match="question"):
        agent_tools.execute_extract_evidence_action(
            question="   ",
            action=action,
            chunks=[chunk],
            max_cards=3,
        )

    for invalid_limit in (0, 11):
        with pytest.raises(ValueError, match="max_cards"):
            agent_tools.execute_extract_evidence_action(
                question="What does the paper show?",
                action=action,
                chunks=[chunk],
                max_cards=invalid_limit,
            )

    # 即使块本身有效，也不能把它配给另一个 chunk_id。
    wrong_action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=["different-chunk-id"],
    )
    with pytest.raises(ValueError, match="do not match"):
        agent_tools.execute_extract_evidence_action(
            question="What does the paper show?",
            action=wrong_action,
            chunks=[chunk],
            max_cards=3,
        )


def test_extract_evidence_action_limits_chunk_count() -> None:
    """动作模型应在工具执行前拒绝超过 8 个块。"""

    with pytest.raises(ValidationError):
        ExtractEvidenceAction(
            kind="extract_evidence",
            chunk_ids=[f"chunk-{index}" for index in range(9)],
        )