from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)

from littrace.api.dependencies import DatabaseSession
from littrace.api.schemas.paper import PaperResponse
from littrace.config import get_settings
from littrace.database.models.paper import (
    Paper,
    PaperStatus,
)
from littrace.database.models.project import (
    ProjectStatus,
    ResearchProject,
)
from littrace.ingestion.pdf_parser import extract_pages
from littrace.storage.paper_storage import (
    calculate_content_hash,
    delete_paper_file,
    resolve_storage_path,
    save_paper_file,
)


router = APIRouter(
    prefix="/projects/{project_id}/papers",
    tags=["papers"],
)

@router.post(
    "",
    response_model=PaperResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上传论文 PDF",
)
def upload_paper(
    project_id: UUID,
    session: DatabaseSession,
    file: UploadFile = File(
        ...,
        description="带文本层的 PDF 论文",
    ),
) -> PaperResponse:
    """
    向指定研究项目上传一篇 PDF 论文。

    当前接口同步完成以下操作：

    1. 检查研究项目。
    2. 校验文件名、大小和 PDF 文件头。
    3. 计算 SHA-256，检查重复文件。
    4. 将 PDF 保存到本地存储目录。
    5. 使用 PyMuPDF 提取页面，确认存在文本层。
    6. 保存论文数据库记录。
    """

    settings = get_settings()

    # 先确认父项目存在。
    # 这样不会给不存在的项目保存孤立文件。
    project = session.get(
        ResearchProject,
        project_id
    )

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 浏览器有时可能传入完整本地路径，
    # 因此只保留最后的文件名部分。
    raw_filename = file.filename or "paper.pdf"
    original_filename = (
        raw_filename
        .replace("\\", "/")
        .split("/")[-1]
    )

    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported",
        )

    if len(original_filename) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDF filename is too long",
        )

    # 当前是同步 MVP，因此一次性读取文件。
    # 后续处理更大文件时，可以改为分块读取。
    content = file.file.read()

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded PDF is empty",
        )

    if len(content) > settings.max_pdf_size_bytes:
        max_size_mb = (
            settings.max_pdf_size_bytes
            // (1024 * 1024)
        )

        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"PDF exceeds the "
                f"{max_size_mb} MB limit"
            ),
        )

    # PDF 标识通常位于文件开头。
    # 不能只相信客户端声明的 Content-Type。
    if b"%PDF-" not in content[:1024]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content is not a valid PDF",
        )

    content_hash = calculate_content_hash(content)

    # 同一个项目中不允许重复导入内容相同的 PDF。
    existing_paper = session.scalar(
        select(Paper).where(
            Paper.project_id == project_id,
            Paper.content_hash == content_hash
        )
    )

    if existing_paper is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This PDF has already been "
                "uploaded to the project"
            ),
        )

    # 提前生成 paper_id，用它构造磁盘文件路径。
    paper_id = uuid4()

    storage_key = save_paper_file(
        content=content,
        project_id=project_id,
        paper_id=paper_id,
        storage_root=settings.paper_storage_dir
    )

    stored_path = resolve_storage_path(
        storage_root=settings.paper_storage_dir,
        storage_key=storage_key,
    )

    try:
        # 复用 Day 1 的 PDF 解析函数。
        # 如果是扫描版 PDF或损坏文件，这里会失败。
        pages = extract_pages(stored_path)
    except (ValueError, RuntimeError) as exc:
        # 解析失败时，不保留无效文件。
        delete_paper_file(
            storage_root=settings.paper_storage_dir,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=(
                "PDF cannot be parsed or contains "
                "no extractable text"
            ),
        ) from exc

    paper = Paper(
        id=paper_id,
        project_id=project_id,
        original_filename=original_filename,
        storage_key=storage_key,
        content_hash=content_hash,
        status=PaperStatus.PARSED,
        page_count=len(pages),
    )
    # 首次成功上传论文后，将项目状态从
    # draft 更新为 papers_uploaded。
    #
    # 如果项目以后已经进入更后面的状态，
    # 不应该因为新增论文而退回该状态。
    if project.status == ProjectStatus.DRAFT:
        project.status = (
            ProjectStatus.PAPERS_UPLOADED
        )

    try:
        session.add(paper)
        session.commit()
        session.refresh(paper)

    except IntegrityError as exc:
        # 并发上传相同文件时，前面的查询可能都没有
        # 查到记录，因此数据库唯一约束是最后防线。
        session.rollback()

        delete_paper_file(
            storage_root=settings.paper_storage_dir,
            storage_key=storage_key,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This PDF has already been "
                "uploaded to the project"
            ),
        ) from exc    

    except SQLAlchemyError:
        # 数据库失败时删除已经写入磁盘的文件，
        # 避免形成“有文件、无数据库记录”的垃圾数据。
        session.rollback()

        delete_paper_file(
            storage_root=settings.paper_storage_dir,
            storage_key=storage_key,
        )

        raise

    return PaperResponse.model_validate(paper)

@router.get(
    "",
    response_model=list[PaperResponse],
    status_code=status.HTTP_200_OK,
    summary="查询项目中的论文列表",
)
def list_project_papers(
    project_id: UUID,
    session: DatabaseSession,
) -> list[PaperResponse]:
    """
    查询指定研究项目中已经上传的全部论文。

    返回结果按创建时间倒序排列，
    即最近上传的论文排在前面。
    """
    # 即使论文列表为空，也需要先确认项目存在。
    #
    # “项目存在但没有论文”应返回 200 和 []；
    # “项目不存在”应返回 404。

    project = session.get(
        ResearchProject,
        project_id
    )

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    result = session.scalars(
        select(Paper)
        .where(Paper.project_id == project_id)
        .order_by(
            Paper.created_at.desc(),
            Paper.id.desc(),
        )
    )

    papers = result.all()

    return [
        PaperResponse.model_validate(paper)
        for paper in papers
    ]

