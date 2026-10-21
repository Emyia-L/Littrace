import pytest

import littrace.evidence.gate as gate_module
from littrace.domain.models import (
    EvidenceCard,
    PaperChunk,
)
from unittest.mock import Mock
from littrace.domain.models import (
    ClaimSupportCheck,
    EvidenceCard,
    PaperChunk,
)

def create_chunk() -> PaperChunk:
    return PaperChunk(
        chunk_id="paper-001-p8-c3",
        paper_id="paper-001",
        page_number=8,
        chunk_index=3,
        text=(
            "The primary limitation is "
            "the computational cost."
        ),
    )


def fake_extract_evidence(
    question: str,
    chunks: list[PaperChunk],
    max_cards: int = 3,
) -> list[EvidenceCard]:
    """
    模拟模型同时返回一条真实引用和一条虚构引用。
    """

    valid_evidence = EvidenceCard(
        question=question,
        claim=(
            "The method has a high computational cost."
        ),
        relation="supports",
        quote=(
            "The primary limitation is "
            "the computational cost."
        ),
        page_number=8,
        chunk_id="paper-001-p8-c3",
        limitations=[],
    )

    invalid_evidence = EvidenceCard(
        question=question,
        claim="The method requires no computation.",
        relation="supports",
        quote=(
            "The method requires no computational resources."
        ),
        page_number=8,
        chunk_id="paper-001-p8-c3",
        limitations=[],
    )

    return [
        valid_evidence,
        invalid_evidence,
    ]


def test_gate_separates_valid_and_invalid_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate_module,
        "extract_evidence",
        fake_extract_evidence,
    )

    # 模拟论断检查通过。
    # 这个测试验证 Gate 的分流逻辑，不测试真实模型的判断能力。
    support_checker = Mock(
        return_value=ClaimSupportCheck(
            verdict="supported",
            reason="引用支持该论断。",
        )
    )

    monkeypatch.setattr(
        gate_module,
        "check_claim_support",
        support_checker,
    )

    chunk = create_chunk()

    result = gate_module.run_evidence_gate(
        question=(
            "What limitation did the authors mention?"
        ),
        chunks=[chunk],
    )

    assert len(result.accepted) == 1
    assert len(result.rejected) == 1

    assert (
        result.accepted[0].quote
        == "The primary limitation is the computational cost."
    )

    rejected = result.rejected[0]

    assert rejected.evidence.claim == (
        "The method requires no computation."
    )
    assert rejected.citation_check.valid is False
    assert (
        rejected.citation_check.reason
        == "quote_not_found"
    )

    # 两张卡中只有引文真实的那张需要调用论断检查器。
    support_checker.assert_called_once_with(
        claim=result.accepted[0].claim,
        quote=result.accepted[0].quote,
    )

    # 虚构引文在第一关就被拒绝，因此没有论断检查结果。
    assert rejected.claim_support_check is None

        # 通过的卡片应当保留完整检查明细。
    assert len(result.accepted_details) == 1

    detail = result.accepted_details[0]

    assert detail.evidence == result.accepted[0]
    assert detail.citation_check.valid is True

    # 检查保存的结果与检查器实际返回的结果一致，
    # 包括 verdict 和 reason。
    assert (
        detail.claim_support_check
        == support_checker.return_value
    )

    # 验证序列化后的接口数据。
    payload = result.model_dump(mode="json")

    # 原有 accepted 仍然输出证据卡列表。
    assert payload["accepted"] == [
        detail.evidence.model_dump(mode="json"),
    ]

    # 新增明细中包含真实的检查理由。
    assert (
        payload["accepted_details"][0]["claim_support_check"]
        == support_checker.return_value.model_dump(mode="json")
    )

@pytest.mark.parametrize(
    "verdict",
    ["unsupported", "uncertain"],
)
def test_gate_rejects_claims_without_confirmed_support(
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
) -> None:
    """即使引用真实，论断不受支持或无法判断时也不能接受。"""

    monkeypatch.setattr(
        gate_module,
        "extract_evidence",
        fake_extract_evidence,
    )

    support_checker = Mock(
        return_value=ClaimSupportCheck(
            verdict=verdict,
            reason="引用不足以确认完整论断。",
        )
    )

    monkeypatch.setattr(
        gate_module,
        "check_claim_support",
        support_checker,
    )

    result = gate_module.run_evidence_gate(
        question="What limitation did the authors mention?",
        chunks=[create_chunk()],
    )

    # 第一张卡未通过论断检查；第二张卡未通过引文定位检查。
    assert result.accepted == []
    assert len(result.rejected) == 2

    rejected = result.rejected[0]
    assert rejected.citation_check.valid is True
    assert rejected.claim_support_check is not None
    assert rejected.claim_support_check.verdict == verdict

    # 第二张卡的引文无效，不应额外调用模型。
    support_checker.assert_called_once()
    assert result.rejected[1].claim_support_check is None

    # 所有卡片都被拒绝时，通过明细也应为空。
    assert result.accepted_details == []

    