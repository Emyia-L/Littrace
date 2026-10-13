from littrace.domain.models import (
    EvidenceGateResult,
    PaperChunk,
    RejectedEvidence
)
from littrace.evidence.citation_validator import (
    validate_quote,
)
from littrace.evidence.extractor import (
    extract_evidence
)

def run_evidence_gate(
        question: str,
        chunks: list[PaperChunk],
        max_cards: int = 3
) -> EvidenceGateResult:
    """
    提取 Evidence Card，并验证每一条引用。

    验证通过的证据进入 accepted。
    验证失败的证据进入 rejected。
    """

    evidence_cards = extract_evidence(
        question=question,
        chunks=chunks,
        max_cards=max_cards
    )

    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}

    result = EvidenceGateResult()

    for evidence in evidence_cards:
        citation_check = validate_quote(
            evidence=evidence,
            chunks_by_id=chunks_by_id
        )

        if citation_check.valid:
            result.accepted.append(evidence)
        else:
            result.rejected.append(
                RejectedEvidence(
                    evidence=evidence,
                    citation_check=citation_check
                )
            )

    return result

        