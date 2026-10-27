"""LitTrace 第一版确定性离线评测，不调用模型或修改数据库。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from littrace.evidence.citation_validator import normalize_text


class GoldQuote(BaseModel):
    """人工标注的关键引文；用文件哈希而不是易变化的论文 UUID 定位。"""

    model_config = ConfigDict(extra="forbid")

    paper_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    # 第一版要求标注一段完整、且在同一文本块内的连续文字。
    quote: str = Field(min_length=20)

    @model_validator(mode="after")
    def validate_quote(self) -> GoldQuote:
        if len(normalize_text(self.quote)) < 20:
            raise ValueError("Gold quote must contain at least 20 normalized characters")
        return self


class EvalCase(BaseModel):
    """一次评测输入；无答案的题目可让 gold_quotes 为空。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    paper_sha256: list[str] = Field(min_length=1)
    gold_quotes: list[GoldQuote] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_corpus(self) -> EvalCase:
        hashes = self.paper_sha256
        if len(hashes) != len(set(hashes)) or any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in hashes
        ):
            raise ValueError("paper_sha256 must contain unique lowercase SHA-256 values")
        if any(gold.paper_sha256 not in hashes for gold in self.gold_quotes):
            raise ValueError("Every gold quote must belong to the case corpus")
        return self


class ObservedChunk(BaseModel):
    """Agent 轨迹中实际展示给规划器的文本块。"""

    paper_sha256: str
    page_number: int
    text: str


class EvaluatedEvidence(BaseModel):
    """已保存证据的评分所需字段，而不是完整论文或模型响应。"""

    decision: Literal["accepted", "rejected"]
    paper_sha256: str | None
    page_number: int
    quote: str
    citation_valid: bool
    support_verdict: Literal["supported", "unsupported", "uncertain"] | None
    source_text: str | None


class EvaluatedStep(BaseModel):
    kind: str
    skipped_duplicate_search: bool = False
    empty_search: bool = False


class EvalRun(BaseModel):
    run_id: UUID
    execution_mode: Literal["workflow", "agent"]
    status: str
    agent_stop_reason: str | None
    model_call_count: int
    model_total_tokens: int
    model_duration_ms: int
    elapsed_ms: int | None
    estimated_cost_microusd: int
    observed_chunks: list[ObservedChunk] = Field(default_factory=list)
    evidence: list[EvaluatedEvidence] = Field(default_factory=list)
    steps: list[EvaluatedStep] = Field(default_factory=list)


class EvalResult(BaseModel):
    """None 表示没有基准或没有轨迹，绝不混同于 0 分。"""

    case_id: str
    run_id: UUID
    execution_mode: str
    status: str
    gold_quote_count: int
    observed_gold_recall: float | None
    accepted_gold_recall: float | None
    accepted_count: int
    citation_invariant_violations: int
    duplicate_search_count: int
    empty_search_count: int
    step_limit_reached: bool
    model_call_count: int
    model_total_tokens: int
    model_duration_ms: int
    elapsed_ms: int | None
    estimated_cost_microusd: int


def normalize_quote(value: str) -> str:
    """复用生产引文检查的文本规范化，但不作模糊语义匹配。"""

    return normalize_text(value)


def _matches_gold(gold: GoldQuote, paper_sha256: str | None, page: int, text: str) -> bool:
    return (
        paper_sha256 == gold.paper_sha256
        and page == gold.page_number
        and normalize_quote(gold.quote) in normalize_quote(text)
    )


def evaluate_run(case: EvalCase, run: EvalRun) -> EvalResult:
    """用人工标注的引文锚点评估一次运行；不把 Gate 接受率当成准确率。"""

    accepted = [item for item in run.evidence if item.decision == "accepted"]
    valid_accepted = []
    violations = 0
    for item in accepted:
        quote = normalize_quote(item.quote)
        if (
            not item.citation_valid
            or item.support_verdict != "supported"
            or item.source_text is None
            or not quote
            or quote not in normalize_quote(item.source_text or "")
        ):
            violations += 1
        else:
            valid_accepted.append(item)
    # 违反基本引文约束的卡片不能获得 gold 命中分，即使恰巧包含标注文本。
    gold_count = len(case.gold_quotes)

    # Workflow 不保存逐步搜索观察，所以这里不能把缺失轨迹计为零召回。
    observed_recall = None
    if run.execution_mode == "agent" and gold_count:
        observed_recall = sum(
            any(_matches_gold(gold, chunk.paper_sha256, chunk.page_number, chunk.text)
                for chunk in run.observed_chunks)
            for gold in case.gold_quotes
        ) / gold_count

    accepted_recall = None
    if gold_count:
        accepted_recall = sum(
            any(_matches_gold(gold, item.paper_sha256, item.page_number, item.quote)
                for item in valid_accepted)
            for gold in case.gold_quotes
        ) / gold_count

    # 这是“数据库状态是否违反 Gate 约束”，不是人工标注的事实错误率。
    return EvalResult(
        case_id=case.case_id,
        run_id=run.run_id,
        execution_mode=run.execution_mode,
        status=run.status,
        gold_quote_count=gold_count,
        observed_gold_recall=observed_recall,
        accepted_gold_recall=accepted_recall,
        accepted_count=len(accepted),
        citation_invariant_violations=violations,
        duplicate_search_count=sum(step.skipped_duplicate_search for step in run.steps),
        empty_search_count=sum(step.empty_search for step in run.steps),
        step_limit_reached=run.agent_stop_reason == "step_limit",
        model_call_count=run.model_call_count,
        model_total_tokens=run.model_total_tokens,
        model_duration_ms=run.model_duration_ms,
        elapsed_ms=run.elapsed_ms,
        estimated_cost_microusd=run.estimated_cost_microusd,
    )
