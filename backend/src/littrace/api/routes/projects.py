from fastapi import (
    APIRouter,
    HTTPException,
    status,
)

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from littrace.api.dependencies import (
    DatabaseSession,
)
from littrace.api.schemas.project import (
    ProjectCreate,
    ProjectResponse,
)
from littrace.database.models.project import (
    ResearchProject,
)
# prefix 会自动加到本文件的所有路由前面。
#
# 下面函数中的路径是空字符串 ""，
# 最终接口地址就是 POST /projects。

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
)

@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建研究项目"
)
def create_project(
    project_data: ProjectCreate,
    session: DatabaseSession
) -> ProjectResponse:
    """
    创建一个新的文献研究项目。

    FastAPI 会先使用 ProjectCreate 校验请求 JSON，
    校验通过后才会执行这个函数。
    """
    # ORM 对象对应数据库中的一行。
    #
    # id、status、created_at 和 updated_at 不需要
    # 从用户请求中读取，它们由 ORM 或数据库生成。

    project = ResearchProject(
        title=project_data.title,
        research_question=(
            project_data.research_question
        ),
    )

    try:
        # add() 只是把对象加入当前 Session，
        # 此时通常还没有真正提交数据库事务。
        session.add(project)

        # commit() 提交事务。
        # PostgreSQL 中此时才真正保存该项目。
        session.commit()

        # 数据库负责生成 created_at 等字段。
        # refresh() 会重新查询该行，将这些字段
        # 加载回当前 ORM 对象。
        session.refresh(project)

    except SQLAlchemyError:
        # 如果写入或提交失败，必须回滚事务。
        #
        # 否则当前 Session 会保持在失败状态，
        # 后续 SQL 无法继续执行。
        session.rollback()

        # 暂时继续抛出原始异常，让 FastAPI 返回 500。
        # 后续会增加统一异常处理和结构化错误响应。
        raise

    # from_attributes=True 允许 Pydantic
    # 从 SQLAlchemy 对象属性中读取响应字段。
    return ProjectResponse.model_validate(project)

@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="查询单个研究项目",
)
def get_project(
    project_id: UUID,
    session: DatabaseSession,
) -> ProjectResponse:
    """
    根据项目 ID 查询研究项目。

    FastAPI 会自动把 URL 中的 project_id
    从字符串转换为 UUID。

    如果传入的字符串不是合法 UUID，
    FastAPI 会自动返回 422，不会进入此函数。
    """
    # Session.get() 专门用于根据主键查询。
    #
    # 第一个参数是 ORM 模型；
    # 第二个参数是要查询的主键值。
    project = session.get(
        ResearchProject,
        project_id,
    )
     # 找不到记录时，session.get() 会返回 None。
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    return ProjectResponse.model_validate(project)

