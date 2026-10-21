"""Track research run retries.

Revision ID: e081d3a74c29
Revises: 9668044b1c94
"""

from alembic import op
import sqlalchemy as sa


revision: str = "e081d3a74c29"
down_revision: str | None = "9668044b1c94"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 允许 NULL，现有运行会保留为“没有重试来源”。
    op.add_column(
        "research_runs",
        sa.Column("retry_of_run_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_research_runs_retry_of",
        "research_runs",
        "research_runs",
        ["retry_of_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_research_runs_retry_of_run_id",
        "research_runs",
        ["retry_of_run_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_research_runs_not_self_retry",
        "research_runs",
        "retry_of_run_id IS NULL OR retry_of_run_id <> id",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_research_runs_not_self_retry", "research_runs", type_="check"
    )
    op.drop_index("ix_research_runs_retry_of_run_id", table_name="research_runs")
    op.drop_constraint(
        "fk_research_runs_retry_of", "research_runs", type_="foreignkey"
    )
    op.drop_column("research_runs", "retry_of_run_id")
