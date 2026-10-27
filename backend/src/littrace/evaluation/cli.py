"""用法：python -m littrace.evaluation.cli --case evals/my_case.json --run-id UUID。"""

import argparse
import json
from pathlib import Path
from uuid import UUID

from littrace.evaluation.offline import EvalCase, evaluate_run


def main() -> None:
    parser = argparse.ArgumentParser(description="只读地评估已有 LitTrace 研究运行")
    parser.add_argument("--case", type=Path, required=True, help="人工标注的 JSON 案例")
    parser.add_argument(
        "--run-id", type=UUID, action="append", required=True,
        help="已有研究运行 UUID；可多次传入以比较 Workflow 与 Agent",
    )
    args = parser.parse_args()

    # 延迟导入数据库连接：--help 不需要加载 .env 或连接 PostgreSQL。
    from littrace.database.session import SessionLocal
    from littrace.evaluation.from_database import load_eval_run

    case = EvalCase.model_validate_json(args.case.read_text(encoding="utf-8"))
    with SessionLocal() as session:
        results = [
            evaluate_run(case, load_eval_run(session, case, run_id)).model_dump(mode="json")
            for run_id in args.run_id
        ]
    print(json.dumps({"case_id": case.case_id, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
