from uuid import UUID

from pydantic import BaseModel, Field


class EvidenceSourceResponse(BaseModel):
    """已接受证据在原始 PDF 中的定位信息。"""

    evidence_id: UUID
    paper_id: UUID
    original_filename: str
    page_number: int = Field(ge=1)
    chunk_id: str
    quote: str

    # 指向现有的 PDF 下载接口；不把服务器上的存储路径交给客户端。
    file_url: str
