"""Run the existing PDF-backed cases through the real Agent and score saved runs.

Usage (from backend):
    uv run python -u -X utf8 -m evals.run_agent_benchmark --label local-agent-v1

The result file is resumable. Each corpus gets a separate project; cases sharing the
same corpus share only documents, not human-confirmed memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from littrace.api.app import app
from littrace.database.models.evidence import EvidenceRecord
from littrace.database.session import SessionLocal
from littrace.domain.models import EvidenceCard
from littrace.evaluation.from_database import load_eval_run
from littrace.evaluation.offline import EvalCase, evaluate_run
from littrace.config import get_settings
from littrace.research.executor import AGENT_MAX_STEPS


EVAL_DIR = Path(__file__).resolve().parent
RESULT_DIR = EVAL_DIR / "results"


def save_report(path: Path, report: dict) -> None:
    """Write after each API action so an interrupted benchmark can resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    report["updated_at_utc"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def api_json(client: TestClient, method: str, path: str, **kwargs) -> dict:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and score LitTrace Agent eval cases")
    parser.add_argument("--label", required=True, help="Safe local result filename, e.g. agent-20261022")
    parser.add_argument("--case-id", action="append", help="Optional case ID filter; repeat as needed")
    parser.add_argument("--rerun-failed", action="store_true", help="Create a new run after a failed one")
    parser.add_argument(
        "--continue-on-failure", action="store_true",
        help="Keep running other cases after a model or execution failure",
    )
    parser.add_argument(
        "--direct-execute", action="store_true",
        help="Call the same research executor directly so diagnostic errors are visible",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.label):
        parser.error("--label must use only letters, numbers, hyphen or underscore")

    cases = sorted(
        (
            EvalCase.model_validate_json(path.read_text(encoding="utf-8"))
            for path in EVAL_DIR.glob("*.json")
            if path.name not in {
                "paper_catalog.json", "answer_rubrics.json", "claim_challenges.json",
                "agent_behavior_scenarios.json",
            }
        ),
        key=lambda case: case.case_id,
    )
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.case_id in selected]
        if {case.case_id for case in cases} != selected:
            parser.error("Unknown --case-id value")

    catalog = json.loads((EVAL_DIR / "paper_catalog.json").read_text(encoding="utf-8"))
    paper_file_by_hash = {paper["sha256"]: paper["file"] for paper in catalog["papers"]}
    output = RESULT_DIR / f"{args.label}.json"
    report = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {
        "label": args.label,
        "dataset": "evals/*.json (AI-assisted first-pass labels)",
        "execution_mode": "agent",
        "top_k": 8,
        "max_cards": 3,
        "project_ids_by_corpus": {},
        "cases": {},
    }
    # Record the key runtime configuration alongside results; never store secrets.
    report.setdefault("chat_model", get_settings().deepseek_model)
    report.setdefault("embedding_model", get_settings().embedding_model)
    report.setdefault("agent_max_steps", AGENT_MAX_STEPS)
    save_report(output, report)

    with TestClient(app) as client:
        for index, case in enumerate(cases, 1):
            corpus = sorted(case.paper_sha256)
            corpus_key = ":".join(corpus)
            print(f"[{index}/{len(cases)}] {case.case_id}", flush=True)

            project_id = report["project_ids_by_corpus"].get(corpus_key)
            if project_id is None:
                project = api_json(client, "POST", "/projects", json={
                    "title": f"Eval {args.label} {hashlib.sha256(corpus_key.encode()).hexdigest()[:10]}",
                    "research_question": "Offline Agent evaluation corpus; no human-confirmed memory",
                })
                project_id = project["id"]
                report["project_ids_by_corpus"][corpus_key] = project_id
                save_report(output, report)

            existing = api_json(client, "GET", f"/projects/{project_id}/papers")
            uploaded_hashes = {paper["content_hash"] for paper in existing}
            for digest in corpus:
                if digest in uploaded_hashes:
                    continue
                filename = paper_file_by_hash[digest]
                path = EVAL_DIR.parent / "samples" / filename
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError(f"Local PDF hash mismatch: {filename}")
                print(f"  Uploading/indexing {filename}", flush=True)
                paper = api_json(client, "POST", f"/projects/{project_id}/papers", files={
                    "file": (filename, content, "application/pdf"),
                })
                uploaded_hashes.add(paper["content_hash"])

            entry = report["cases"].setdefault(case.case_id, {"project_id": project_id})
            run_id = entry.get("run_id")
            if run_id is not None and args.rerun_failed:
                previous = api_json(client, "GET", f"/projects/{project_id}/runs/{run_id}")
                if previous["status"] == "failed":
                    entry.setdefault("previous_run_ids", []).append(run_id)
                    entry.pop("run_id")
                    entry.pop("execution_error", None)
                    entry.pop("metrics", None)
                    entry.pop("evidence", None)
                    run_id = None
                    save_report(output, report)
            if run_id is None:
                run = api_json(client, "POST", f"/projects/{project_id}/runs", json={
                    "question": case.question,
                    "top_k": 8,
                    "max_cards": 3,
                })
                run_id = run["id"]
                entry["run_id"] = run_id
                save_report(output, report)
            else:
                run = api_json(client, "GET", f"/projects/{project_id}/runs/{run_id}")

            if run["status"] == "pending":
                print(f"  Running Agent {run_id}", flush=True)
                try:
                    if args.direct_execute:
                        # The API masks model errors as 502; this executes its underlying
                        # production function, while keeping the same database records.
                        from littrace.research.executor import execute_research_run

                        with SessionLocal() as session:
                            execute_research_run(
                                session=session,
                                project_id=UUID(project_id),
                                run_id=UUID(run_id),
                                execution_mode="agent",
                            )
                        run = api_json(client, "GET", f"/projects/{project_id}/runs/{run_id}")
                    else:
                        run = api_json(
                            client, "POST",
                            f"/projects/{project_id}/runs/{run_id}/execute?execution_mode=agent",
                        )
                except Exception as exc:
                    # Do not persist provider response bodies or credentials in reports.
                    entry["execution_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc)[:500] if isinstance(exc, ValueError) else "external or internal execution error",
                    }
                    print(f"  error={entry['execution_error']}", flush=True)
                    run = api_json(client, "GET", f"/projects/{project_id}/runs/{run_id}")
            entry["status"] = run["status"]
            if run["status"] == "completed":
                with SessionLocal() as session:
                    snapshot = load_eval_run(session, case, UUID(run_id))
                    entry["metrics"] = evaluate_run(case, snapshot).model_dump(mode="json")
                    records = session.scalars(
                        select(EvidenceRecord).where(EvidenceRecord.run_id == UUID(run_id))
                        .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
                    ).all()
                    entry["evidence"] = [
                        {
                            "decision": record.decision.value,
                            "claim": (card := EvidenceCard.model_validate(record.evidence)).claim,
                            "quote": card.quote,
                            "chunk_id": card.chunk_id,
                            "page_number": card.page_number,
                        }
                        for record in records
                    ]
                m = entry["metrics"]
                print(
                    f"  completed; observed={m['observed_gold_recall']} "
                    f"accepted={m['accepted_gold_recall']} calls={m['model_call_count']}",
                    flush=True,
                )
            else:
                print(f"  status={run['status']}", flush=True)
            save_report(output, report)
            if run["status"] == "failed" and not args.continue_on_failure:
                print("Pausing benchmark after failure; inspect the report before resuming.", flush=True)
                break

    print(f"Report: {output}", flush=True)


if __name__ == "__main__":
    main()
