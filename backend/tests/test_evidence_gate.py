import pytest

import littrace.evidence.gate as gate_module
from littrace.domain.models import (
    EvidenceCard,
    PaperChunk,
)


def create_chunk() -> PaperChunk:
    return PaperChunk(
        chunk_id="paper-001-p8-c3",
        paper_id="paper-001",
        page_number=8,
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