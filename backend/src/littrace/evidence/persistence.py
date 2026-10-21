from uuid import UUID

from sqlalchemy.orm import Session

from littrace.database.models.evidence import (
    EvidenceDecision,
    EvidenceRecord,
)
from littrace.database.models.research_run import ResearchRun
from littrace.domain.models import EvidenceGateResult


def save_evidence_records(
    *,
    session: Session,
    run_id: UUID,
    result: EvidenceGateResult,
) -> list[EvidenceRecord]:
    """
    将一次 Gate 检查结果追加写入指定运行的当前事务。

    本函数负责：
    1. 检查运行记录是否存在。
    2. 检查通过、拒绝分组与检查结果是否一致。
    3. 转换为数据库模型并执行写入。

    调用方负责提交事务；出现异常时也由调用方回滚。
    """

    # * 表示这些参数必须通过名称传入，
    # 例如 save_evidence_records(session=..., run_id=..., result=...)。
    #
    # 使用普通 Session 类型，使函数既可以被接口调用，
    # 也可以被后续 Agent 执行逻辑和测试调用。
    run = session.get(
        ResearchRun,
        run_id,
    )

    if run is None:
        raise ValueError("Research run not found")

    records: list[EvidenceRecord] = []

    # accepted 是从明细计算得到的卡片列表。
    # 保存时遍历 accepted_details，才能取得完整检查结果。
    for detail in result.accepted_details:
        # 核对分组是否与检查结果一致。
        # 这只是检查已有结果的一致性，
        # 不会重新调用模型，也不会重新判断论文事实。
        if (
            not detail.citation_check.valid
            or detail.claim_support_check.verdict != "supported"
        ):
            raise ValueError(
                "Accepted evidence must pass both checks"
            )

        record = EvidenceRecord(
            run_id=run_id,
            decision=EvidenceDecision.ACCEPTED,

            # model_dump 返回字典。
            # mode="json" 将字段转换为适合 JSON 存储的值，
            # 然后交给 SQLAlchemy 写入 JSONB 列。
            evidence=detail.evidence.model_dump(mode="json"),
            citation_check=detail.citation_check.model_dump(
                mode="json",
            ),
            claim_support_check=detail.claim_support_check.model_dump(
                mode="json",
            ),
        )

        records.append(record)

    for detail in result.rejected:
        support_check = detail.claim_support_check

        # 拒绝记录有两种正常情况：
        # 1. 引文检查失败，论断检查没有执行。
        # 2. 引文检查通过，论断检查为 unsupported 或 uncertain。
        if not detail.citation_check.valid:
            if support_check is not None:
                raise ValueError(
                    "Claim support check must be absent "
                    "when citation check fails"
                )
        elif (
            support_check is None
            or support_check.verdict == "supported"
        ):
            raise ValueError(
                "Rejected evidence has inconsistent checks"
            )

        record = EvidenceRecord(
            run_id=run_id,
            decision=EvidenceDecision.REJECTED,
            evidence=detail.evidence.model_dump(mode="json"),
            citation_check=detail.citation_check.model_dump(
                mode="json",
            ),

            # 未执行论断检查时保留 Python None。
            # 模型中的 JSONB(none_as_null=True)
            # 会将它写为数据库 NULL。
            claim_support_check=(
                support_check.model_dump(mode="json")
                if support_check is not None
                else None
            ),
        )

        records.append(record)

    # 上面的所有转换和一致性检查完成后，
    # 才将这些对象加入 Session。
    if records:
        session.add_all(records)

        # flush 将 INSERT 发送给数据库，
        # 可以提前发现外键、非空等数据库错误，
        # 并取得插入时生成的 ID 和创建时间。
        #
        # flush 尚未提交事务。
        # 调用方仍可以将本次写入整体回滚。
        session.flush()

    # 没有证据时返回空列表；否则返回已写入事务的对象。
    return records