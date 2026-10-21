"""register synthesis as an agent tool

Revision ID: 9d61ea720b34
Revises: 8b47dc510a2f
Create Date: 2026-10-17 16:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9d61ea720b34"
down_revision: Union[str, Sequence[str], None] = "8b47dc510a2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_agent_steps_action_kind", "agent_steps", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_steps_action_kind",
        "agent_steps",
        "action_kind IN ('search', 'extract_evidence', 'synthesize_project', 'finish')",
    )
    op.add_column(
        "project_syntheses",
        sa.Column("triggered_by_run_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_syntheses_triggered_by_run",
        "project_syntheses",
        "research_runs",
        ["triggered_by_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_project_syntheses_triggered_by_run_id"),
        "project_syntheses",
        ["triggered_by_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_project_syntheses_triggered_by_run_id"),
        table_name="project_syntheses",
    )
    op.drop_constraint(
        "fk_project_syntheses_triggered_by_run",
        "project_syntheses",
        type_="foreignkey",
    )
    op.drop_column("project_syntheses", "triggered_by_run_id")
    op.drop_constraint(
        "ck_agent_steps_action_kind", "agent_steps", type_="check"
    )
    op.create_check_constraint(
        "ck_agent_steps_action_kind",
        "agent_steps",
        "action_kind IN ('search', 'extract_evidence', 'finish')",
    )
