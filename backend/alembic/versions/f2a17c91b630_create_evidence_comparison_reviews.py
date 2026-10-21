"""create evidence comparison reviews

Revision ID: f2a17c91b630
Revises: 64a44270d095
Create Date: 2026-10-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a17c91b630"
down_revision: Union[str, Sequence[str], None] = "64a44270d095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum 在 PostgreSQL 中是独立类型，先建表时一并创建。
    comparison_decision = sa.Enum(
        "mutually_support", "conflicting", "inconclusive",
        name="comparison_decision",
    )
    op.create_table(
        "evidence_comparison_reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("left_evidence_id", sa.UUID(), nullable=False),
        sa.Column("right_evidence_id", sa.UUID(), nullable=False),
        sa.Column(
            "review_order", sa.BigInteger(), sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("decision", comparison_decision, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "left_evidence_id <> right_evidence_id",
            name="ck_comparison_distinct_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["left_evidence_id"], ["evidence_records.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["right_evidence_id"], ["evidence_records.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_order", name="uq_comparison_review_order"),
    )
    op.create_index(
        "ix_evidence_comparison_reviews_project_id",
        "evidence_comparison_reviews", ["project_id"], unique=False,
    )
    op.create_index(
        "ix_evidence_comparison_reviews_left_evidence_id",
        "evidence_comparison_reviews", ["left_evidence_id"], unique=False,
    )
    op.create_index(
        "ix_evidence_comparison_reviews_right_evidence_id",
        "evidence_comparison_reviews", ["right_evidence_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_comparison_reviews_right_evidence_id",
        table_name="evidence_comparison_reviews",
    )
    op.drop_index(
        "ix_evidence_comparison_reviews_left_evidence_id",
        table_name="evidence_comparison_reviews",
    )
    op.drop_index(
        "ix_evidence_comparison_reviews_project_id",
        table_name="evidence_comparison_reviews",
    )
    op.drop_table("evidence_comparison_reviews")
    sa.Enum(name="comparison_decision").drop(op.get_bind(), checkfirst=True)
