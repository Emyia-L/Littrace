from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from littrace.domain.models import ClaimSupportCheck
from littrace.research.project_comparisons import ConfirmedProjectEvidence
from littrace.research.project_synthesis import (
    create_evidence_constrained_synthesis,
    select_diverse_sources,
)
from littrace.research.synthesis_models import (
    GeneratedSynthesis,
    GeneratedSynthesisPoint,
)


def candidate(*, paper_id, suffix: str) -> ConfirmedProjectEvidence:
    return ConfirmedProjectEvidence(
        evidence_id=uuid4(),
        run_id=uuid4(),
        question="共同问题？",
        relation="supports",
        claim=f"论断 {suffix}",
        quote=f"Quote {suffix}",
        paper_id=paper_id,
        original_filename=f"paper-{suffix}.pdf",
        page_number=1,
    )


def test_select_diverse_sources_round_robins_across_papers():
    paper_a, paper_b = uuid4(), uuid4()
    sources = [
        candidate(paper_id=paper_a, suffix="a1"),
        candidate(paper_id=paper_a, suffix="a2"),
        candidate(paper_id=paper_b, suffix="b1"),
    ]

    selected = select_diverse_sources(
        sources,
        question=" 共同问题？ ",
        limit=2,
    )

    assert {item.paper_id for item in selected} == {paper_a, paper_b}


def test_synthesis_accepts_supported_cross_paper_point_and_rejects_bad_label(
    monkeypatch,
):
    import littrace.research.project_synthesis as synthesis

    paper_a, paper_b = uuid4(), uuid4()
    sources = [
        candidate(paper_id=paper_a, suffix="a"),
        candidate(paper_id=paper_b, suffix="b"),
    ]
    generated = GeneratedSynthesis(points=[
        GeneratedSynthesisPoint(
            title="共同发现",
            conclusion="两篇论文都支持该发现。",
            relationship="agreement",
            source_labels=["E1", "E2"],
        ),
        GeneratedSynthesisPoint(
            title="错误引用",
            conclusion="这条结论引用了不存在的来源。",
            relationship="inconclusive",
            source_labels=["E1", "E9"],
        ),
    ])
    monkeypatch.setattr(
        synthesis,
        "generate_candidate_synthesis",
        MagicMock(return_value=generated),
    )
    support_check = MagicMock(return_value=ClaimSupportCheck(
        verdict="supported",
        reason="两段引文共同支持结论。",
    ))
    monkeypatch.setattr(synthesis, "check_claim_support", support_check)
    monkeypatch.setattr(
        synthesis,
        "get_settings",
        lambda: SimpleNamespace(
            deepseek_model="test-model",
            llm_input_cost_per_million_usd=0,
            llm_output_cost_per_million_usd=0,
        ),
    )
    session = MagicMock()

    row = create_evidence_constrained_synthesis(
        session=session,
        project_id=uuid4(),
        question="共同问题？",
        candidates=sources,
        max_sources=12,
        max_points=5,
    )

    assert len(row.result["accepted"]) == 1
    assert row.result["accepted"][0]["source_evidence_ids"] == [
        str(sources[0].evidence_id),
        str(sources[1].evidence_id),
    ]
    assert row.result["rejected"][0]["rejection_reason"] == "unknown_source_label"
    support_check.assert_called_once()
    session.add.assert_called_once_with(row)
    session.commit.assert_called_once()
