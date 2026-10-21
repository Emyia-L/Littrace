"""跨论文综合在模型输出、验证和 API 之间共享的数据结构。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from littrace.domain.models import ClaimSupportCheck


SynthesisRelationship = Literal[
    "agreement", "conflict", "complementary", "inconclusive"
]


class GeneratedSynthesisPoint(BaseModel):
    """模型提出的候选比较点；source_labels 仍需后端核验。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    conclusion: str = Field(min_length=1, max_length=2000)
    relationship: SynthesisRelationship
    source_labels: list[str] = Field(min_length=2, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("source_labels")
    @classmethod
    def labels_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source_labels must be unique")
        return value


class GeneratedSynthesis(BaseModel):
    """模型只能输出候选比较点，最终接受与否由服务端决定。"""

    model_config = ConfigDict(extra="forbid")
    points: list[GeneratedSynthesisPoint] = Field(min_length=1, max_length=8)


class AcceptedSynthesisPoint(BaseModel):
    title: str
    conclusion: str
    relationship: SynthesisRelationship
    source_evidence_ids: list[UUID]
    limitations: list[str]
    support_check: ClaimSupportCheck


class RejectedSynthesisPoint(BaseModel):
    title: str
    conclusion: str
    relationship: SynthesisRelationship
    source_evidence_ids: list[UUID]
    limitations: list[str]
    rejection_reason: str
    support_check: ClaimSupportCheck | None = None


class ProjectSynthesisResult(BaseModel):
    accepted: list[AcceptedSynthesisPoint] = Field(default_factory=list)
    rejected: list[RejectedSynthesisPoint] = Field(default_factory=list)
