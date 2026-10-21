from pydantic import BaseModel, Field


class EvidenceRequest(BaseModel):
    """请求 LitTrace 为一个研究问题提取可追溯证据。"""

    # 用户想从当前项目论文中回答的具体问题。
    question: str = Field(
        min_length=1,
        max_length=1000,
        description="需要从论文中查找证据的研究问题",
    )

    # 混合检索先找多少个候选文本块，再交给模型分析。
    top_k: int = Field(
        default=8,
        ge=1,
        le=100,
        description="送入证据提取流程的候选文本块数量",
    )

    # 限制模型最多返回多少张 Evidence Card。
    max_cards: int = Field(
        default=3,
        ge=1,
        le=10,
        description="最多生成的证据卡数量",
    )