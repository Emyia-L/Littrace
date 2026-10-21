"""把已确认且已定位来源的证据排版为 Markdown 报告草稿。"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ReportEvidence:
    """一条报告证据所需的最小信息。"""

    evidence_id: UUID
    claim: str
    quote: str
    original_filename: str
    page_number: int


@dataclass(frozen=True)
class ProjectReportEvidence:
    """项目级综述的一条证据，额外保留运行问题与关系类型。"""

    evidence_id: UUID
    question: str
    relation: str
    claim: str
    quote: str
    original_filename: str
    page_number: int
    run_id: UUID


@dataclass(frozen=True)
class ProjectReportComparison:
    """一对证据的最新人工比较决定，不包含模型推断。"""

    left_evidence_id: UUID
    right_evidence_id: UUID
    decision: str
    note: str | None


def render_report_draft(
    question: str,
    evidence: Sequence[ReportEvidence],
) -> str:
    """只排版已有论断和引文，不调用模型生成新的事实。"""
    lines = ["# 文献证据草稿", "", f"研究问题：{question}", ""]

    if not evidence:
        lines.append("暂无经过人工确认的证据。")

    for number, item in enumerate(evidence, start=1):
        lines.extend([
            f"## 证据 {number}",
            f"论断：{item.claim}",
            f"来源：{item.original_filename}，第 {item.page_number} 页",
            f"证据 ID：{item.evidence_id}",
            # 引文若跨多行，则每行都加 Markdown 引用标记。
            *[f"> {line}" for line in item.quote.splitlines()],
            "",
        ])

    return "\n".join(lines)


def render_project_report_draft(
    project_title: str,
    project_question: str,
    evidence: Sequence[ProjectReportEvidence],
    comparisons: Sequence[ProjectReportComparison] = (),
) -> str:
    """将多次运行中已确认的证据汇总为项目级 Markdown，不生成新结论。"""
    lines = [
        f"# 项目证据综述：{project_title}",
        "",
        f"项目研究问题：{project_question}",
        "",
        "本综述仅包含 Evidence Gate 接受、且最新人工复核决定为确认的证据。",
        "`supports/contradicts/mixed/insufficient` 描述证据相对于其所属运行的问题，",
        "不会依据该标签自动判定不同论文间关系；下方比较结论均为人工保存的决定。",
        "",
    ]

    if not evidence:
        lines.append("暂无经过人工确认的证据。")
        return "\n".join(lines)

    # 先按各次运行的问题分组，避免把针对不同问题的证据混成一个结论。
    grouped: dict[str, list[ProjectReportEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.question, []).append(item)

    for question, items in grouped.items():
        lines.extend([f"## 研究问题：{question}", ""])
        for number, item in enumerate(items, start=1):
            lines.extend([
                f"### 证据 {number}：{item.original_filename}，第 {item.page_number} 页",
                f"关系：{item.relation}",
                f"论断：{item.claim}",
                f"证据 ID：{item.evidence_id}",
                f"运行 ID：{item.run_id}",
                *[f"> {line}" for line in item.quote.splitlines()],
                "",
            ])

    # 单独列出人工比较结果，并回链到两条已收录证据，避免和原始证据混为一谈。
    if comparisons:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        decision_labels = {
            "mutually_support": "人工判断：相互支持",
            "conflicting": "人工判断：存在冲突",
            "inconclusive": "人工判断：尚不明确",
        }
        lines.extend(["## 人工跨论文比较", ""])
        for number, comparison in enumerate(comparisons, start=1):
            left = evidence_by_id.get(comparison.left_evidence_id)
            right = evidence_by_id.get(comparison.right_evidence_id)
            # 比较记录只在两个证据当前仍可进入综述时传入；此处再做防御性检查。
            if left is None or right is None:
                continue
            lines.extend([
                f"### 比较 {number}：{decision_labels.get(comparison.decision, comparison.decision)}",
                f"研究问题：{left.question}",
                f"论文 A：{left.original_filename}，第 {left.page_number} 页；论断：{left.claim}",
                f"论文 B：{right.original_filename}，第 {right.page_number} 页；论断：{right.claim}",
                f"证据 ID：{left.evidence_id} ↔ {right.evidence_id}",
                f"人工备注：{comparison.note or '未填写'}",
                "",
            ])

    return "\n".join(lines)
