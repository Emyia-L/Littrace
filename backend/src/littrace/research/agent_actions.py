from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# 一次证据提取最多向模型提供多少个文本块。
MAX_EVIDENCE_CHUNKS = 8

class SearchAction(BaseModel):
    """选择一种检索方式，寻找与研究问题相关的文本块。"""

    # 拒绝模型额外编造的参数；自动去掉字符串两端空白。
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True
    )

    # kind 告诉程序这是哪一种动作。
    kind: Literal["search"]

    # 三种选择将分别对应现有的全文、向量、混合检索函数。
    strategy: Literal["keyword", "semantic", "hybrid"]

    # Agent 可以根据上一轮看到的结果调整检索词。
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=8, ge=1, le=100)

class  ExtractEvidenceAction(BaseModel):
    """从已检索到的文本块中提取并检查证据。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["extract_evidence"]

    # 这里只保存模型想使用的块 ID。
    # 后续执行工具时，程序必须核对这些 ID 确实属于当前项目
    # 且已经出现在本次运行的检索结果中。
    chunk_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_CHUNKS,
    )
    max_cards: int = Field(default=3, ge=1, le=10)


class SynthesizeProjectAction(BaseModel):
    """使用项目中既有的人工确认证据执行跨论文综合。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["synthesize_project"]
    # 模型可以缩小预算，但不能突破服务端和数据模型规定的上限。
    max_sources: int = Field(default=12, ge=2, le=20)
    max_points: int = Field(default=5, ge=1, le=8)

class FinishAction(BaseModel):
    """模型决定结束本次研究循环。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["finish"]

    # 简短记录停止原因；它不是论文结论或证据。
    reason: str = Field(min_length=1, max_length=500)

# discriminator 根据 JSON 中的 kind，选择对应的动作模型。
# 例如 kind="search" 时，必须同时满足 SearchAction 的字段规则。
AgentAction = Annotated[
    SearchAction | ExtractEvidenceAction | SynthesizeProjectAction | FinishAction,
    Field(discriminator="kind"),
]

class AgentDecision(BaseModel):
    """模型一轮只选择一个动作；执行后再观察结果并决定下一轮。"""

    model_config = ConfigDict(extra="forbid")

    action: AgentAction
