from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from littrace.database.models.paper import (
    PaperStatus,
)


class PaperResponse(BaseModel):
    """
    论文上传成功或查询论文时返回的数据。

    这里不返回 storage_key，
    因为服务器内部文件路径不应该暴露给客户端。
    """

    model_config = ConfigDict(
        # 允许 Pydantic 从 SQLAlchemy ORM
        # 对象的属性中读取字段。
        from_attributes=True,
    )

    id: UUID
    project_id: UUID
    original_filename: str
    content_hash: str
    status: PaperStatus

    # 文件刚上传但尚未解析时，
    # page_count 可以暂时为 None。
    page_count: int | None

    created_at: datetime
    updated_at: datetime