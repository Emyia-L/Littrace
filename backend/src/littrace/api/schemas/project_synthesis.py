"""项目级自动比较与综合接口结构。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from littrace.api.schemas.run_evaluation import ModelUsageSummary
from littrace.research.synthesis_models import ProjectSynthesisResult


class ProjectSynthesisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # 留空时使用项目的 research_question。
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    max_sources: int = Field(default=12, ge=2, le=20)
    max_points: int = Field(default=5, ge=1, le=8)


class SynthesisSourceResponse(BaseModel):
    evidence_id: UUID
    paper_id: UUID
    original_filename: str
    page_number: int
    claim: str
    quote: str


class ProjectSynthesisResponse(BaseModel):
    id: UUID
    project_id: UUID
    triggered_by_run_id: UUID | None
    question: str
    model: str
    sources: list[SynthesisSourceResponse]
    result: ProjectSynthesisResult
    model_usage: ModelUsageSummary
    created_at: datetime
