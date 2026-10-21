from typing import Literal
from pydantic import BaseModel, Field, computed_field


class PageText(BaseModel):
    page_number: int = Field(ge = 1)
    text: str

class PaperChunk(BaseModel):

    """
    论文中的一个文本块。

    page_number 表示原始 PDF 页码；
    chunk_index 表示该页中的第几个文本块。
    """

    chunk_id: str
    paper_id: str
    page_number: int = Field(ge = 1)  
    chunk_index: int = Field(ge=1)  
    text: str

class EvidenceCard(BaseModel):
    question: str
    claim: str
    relation: Literal[
        "supports",
        "contradicts",
        "mixed",
        "insufficient"
    ]
    quote: str
    page_number: int = Field(ge = 1)
    chunk_id: str
    limitations: list[str] = Field(default_factory=list)

class CitationCheck(BaseModel):
    valid: bool
    reason: str | None = None

class ClaimSupportCheck(BaseModel):
    """记录一段引用是否足以支持对应论断。"""

    # supported：引用直接支持 claim 中的所有实质性事实。
    # unsupported：claim 包含引用未支持的信息，或与引用矛盾。
    # uncertain：引用残缺或含义不清，无法可靠判断。
    verdict: Literal[
        "supported",
        "unsupported",
        "uncertain",
    ]

    # 保存判断理由，方便开发者和用户复查。
    # 例如：“claim 提到了推理成本，但引用没有提到成本。”
    reason: str = Field(min_length=1)

class AcceptedEvidence(BaseModel):
    """保存通过检查的证据卡及其实际检查结果。"""

    evidence: EvidenceCard

    # 引文定位检查结果。
    # Gate 只有在 valid=True 时才创建通过记录。
    citation_check: CitationCheck

    # 通过记录必须包含论断支持检查结果。
    # Gate 只有在 verdict="supported" 时才接受证据。
    # 这里保存检查器实际返回的理由。
    claim_support_check: ClaimSupportCheck

class RejectedEvidence(BaseModel):
    """保存被拒绝的证据卡及其检查结果。"""

    evidence: EvidenceCard
    citation_check: CitationCheck

    # 如果引文定位就失败，不再调用模型检查论断，
    # 因此这里允许为 None，表示“尚未检查”。
    #
    # 如果引文定位通过，但论断支持检查失败，
    # 这里会保存 unsupported 或 uncertain 及其理由。
    claim_support_check: ClaimSupportCheck | None = None

class EvidenceGateResult(BaseModel):
    """保存证据检查后的通过明细和拒绝明细。"""

    # 通过的卡片及其完整检查结果。
    # 这是后续保存通过证据时使用的数据。
    accepted_details: list[AcceptedEvidence] = Field(
        default_factory=list,
    )

    # 拒绝记录本来就包含检查结果，继续沿用。
    rejected: list[RejectedEvidence] = Field(
        default_factory=list,
    )

    @computed_field
    @property
    def accepted(self) -> list[EvidenceCard]:
        """从通过明细中取得证据卡，供现有调用方使用。"""

        # property 让调用方可以写 result.accepted，
        # 而不需要写成 result.accepted()。
        #
        # computed_field 让 Pydantic 在生成 JSON 时，
        # 也输出这个计算得到的 accepted 字段。
        #
        # accepted 自动由明细生成，
        # 因此添加通过记录时，只需维护 accepted_details。
        return [
            detail.evidence
            for detail in self.accepted_details
        ]

class ChunkSearchResult(BaseModel):
    """
    一条文本块检索结果。

    将正文及定位信息与检索分数组合起来，
    方便后续展示结果和融合不同检索方式。
    """
    # 复用已有 PaperChunk，保留正文、论文 ID、
    # 页码和块序号，避免只返回一段无法溯源的文本。

    chunk: PaperChunk

    # 全文检索相关性分数，数值越大越靠前。
    # 它不是模型置信度，也不是结论正确的概率。
    score: float = Field(ge=0)




