from littrace.domain.models import (
    EvidenceGateResult,
    PaperChunk,
    RejectedEvidence,
)
from littrace.evidence.citation_validator import validate_quote
from littrace.evidence.claim_validator import check_claim_support
from littrace.evidence.extractor import extract_evidence

from littrace.domain.models import (
    AcceptedEvidence,
    EvidenceGateResult,
    PaperChunk,
    RejectedEvidence,
)


def run_evidence_gate(
    question: str,
    chunks: list[PaperChunk],
    max_cards: int = 3,
) -> EvidenceGateResult:
    """提取证据卡，依次检查引文定位和论断支持关系。"""

    evidence_cards = extract_evidence(
        question=question,
        chunks=chunks,
        max_cards=max_cards,
    )

    # 建立查找表，方便通过 chunk_id 定位原始文本块。
    chunks_by_id = {
        chunk.chunk_id: chunk
        for chunk in chunks
    }

    result = EvidenceGateResult()

    for evidence in evidence_cards:
        # 第一关：确定性检查，不调用模型。
        # 检查文本块是否存在、页码是否匹配、引文是否存在。
        citation_check = validate_quote(
            evidence=evidence,
            chunks_by_id=chunks_by_id,
        )

        if not citation_check.valid:
            result.rejected.append(
                RejectedEvidence(
                    evidence=evidence,
                    citation_check=citation_check,
                    claim_support_check=None,
                )
            )

            # 引文本身未通过验证，无需继续检查它能否支持论断。
            # continue 会跳到下一张证据卡。
            continue

        # 第二关：调用模型，判断当前引文能否支持完整论断。
        # 这里只传 claim 和 quote，不让检查器借用其他文本补充依据。
        support_check = check_claim_support(
            claim=evidence.claim,
            quote=evidence.quote,
        )

        # 只有明确判断为 supported 才接受。
        # unsupported 和 uncertain 都进入 rejected，
        # 同时保留判断理由，方便人工复查。
        if support_check.verdict == "supported":
            # 保存卡片，以及本轮实际得到的两项检查结果。
            # 后续入库时，可以直接使用这些数据，
            # 无需再次调用模型获取检查理由。
            result.accepted_details.append(
                AcceptedEvidence(
                    evidence=evidence,
                    citation_check=citation_check,
                    claim_support_check=support_check,
                )
            )
        else:
            # 引文定位通过，但论断不受支持或无法判断时，
            # 保存拒绝记录和检查理由，避免遗漏这张卡片。
            result.rejected.append(
                RejectedEvidence(
                    evidence=evidence,
                    citation_check=citation_check,
                    claim_support_check=support_check,
                )
            )

    return result
