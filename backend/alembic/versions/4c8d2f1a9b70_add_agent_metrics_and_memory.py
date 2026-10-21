"""add agent metrics and cross-run memory

Revision ID: 4c8d2f1a9b70
Revises: f2a17c91b630
Create Date: 2026-10-17 11:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c8d2f1a9b70"
down_revision: Union[str, Sequence[str], None] = "f2a17c91b630"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_runs", sa.Column("model_call_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("research_runs", sa.Column("model_prompt_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("research_runs", sa.Column("model_completion_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("research_runs", sa.Column("model_total_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("research_runs", sa.Column("model_duration_ms", sa.Integer(), server_default="0", nullable=False))
    op.add_column("research_runs", sa.Column("estimated_cost_microusd", sa.BigInteger(), server_default="0", nullable=False))
    op.create_check_constraint(
        "ck_research_runs_model_metrics_nonnegative",
        "research_runs",
        "model_call_count >= 0 AND model_prompt_tokens >= 0 AND model_completion_tokens >= 0 "
        "AND model_total_tokens >= 0 AND model_duration_ms >= 0 AND estimated_cost_microusd >= 0",
    )
    op.create_table(
        "research_run_memories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("source_evidence_id", sa.UUID(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_evidence_id"], ["evidence_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_evidence_id", name="uq_research_run_memories_run_evidence"),
    )
    op.create_index(op.f("ix_research_run_memories_run_id"), "research_run_memories", ["run_id"], unique=False)
    op.create_index(op.f("ix_research_run_memories_source_evidence_id"), "research_run_memories", ["source_evidence_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_research_run_memories_source_evidence_id"), table_name="research_run_memories")
    op.drop_index(op.f("ix_research_run_memories_run_id"), table_name="research_run_memories")
    op.drop_table("research_run_memories")
    op.drop_constraint("ck_research_runs_model_metrics_nonnegative", "research_runs", type_="check")
    op.drop_column("research_runs", "estimated_cost_microusd")
    op.drop_column("research_runs", "model_duration_ms")
    op.drop_column("research_runs", "model_total_tokens")
    op.drop_column("research_runs", "model_completion_tokens")
    op.drop_column("research_runs", "model_prompt_tokens")
    op.drop_column("research_runs", "model_call_count")
