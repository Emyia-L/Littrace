"""Read-only replay of the first search observation from a failed Agent run.

Run from backend: uv run python -u -X utf8 -m evals.probe_model_pipeline --run-id UUID
It makes fresh model calls, but does not create or change projects, runs or evidence.
"""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from openai import OpenAI
from sqlalchemy import select

from littrace.config import get_settings
from littrace.database.models.agent_step import AgentStepRecord
from littrace.database.models.research_run import ResearchRun
from littrace.database.session import SessionLocal
from littrace.evidence.citation_validator import validate_quote
from littrace.evidence import extractor
from littrace.research import agent_planner
from littrace.research.agent_actions import ExtractEvidenceAction, SearchAction
from littrace.research.agent_tools import resolve_evidence_chunks
from littrace.research.model_usage import (
    ModelUsageCollector,
    activate_model_usage,
    reset_model_usage,
)


_PROBE_TIMEOUT_SECONDS = 180.0


def probe_client() -> OpenAI:
    """Keep the production 180-second timeout, but disable retry for diagnosis."""
    settings = get_settings()
    return OpenAI(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        timeout=_PROBE_TIMEOUT_SECONDS,
        max_retries=0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Agent planner/extractor latency")
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--extractor-only", action="store_true", help="Skip planner and use the first three visible chunks")
    parser.add_argument("--extractor-model", help="Probe model name; does not change app configuration")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if not 5 <= args.timeout_seconds <= 180:
        parser.error("--timeout-seconds must be between 5 and 180")
    global _PROBE_TIMEOUT_SECONDS
    _PROBE_TIMEOUT_SECONDS = args.timeout_seconds

    # Override only this probe process, not the application configuration.
    agent_planner.create_model_client = probe_client
    extractor.create_model_client = probe_client
    if args.extractor_model:
        # Only the extractor in this short-lived probe uses the alternate model.
        extractor.get_settings = lambda: get_settings().model_copy(
            update={"deepseek_model": args.extractor_model}
        )
    collector = ModelUsageCollector()
    token = activate_model_usage(collector)
    try:
        with SessionLocal() as session:
            run = session.get(ResearchRun, args.run_id)
            if run is None:
                raise ValueError("Run does not exist")
            question = run.question
            project_id = run.project_id
            run_max_cards = run.max_cards
            step = session.scalar(
                select(AgentStepRecord)
                .where(AgentStepRecord.run_id == args.run_id, AgentStepRecord.step_number == 1)
            )
            if step is None or step.action_kind != "search" or not step.observation_summary:
                raise ValueError("Run has no first search observation")
            observation = json.loads(step.observation_summary)
            visible_ids = {match["chunk_id"] for match in observation.get("matches", [])}
            recent_action = SearchAction.model_validate(step.action_payload)
            print(
                f"Replaying after search: visible_chunks={len(visible_ids)} "
                f"planner_model={get_settings().deepseek_model} "
                f"extractor_model={args.extractor_model or get_settings().deepseek_model}",
                flush=True,
            )

            if args.extractor_only:
                selected_ids = [match["chunk_id"] for match in observation["matches"][:3]]
                action = ExtractEvidenceAction(
                    kind="extract_evidence", chunk_ids=selected_ids, max_cards=3
                )
                print("Planner skipped for extractor-only diagnostic", flush=True)
            else:
                decision = agent_planner.choose_next_action(
                    question=question,
                    observation_summary=step.observation_summary,
                    observed_chunk_ids=visible_ids,
                    remaining_steps=5,
                    accepted_count=0,
                    rejected_count=0,
                    recent_actions=[recent_action],
                    synthesis_readiness={"eligible": False, "confirmed_evidence_count": 0, "paper_count": 0},
                )
                action = decision.action
                print(f"Planner action: {action.kind}", flush=True)
            if isinstance(action, ExtractEvidenceAction):
                chunks = resolve_evidence_chunks(
                    session=session,
                    project_id=project_id,
                    action=action,
                    observed_chunk_ids=visible_ids,
                )
                session.rollback()  # Release the read-only transaction before the model request.
                print(
                    f"Extractor input: chunks={len(chunks)} "
                    f"characters={sum(len(chunk.text) for chunk in chunks)}",
                    flush=True,
                )
                cards = extractor.extract_evidence(
                    question=question,
                    chunks=chunks,
                    max_cards=min(action.max_cards, run_max_cards),
                )
                print(f"Extractor returned cards={len(cards)}", flush=True)
                chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
                valid_citations = sum(
                    validate_quote(evidence=card, chunks_by_id=chunks_by_id).valid
                    for card in cards
                )
                print(f"Deterministic citation checks passed={valid_citations}/{len(cards)}", flush=True)
    except Exception as exc:
        # Never print provider exception bodies or prompt contents.
        print(f"Probe failed: {type(exc).__name__}", flush=True)
    finally:
        reset_model_usage(token)
        for call in collector.calls:
            print(
                f"component={call.component} succeeded={call.succeeded} "
                f"duration_ms={call.duration_ms} total_tokens={call.total_tokens}",
                flush=True,
            )


if __name__ == "__main__":
    main()
