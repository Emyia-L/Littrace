"""根据 Evidence Gate 与人工复核结果筛选报告候选证据。"""

from collections.abc import Sequence
from uuid import UUID

from littrace.database.models.evidence import EvidenceDecision, EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview, ReviewDecision


def select_report_candidates(
    evidence_records: Sequence[EvidenceRecord],
    reviews: Sequence[EvidenceReview],
) -> list[EvidenceRecord]:
    """保留 Gate 已接受、且最后一次人工决定为确认的证据。

    调用方应先按项目和运行限定数据库记录。本函数只处理筛选规则，
    不查询数据库、不调用模型，也不修改传入的 ORM 对象。
    """
    latest_by_evidence: dict[UUID, EvidenceReview] = {}

    for review in reviews:
        previous = latest_by_evidence.get(review.evidence_id)
        # 列表即使被打乱，也使用数据库递增序号找出最后一次复核。
        if previous is None or review.review_order > previous.review_order:
            latest_by_evidence[review.evidence_id] = review

    candidates: list[EvidenceRecord] = []
    for evidence in evidence_records:
        if evidence.decision != EvidenceDecision.ACCEPTED:
            continue

        latest = latest_by_evidence.get(evidence.id)
        # 未复核和最终驳回的证据都不进入报告候选集。
        if latest is None or latest.decision != ReviewDecision.CONFIRMED:
            continue

        candidates.append(evidence)

    # 沿用输入证据的顺序，后续报告生成器可自行决定呈现顺序。
    return candidates
