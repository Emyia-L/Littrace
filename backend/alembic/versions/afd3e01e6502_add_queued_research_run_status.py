"""add queued research run status

Revision ID: afd3e01e6502
Revises: 9d61ea720b34
Create Date: 2026-10-21 16:41:56.769205

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'afd3e01e6502'
down_revision: Union[str, Sequence[str], None] = '9d61ea720b34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # research_runs.status 使用 PostgreSQL 原生枚举；
    # 修改 Python Enum 本身不会自动修改数据库里的枚举类型。
    op.execute(
        "ALTER TYPE research_run_status "
        "ADD VALUE IF NOT EXISTS 'queued' AFTER 'pending'"
    )


def downgrade() -> None:
    # PostgreSQL 不能直接删除一个枚举值。
    # 若已有 queued 记录，阻止回滚，避免丢失或篡改运行状态。
    has_queued_runs = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM research_runs WHERE status::text = 'queued'"
            ")"
        )
    ).scalar_one()

    if has_queued_runs:
        raise RuntimeError("仍存在 queued 运行，不能回滚此迁移")

    # 临时保留旧类型，创建不含 queued 的新类型，再将列转换过去。
    op.execute(
        "ALTER TYPE research_run_status "
        "RENAME TO research_run_status_with_queued"
    )
    op.execute(
        "CREATE TYPE research_run_status AS ENUM "
        "('pending', 'running', 'completed', 'failed')"
    )
    op.execute(
        "ALTER TABLE research_runs ALTER COLUMN status "
        "TYPE research_run_status "
        "USING status::text::research_run_status"
    )
    op.execute("DROP TYPE research_run_status_with_queued")
