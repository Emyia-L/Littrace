"""检查可提交的评测标注结构；不依赖被 Git 忽略的 samples/*.pdf。"""

import json
from pathlib import Path

from littrace.evaluation.offline import EvalCase


EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
SUPPORT_FILES = {
    "paper_catalog.json", "answer_rubrics.json", "claim_challenges.json",
    "agent_behavior_scenarios.json",
}


def _cases() -> dict[str, EvalCase]:
    """只把 EvalCase JSON 当作题目；目录/答案标准属于不同数据结构。"""
    paths = sorted(path for path in EVALS_DIR.glob("*.json")
                   if path.name not in SUPPORT_FILES)
    cases = [EvalCase.model_validate_json(path.read_text(encoding="utf-8"))
             for path in paths]
    assert len({case.case_id for case in cases}) == len(cases)
    return {case.case_id: case for case in cases}


def test_case_set_contains_answer_and_abstention_examples() -> None:
    cases = _cases()
    assert len(cases) == 20
    assert sum(bool(case.gold_quotes) for case in cases.values()) == 17
    assert sum(not case.gold_quotes for case in cases.values()) == 3
    assert sum(len(case.gold_quotes) for case in cases.values()) == 56


def test_answer_rubrics_cover_every_case_and_quote() -> None:
    cases = _cases()
    data = json.loads((EVALS_DIR / "answer_rubrics.json").read_text(encoding="utf-8"))
    rubrics = data["cases"]
    assert len(rubrics) == len(cases)
    assert {item["case_id"] for item in rubrics} == set(cases)
    for item in rubrics:
        case = cases[item["case_id"]]
        if item["expected_behavior"] == "answer":
            used = {index for point in item["must_cover"]
                    for index in point["quote_indices"]}
            assert used == set(range(1, len(case.gold_quotes) + 1))
            assert item["must_not_claim"]
            if len(case.paper_sha256) > 1:
                assert item["expected_relation"]
                assert item["comparison_limitation"]
        else:
            assert item["expected_behavior"] == "insufficient_evidence"
            assert not case.gold_quotes
            assert not item["must_cover"]
            assert item["abstention_reason"]


def test_challenges_and_agent_scenarios_have_valid_references() -> None:
    cases = _cases()
    challenges = json.loads((EVALS_DIR / "claim_challenges.json").read_text(encoding="utf-8"))["items"]
    scenarios = json.loads((EVALS_DIR / "agent_behavior_scenarios.json").read_text(encoding="utf-8"))["scenarios"]
    assert len(challenges) == 6
    assert len(scenarios) == 10
    assert len({item["id"] for item in challenges}) == len(challenges)
    assert len({item["id"] for item in scenarios}) == len(scenarios)
    for item in challenges:
        case = cases[item["source_case_id"]]
        assert 1 <= item["quote_index"] <= len(case.gold_quotes)
        assert item["expected_verdict"] in {"supported", "unsupported", "uncertain"}
    for item in scenarios:
        assert item["metric"] and item["given"] and item["expected"]
