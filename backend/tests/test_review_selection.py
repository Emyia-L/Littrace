from uuid import uuid4

import pytest

from littrace.database.models.evidence import EvidenceDecision, EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview, ReviewDecision
from littrace.research.review_selection import select_report_candidates


def make_evidence(decision: EvidenceDecision) -> EvidenceRecord:
    """创建内存中的 ORM 对象；这些单元测试不写数据库。"""
    return EvidenceRecord(
        id=uuid4(),
        run_id=uuid4(),
        decision=decision,
        evidence={
            "question": "论文解决了什么问题？",
            "claim": "方法改善了结果。",
            "relation": "supports",
            "quote": "The method improves results.",
            "page_number": 1,
            "chunk_id": "test-p1-c1",
            "limitations": [],
        },
        citation_check={"valid": True, "reason": None},
        claim_support_check={"verdict": "supported", "reason": "引文支持论断。"},
    )


def make_review(
    evidence: EvidenceRecord,
    order: int,
    decision: ReviewDecision,
) -> EvidenceReview:
    # review_order 在真实数据库中由 Identity 分配；这里手工设置测试值。
    return EvidenceReview(
        id=uuid4(),
        evidence_id=evidence.id,
        review_order=order,
        decision=decision,
        note=None,
    )


@pytest.mark.parametrize(
    ("decisions", "is_candidate"),
    [
        ([], False),  # 没有人复核。
        ([ReviewDecision.CONFIRMED], True),
        ([ReviewDecision.CONFIRMED, ReviewDecision.DISMISSED], False),
        ([ReviewDecision.DISMISSED, ReviewDecision.CONFIRMED], True),
    ],
)
def test_report_candidate_uses_latest_human_decision(
    decisions: list[ReviewDecision],
    is_candidate: bool,
) -> None:
    evidence = make_evidence(EvidenceDecision.ACCEPTED)
    reviews = [
        make_review(evidence, order, decision)
        for order, decision in enumerate(decisions, start=1)
    ]
    # 故意倒序输入：函数应依赖 review_order，而非列表位置。
    result = select_report_candidates([evidence], list(reversed(reviews)))
    assert result == ([evidence] if is_candidate else [])


def test_report_candidate_excludes_gate_rejection_and_keeps_evidence_order() -> None:
    first = make_evidence(EvidenceDecision.ACCEPTED)
    rejected = make_evidence(EvidenceDecision.REJECTED)
    unreviewed = make_evidence(EvidenceDecision.ACCEPTED)
    last = make_evidence(EvidenceDecision.ACCEPTED)
    reviews = [
        make_review(last, 3, ReviewDecision.CONFIRMED),
        make_review(rejected, 2, ReviewDecision.CONFIRMED),
        make_review(first, 1, ReviewDecision.CONFIRMED),
    ]

    result = select_report_candidates(
        [first, rejected, unreviewed, last],
        reviews,
    )
    assert result == [first, last]
