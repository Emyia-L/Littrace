from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from littrace.config import get_settings
from littrace.database.base import Base

# 导入模型是为了把表注册到 Base.metadata。
from littrace.database.models.project import (
    ResearchProject,
)
from littrace.database.models.paper import Paper
# 导入时，SQLAlchemy 会将 paper_chunks 表
# 注册到 Base.metadata，供 Alembic 比较表结构。
from littrace.database.models.paper_chunk import (
    PaperChunkRecord,
)

# 导入模型时，SQLAlchemy 会把 research_runs 表
# 注册到 Base.metadata，供 Alembic 比较数据库结构。
from littrace.database.models.research_run import (
    ResearchRun,
)
# 导入证据模型，将 evidence_records 注册到 Base.metadata。
from littrace.database.models.evidence import EvidenceRecord
from littrace.database.models.evidence_review import EvidenceReview
from littrace.database.models.evidence_comparison import EvidenceComparisonReview

from littrace.database.models.agent_step import AgentStepRecord
from littrace.database.models.run_memory import ResearchRunMemory
from littrace.database.models.project_synthesis import ProjectSynthesis


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


settings = get_settings()

# Alembic 使用 ConfigParser 读取配置，
# 因此连接串中的 % 需要转义为 %%。
database_url = settings.database_url.replace(
    "%",
    "%%",
)

config.set_main_option(
    "sqlalchemy.url",
    database_url,
)

# 明确引用所有 ORM 模型，确保它们都被注册到
# Base.metadata，供 Alembic 自动比较数据库结构。
_ = (
    ResearchProject,
    Paper,
    PaperChunkRecord,
    ResearchRun,
    EvidenceRecord,
    EvidenceReview,
    EvidenceComparisonReview,
    AgentStepRecord,
    ResearchRunMemory,
    ProjectSynthesis,
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    离线生成 SQL，不直接连接数据库。
    """

    url = config.get_main_option(
        "sqlalchemy.url"
    )

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    连接数据库并执行迁移。
    """

    connectable = engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
