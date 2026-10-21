"""通过真实 HTTP API 验证 Agent、模型服务和数据库的完整链路。"""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

import httpx


def main() -> None:
    # Windows 重定向输出时也使用 UTF-8，避免中文在终端或日志中乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="验证 LitTrace 的真实 Agent 执行链路")
    # 先校验 UUID 格式，避免创建无效的接口请求。
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--question",
        default="这篇论文主要解决什么问题？请查找并验证原文证据。",
    )
    parser.add_argument("--output", type=Path, help="可选：将本次运行快照保存为 JSON 文件")
    args = parser.parse_args()

    project_path = f"/projects/{args.project_id}"
    # execute 目前同步运行；规划、提取和验证可能需要多次模型调用。
    with httpx.Client(
        base_url=args.base_url,
        timeout=httpx.Timeout(30.0, read=600.0),
    ) as client:
        papers_response = client.get(f"{project_path}/papers")
        papers_response.raise_for_status()
        if not any(paper["status"] == "parsed" for paper in papers_response.json()):
            raise SystemExit("请先向该项目上传并解析至少一篇 PDF。")

        # 每次使用新 run_id，保留之前的成功或失败记录。
        created = client.post(
            f"{project_path}/runs",
            json={"question": args.question, "top_k": 8, "max_cards": 3},
        )
        created.raise_for_status()
        run_id = created.json()["id"]
        run_path = f"{project_path}/runs/{run_id}"
        print(f"本次 run_id：{run_id}", flush=True)

        execution_status = None
        request_error = None
        try:
            executed = client.post(
                f"{run_path}/execute",
                params={"execution_mode": "agent"},
            )
            execution_status = executed.status_code
        except httpx.RequestError as exc:
            # 请求中断时服务端可能仍在执行，不能自动重发执行请求。
            request_error = type(exc).__name__

        snapshot = {
            "execute_http_status": execution_status,
            "request_error": request_error,
        }
        # execute 即使返回 502，也读取数据库保存的状态和失败前轨迹。
        for name, suffix in (("run", ""), ("steps", "/steps"), ("evidence", "/evidence")):
            response = client.get(f"{run_path}{suffix}", timeout=30.0)
            response.raise_for_status()
            snapshot[name] = response.json()

        snapshot_json = json.dumps(snapshot, ensure_ascii=False, indent=2)
        print(snapshot_json)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(snapshot_json + "\n", encoding="utf-8")
            print(f"运行快照已保存：{args.output.resolve()}")

        run = snapshot["run"]
        steps = snapshot["steps"]
        if run["status"] != "completed":
            raise SystemExit(f"当前状态为 {run['status']}，请保留 run_id 排查。")
        if run["execution_mode"] != "agent":
            raise SystemExit("本次运行没有使用 agent 模式。")
        if run["agent_steps_used"] != len(steps):
            raise SystemExit("运行摘要步数与数据库轨迹数量不一致。")
        if [step["step_number"] for step in steps] != list(range(1, len(steps) + 1)):
            raise SystemExit("数据库轨迹的步骤编号不连续。")

        kinds = [step["action_kind"] for step in steps]
        accepted_count = sum(
            record["decision"] == "accepted" for record in snapshot["evidence"]
        )
        print(f"动作顺序：{kinds}")
        print(f"通过验证的证据数量：{accepted_count}")
        if "search" not in kinds or "extract_evidence" not in kinds or not accepted_count:
            # Agent 可以合理地结束而没有证据，但这未覆盖本脚本要验证的主链路。
            raise SystemExit("本次运行结束，但尚未覆盖搜索、提取、接受证据的完整链路。")
        print("真实 Agent 冒烟测试通过。")


if __name__ == "__main__":
    main()
