from pydantic import BaseModel

from littrace.api.schemas.evidence_record import EvidenceRecordResponse
from littrace.api.schemas.evidence_review import EvidenceReviewResponse


class ReviewQueueItem(BaseModel):
    """一张已接受的证据卡，以及针对它保存的全部人工复核。"""

    # 保留 Gate 的原始判断，供复核人查看引文与检查结果。
    evidence: EvidenceRecordResponse
    # 空列表表示尚无人复核；多次复核均保留，不覆盖历史。
    reviews: list[EvidenceReviewResponse]
