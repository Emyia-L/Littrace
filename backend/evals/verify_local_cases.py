"""核验本机样本 PDF、引文和答案标注；不访问模型或数据库。

在 backend 目录执行：uv run python -X utf8 -m evals.verify_local_cases
真实 PDF 被 Git 忽略，因此其他机器复现时需要先准备完全相同的 PDF 文件。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from littrace.evaluation.offline import EvalCase, normalize_quote
from littrace.ingestion.chunker import chunk_pages
from littrace.ingestion.pdf_parser import extract_pages


EVALS_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = EVALS_DIR.parent / "samples"


def main() -> int:
    catalog = json.loads((EVALS_DIR / "paper_catalog.json").read_text(encoding="utf-8"))
    rubric_data = json.loads((EVALS_DIR / "answer_rubrics.json").read_text(encoding="utf-8"))
    challenge_data = json.loads((EVALS_DIR / "claim_challenges.json").read_text(encoding="utf-8"))
    scenario_data = json.loads((EVALS_DIR / "agent_behavior_scenarios.json").read_text(encoding="utf-8"))
    rubric_items = rubric_data["cases"]
    rubrics = {item["case_id"]: item for item in rubric_items}
    pdf_names = {item["file"] for item in catalog["papers"]}
    actual_names = {path.name for path in SAMPLES_DIR.glob("*.pdf")}
    errors: list[str] = []
    if len(rubrics) != len(rubric_items):
        errors.append("答案标准中存在重复的 case_id")
    challenge_ids = [item["id"] for item in challenge_data["items"]]
    scenario_ids = [item["id"] for item in scenario_data["scenarios"]]
    if len(set(challenge_ids)) != len(challenge_ids):
        errors.append("论断挑战中存在重复 ID")
    if len(set(scenario_ids)) != len(scenario_ids):
        errors.append("Agent 场景中存在重复 ID")

    # 快照里的论文集应该与本机 samples 一致；新增 PDF 时先更新目录和标注计划。
    if pdf_names != actual_names:
        errors.append(f"PDF 清单不一致：目录缺少 {sorted(pdf_names - actual_names)}；未收录 {sorted(actual_names - pdf_names)}")

    # 先核对原始文件哈希，再用项目真实的解析和切块逻辑建立可定位文本块。
    chunks_by_hash: dict[str, list] = {}
    for item in catalog["papers"]:
        path = SAMPLES_DIR / item["file"]
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            errors.append(f"{path.name}: PDF SHA-256 与目录不符")
            continue
        pages = extract_pages(path)
        chunks = chunk_pages(pages, paper_id=digest,
                             max_chars=catalog["chunker"]["max_chars"],
                             overlap=catalog["chunker"]["overlap"])
        if len(pages) != item["page_count"] or len(chunks) != item["chunk_count"]:
            errors.append(f"{path.name}: 页数或块数与目录不符")
        chunks_by_hash[digest] = chunks

    seen_case_ids: set[str] = set()
    case_paths = sorted(EVALS_DIR.glob("*.json"))
    case_paths = [path for path in case_paths if path.name not in {
        "paper_catalog.json", "answer_rubrics.json",
        "claim_challenges.json", "agent_behavior_scenarios.json",
    }]
    cases_by_id: dict[str, EvalCase] = {}
    for path in case_paths:
        try:
            case = EvalCase.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: 案例格式无效：{exc}")
            continue
        if case.case_id in seen_case_ids:
            errors.append(f"{path.name}: 重复的 case_id {case.case_id}")
        seen_case_ids.add(case.case_id)
        cases_by_id[case.case_id] = case
        rubric = rubrics.get(case.case_id)
        if rubric is None:
            errors.append(f"{path.name}: 缺少答案标准")
            continue
        expected = rubric.get("expected_behavior")
        points = rubric.get("must_cover", [])
        if expected == "answer":
            if not case.gold_quotes or not points:
                errors.append(f"{path.name}: 可回答案例缺少引文或答案要点")
            used_indices = {index for point in points
                            for index in point.get("quote_indices", [])}
            if used_indices != set(range(1, len(case.gold_quotes) + 1)):
                errors.append(f"{path.name}: 答案要点没有恰好覆盖所有引文序号")
            if len(case.paper_sha256) > 1 and (
                not rubric.get("expected_relation") or not rubric.get("comparison_limitation")
            ):
                errors.append(f"{path.name}: 跨论文案例缺少关系或可比性限制")
        elif expected == "insufficient_evidence":
            if case.gold_quotes or points or not rubric.get("abstention_reason"):
                errors.append(f"{path.name}: 缺证据案例不应有 gold，引文和弃答理由需一致")
        else:
            errors.append(f"{path.name}: 未知的 expected_behavior {expected!r}")
        if not rubric.get("must_not_claim"):
            errors.append(f"{path.name}: 缺少禁止无据声称的标注")
        seen_quotes: set[tuple[str, int, str]] = set()
        for gold in case.gold_quotes:
            normalized = normalize_quote(gold.quote)
            key = (gold.paper_sha256, gold.page_number, normalized)
            if key in seen_quotes:
                errors.append(f"{path.name}: 重复的 gold 引文：第 {gold.page_number} 页")
            seen_quotes.add(key)
            # 引文必须完整落在指定论文、指定 PDF 页的单个块内。
            matches = [chunk for chunk in chunks_by_hash.get(gold.paper_sha256, [])
                       if chunk.page_number == gold.page_number
                       and normalized in normalize_quote(chunk.text)]
            if not matches:
                errors.append(f"{path.name}: 第 {gold.page_number} 页引文无法在单个文本块中定位：{gold.quote[:60]}")
        if len(case.paper_sha256) > 1 and case.gold_quotes:
            quoted_papers = {gold.paper_sha256 for gold in case.gold_quotes}
            if quoted_papers != set(case.paper_sha256):
                errors.append(f"{path.name}: 跨论文案例未覆盖每篇论文")
        print(f"{path.name}: {len(case.gold_quotes)} 条引文，语料 {len(case.paper_sha256)} 篇")

    if seen_case_ids != set(rubrics):
        errors.append(f"案例与答案标准 ID 不一致：仅在案例中 {sorted(seen_case_ids - set(rubrics))}；仅在答案标准中 {sorted(set(rubrics) - seen_case_ids)}")

    # 挑战样本通过案例 ID + 引文序号引用真实来源，不复制原文，避免两份标注漂移。
    for item in challenge_data["items"]:
        case = cases_by_id.get(item["source_case_id"])
        index = item["quote_index"]
        if case is None or not 1 <= index <= len(case.gold_quotes):
            errors.append(f"{item['id']}: 无法定位来源案例或引文序号")
        if item.get("expected_verdict") not in {"supported", "unsupported", "uncertain"}:
            errors.append(f"{item['id']}: 预期支持裁决无效")
        if not item.get("claim") or not item.get("reason"):
            errors.append(f"{item['id']}: 缺少论断或标注理由")
    for item in scenario_data["scenarios"]:
        if not item.get("metric") or not item.get("given") or not item.get("expected"):
            errors.append(f"{item['id']}: Agent 场景缺少指标、前置条件或期望")

    if errors:
        for error in errors:
            print(f"错误：{error}")
        return 1
    print(f"核验通过：{len(catalog['papers'])} 篇 PDF、{len(case_paths)} 个案例、{len(challenge_ids)} 个论断挑战、{len(scenario_ids)} 个 Agent 场景。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
