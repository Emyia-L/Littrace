from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    BigInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import (
    UUID as PostgresUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from littrace.database.base import Base

class ResearchRunStatus(str, Enum):
    """一次研究运行的状态。"""

    # 已经创建运行记录，但尚未开始执行。
    PENDING = "pending"

    # 正在执行检索、证据提取或检查。
    RUNNING = "running"

    # 流程正常结束。
    # 注意：这不代表所有证据都通过检查，
    # 即使最终没有可接受证据，流程也可能正常结束。
    COMPLETED = "completed"

    # 执行过程中发生错误，未能正常完成。
    FAILED = "failed"

    # API 已接受执行请求，任务在 Redis 中等待 Worker 领取。
    # 此时尚未调用模型，因此不能算 running。
    QUEUED = "queued"

class ResearchRun(Base):
    """记录一次针对项目论文的研究执行。"""   

    __tablename__ = "research_runs"

    # 数据库约束是最后一道数据检查。
    # 即使以后有脚本绕过 API 直接写入数据库，
    # 这里也能阻止不符合范围的参数。
    #
    # 范围与当前 EvidenceRequest 的参数限制保持一致。
    __table_args__ = (
        CheckConstraint(
            "top_k >= 1 AND top_k <= 100",
            name="ck_research_runs_top_k_range",
        ),
        CheckConstraint(
            "max_cards >= 1 AND max_cards <= 10",
            name="ck_research_runs_max_cards_range",
        ),
        CheckConstraint(
            "retry_of_run_id IS NULL OR retry_of_run_id <> id",
            name="ck_research_runs_not_self_retry",
        ),
        CheckConstraint(
            "execution_mode IS NULL OR execution_mode IN ('workflow', 'agent')",
            name="ck_research_runs_execution_mode",
        ),
        CheckConstraint(
            "(agent_stop_reason IS NULL AND agent_steps_used IS NULL) OR "
            "(execution_mode IS NOT NULL AND execution_mode = 'agent' "
            "AND agent_stop_reason IS NOT NULL "
            "AND agent_stop_reason IN ('finish', 'step_limit') "
            "AND agent_steps_used IS NOT NULL "
            "AND agent_steps_used BETWEEN 1 AND 20)",
            name="ck_research_runs_agent_outcome",
        ),
        CheckConstraint(
            "model_call_count >= 0 AND model_prompt_tokens >= 0 "
            "AND model_completion_tokens >= 0 AND model_total_tokens >= 0 "
            "AND model_duration_ms >= 0 AND estimated_cost_microusd >= 0",
            name="ck_research_runs_model_metrics_nonnegative",
        ),
    )

    # 每次运行都有独立 ID。
    #
    # default=uuid4 传入的是函数本身，
    # SQLAlchemy 插入记录时会调用它生成 UUID。
    # 不要写成 default=uuid4()，否则会提前生成一个固定值。
    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # 当前运行属于哪个研究项目。
    #
    # index=True 为“查询某个项目的历史运行”建立索引。
    # 此字段不设置 unique，因为同一个项目可以运行多次。
    #
    # 与现有论文表保持一致：
    # 删除项目时，数据库也会删除它的运行记录。
    project_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "research_projects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # 首次运行为空；重试运行指向它的直接来源，形成可查询的重试链。
    # 来源仍在 research_runs 表中，因此这是自引用外键。
    retry_of_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "research_runs.id",
            name="fk_research_runs_retry_of",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    # 保存这一次实际提交的问题。
    #
    # 不能只读取项目的 research_question：
    # 同一项目可能提出不同问题，项目问题也可能被修改，
    # 因此运行记录需要保留当时的问题文本。
    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # 保存本次检索和证据生成的关键参数。
    # 它们帮助我们解释历史结果，但还不足以完整复现运行；
    # 模型、提示词版本等信息将在后续逐步补充。
    top_k: Mapped[int] = mapped_column(
        Integer,
        default=8,
        nullable=False,
    )

    # 保存本次运行最多允许生成多少张证据卡。
    #
    # top_k 控制“检索多少个候选文本块”，
    # max_cards 控制“最多生成多少张证据卡”。
    # 两者是不同参数，需要分别保存。
    #
    # 后续创建 ResearchRun 时，会从 EvidenceRequest
    # 读取本次请求的 max_cards，保存到这个字段。
    max_cards: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
    )

    # 模式在真正执行时才确定；pending 运行及旧记录允许为 NULL。
    # 数据库 CHECK 约束防止绕过 API 写入其他模式名称。
    execution_mode: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # 仅在 Agent 正常结束后填写；Workflow、pending、running 均为 NULL。
    agent_stop_reason: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    # 本次 Agent 实际调用规划器的轮数，不是检索结果数量。
    agent_steps_used: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # 只保存用量与耗时，不保存可能包含论文正文的模型请求和回复。
    model_call_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    model_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    model_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    model_total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    model_duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 使用百万分之一美元作整数单位，避免浮点金额误差。
    estimated_cost_microusd: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)

    status: Mapped[ResearchRunStatus] = mapped_column(
        SqlEnum(
            ResearchRunStatus,
            name="research_run_status",

            # 数据库存储 pending、running 等枚举值，
            # 而不是 PENDING、RUNNING 等 Python 成员名。
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],

            # 如果调用方传入字符串，检查它是否为合法枚举值。
            validate_strings=True,
        ),
        default=ResearchRunStatus.PENDING,
        nullable=False,
    )

    # 正常运行时为空；失败时保存简短错误说明。
    #
    # 后续写入此字段时，需要由业务代码进行脱敏，
    # 不应直接保存包含 API Key、连接串等信息的异常内容。
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # 记录创建时间，由 PostgreSQL 生成。
    # timezone=True 使用带时区的数据库时间类型。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # 记录最后一次更新时间。
    #
    # onupdate 在 SQLAlchemy 发起更新时提供时间值，
    # 它不是数据库触发器；直接执行其他 SQL 更新时，
    # 不能依赖它自动生效。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # 创建记录时可能尚未开始执行，所以允许为 NULL。
    # 后续执行逻辑负责在真正开始时填写。
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # 运行完成或失败时填写。
    # 正在等待或执行时，该字段保持为 NULL。
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
