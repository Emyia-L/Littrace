from typing import Literal, Optional
from pydantic import BaseModel, Field

class PageText(BaseModel):
    page_number: int = Field(ge = 1)
    text: str

class PaperChunk(BaseModel):

    chunk_id: str
    paper_id: str
    page_number: int = Field(ge = 1)    
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

class RejectedEvidence(BaseModel):
    evidence: EvidenceCard
    citation_check: CitationCheck

class EvidenceGateResult(BaseModel):
    accepted: list[EvidenceCard] = Field(default_factory=list)
    rejected: list[RejectedEvidence] = Field(default_factory=list)


