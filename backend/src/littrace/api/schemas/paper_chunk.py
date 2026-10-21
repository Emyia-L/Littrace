from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

class PaperChunkResponse(BaseModel):
    """
    查询论文文本块时返回的数据。

    这是 API 层的数据契约：
    前端可以依靠这些字段展示正文和定位引用。
    """

    model_config = ConfigDict(
        # 数据库查询结果是 SQLAlchemy ORM 对象。
        # 开启该选项后，Pydantic 可以从对象属性
        # 读取 chunk_id、text 等字段。
        from_attributes=True,
    )

    # 唯一定位一个文本块。
    # 后续 Evidence Card 使用它关联证据原文。
    chunk_id: str

    # 标识文本块属于哪篇论文。
    paper_id: UUID

    # 原始 PDF 页码，从 1 开始。
    page_number: int = Field(ge=1)

    # 当前页中的块序号，从 1 开始。
    chunk_index: int = Field(ge=1)

    # 切块器保存的正文，已规范化连续空白。
    # 它用于文本展示和引用验证，
    # 不包含 PDF 字体、坐标等版面信息。
    text: str

    # 保存文本时计算的 SHA-256。
    content_hash: str