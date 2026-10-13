from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
)

from littrace.database.models.project import (
    ProjectStatus,
)

class ProjectCreate(BaseModel):
    """
    创建研究项目时接收的数据。
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )

    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
    )

    research_question: str = Field(
        min_length=1,
        max_length=5000,
    )

class ProjectResponse(BaseModel):
    """
    返回给 API 客户端的研究项目数据。
    """

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: UUID
    title: str
    research_question: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime