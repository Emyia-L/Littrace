from littrace.domain.models import (
    EvidenceCard,
    PaperChunk,
)
from littrace.evidence.citation_validator import (
    validate_quote,
)


def create_chunk() -> PaperChunk:
    return PaperChunk(
        chunk_id="paper-001-p8-c3",
        paper_id="paper-001",
        page_number=8,
        text=(
            "The primary limi-\n"
            "tation is the computational cost."
        ),
    )


def create_evidence(
    quote: str = (
        "The primary limitation is "
        "the computational cost."
    ),
    page_number: int = 8,
    chunk_id: str = "paper-001-p8-c3",
) -> EvidenceCard:
    return EvidenceCard(
        question="What limitation did the authors mention?",
        claim="The method has a high computational cost.",
        relation="supports",
        quote=quote,
        page_number=page_number,
        chunk_id=chunk_id,
        limitations=[],
    )


def test_valid_quote_is_accepted() -> None:
    chunk = create_chunk()
    evidence = create_evidence()

    chunks_by_id = {
        chunk.chunk_id: chunk,
    }

    result = validate_quote(
        evidence=evidence,
        chunks_by_id=chunks_by_id,
    )

    assert result.valid is True
    assert result.reason is None


def test_missing_chunk_is_rejected() -> None:
    evidence = create_evidence(
        chunk_id="paper-001-p8-c999",
    )

    result = validate_quote(
        evidence=evidence,
        chunks_by_id={},
    )

    assert result.valid is False
    assert result.reason == "chunk_not_found"


def test_page_mismatch_is_rejected() -> None:
    chunk = create_chunk()
    evidence = create_evidence(
        page_number=7,
    )

    chunks_by_id = {
        chunk.chunk_id: chunk,
    }

    result = validate_quote(
        evidence=evidence,
        chunks_by_id=chunks_by_id,
    )

    assert result.valid is False
    assert result.reason == "page_mismatch"


def test_nonexistent_quote_is_rejected() -> None:
    chunk = create_chunk()
    evidence = create_evidence(
        quote="This sentence does not exist in the paper.",
    )

    chunks_by_id = {
        chunk.chunk_id: chunk,
    }

    result = validate_quote(
        evidence=evidence,
        chunks_by_id=chunks_by_id,
    )

    assert result.valid is False
    assert result.reason == "quote_not_found"