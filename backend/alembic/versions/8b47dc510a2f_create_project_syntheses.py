"""create project syntheses

Revision ID: 8b47dc510a2f
Revises: 4c8d2f1a9b70
Create Date: 2026-10-17 15:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "8b47dc510a2f"
down_revision: Union[str, Sequence[str], None] = "4c8d2f1a9b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_syntheses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("source_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("model_call_count", sa.Integer(), nullable=False),
        sa.Column("model_prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("model_completion_tokens", sa.Integer(), nullable=False),
        sa.Column("model_total_tokens", sa.Integer(), nullable=False),
        sa.Column("model_duration_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_syntheses_project_id"),
        "project_syntheses", ["project_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_project_syntheses_project_id"),
        table_name="project_syntheses",
    )
    op.drop_table("project_syntheses")
