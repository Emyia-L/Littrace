from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from littrace.database.models.paper_chunk import (
    PaperChunkRecord,
)

from littrace.ingestion.chunker import chunk_pages

from littrace.api.dependencies import DatabaseSession
from littrace.api.schemas.paper import PaperResponse
from littrace.api.schemas.paper_chunk import (
    PaperChunkResponse,
)

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

from openai import APIError

from littrace.embedding_client import create_embeddings


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

        # 使用真实论文 UUID 生成 chunk_id。
        # 每个文本块仍然保留原始 PDF 页码。
        chunks = chunk_pages(
            pages=pages,
            paper_id=str(paper_id),
        )
        if not chunks:
            raise ValueError(
                "PDF contains no usable text chunks"
            )

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

    try:
        # 一次为所有文本块生成向量。
        # create_embeddings 内部会拆成每批最多 20 条请求。
        embeddings = create_embeddings(
            [chunk.text for chunk in chunks]
        )
    except APIError as exc:
        # Embedding 服务不可用时，不保留已经保存但未处理完成的 PDF。
        delete_paper_file(
            storage_root=settings.paper_storage_dir,
            storage_key=storage_key,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding service failed; please retry",
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
        # flush() 将论文 INSERT 发送给数据库，
        # 保证后面的文本块能够引用这篇论文。
        #
        # flush() 没有提交事务：
        # 如果后续文本块写入失败，论文也可以一起回滚。
        session.flush()
        chunk_records = [
            PaperChunkRecord(
                chunk_id=chunk.chunk_id,
                paper_id=paper_id,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                text=chunk.text,

                # 向量与正文一一对应，保存在同一条文本块记录中。
                embedding=embedding,

                content_hash=calculate_content_hash(
                    chunk.text.encode("utf-8")
                ),
            )
            # strict=True 会在两边数量不一致时立即报错，
            # 避免把某个向量错配到其他文本块。
            for chunk, embedding in zip(
                chunks,
                embeddings,
                strict=True,
            )
        ]
        # add_all() 将多个 ORM 对象加入 Session。
        session.add_all(chunk_records)
        # 论文、文本块和项目状态统一提交。
        # 任意数据库操作失败，都回滚整个事务。
        session.commit()

    except IntegrityError as exc:
        # 并发上传相同文件时，前面的查询可能都没有
        # 查到记录，因此数据库唯一约束是最后防线。
        session.rollback()

        delete_paper_file(
            storage_root=settings.paper_storage_dir,
            storage_key=storage_key,
        )
        # IntegrityError 也可能来自外键或块序号约束。
        # 只有论文哈希唯一约束冲突才表示重复上传。
        diagnostic = getattr(
            exc.orig,
            "diag",
            None,
        )
        constraint_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        if constraint_name == (
            "uq_papers_project_content_hash"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This PDF has already been "
                    "uploaded to the project"
                ),
            ) from exc

        # 其他约束错误保留原始异常，
        # 方便通过服务端日志定位问题。
        raise  

    except SQLAlchemyError:
        # 数据库失败时删除已经写入磁盘的文件，
        # 避免形成“有文件、无数据库记录”的垃圾数据。
        session.rollback()

        delete_paper_file(
            storage_root=settings.paper_storage_dir,
            storage_key=storage_key,
        )

        raise

    # refresh 放在提交异常处理之后。
    # 此时数据已经提交，如果读取失败，
    # 不能删除已被数据库记录引用的 PDF。
    session.refresh(paper)

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

@router.get(
    "/{paper_id}/chunks",
    response_model=list[PaperChunkResponse],
    status_code=status.HTTP_200_OK,
    summary="查询论文文本块",
)
def list_paper_chunks(
    project_id: UUID,
    paper_id: UUID,
    session: DatabaseSession,
    offset: int = Query(
        default=0,
        ge=0,
        description="跳过的文本块数量",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="本次最多返回的文本块数量",
    ),
) -> list[PaperChunkResponse]:
    """
    分页查询指定论文的文本块。

    返回结果按页码、页内块序号升序排列，
    与阅读论文正文的顺序一致。
    """
    # project_id 和 paper_id 来自 URL 路径。
    # FastAPI 会自动验证它们是否是合法 UUID。
    project = session.get(
        ResearchProject,
        project_id,
    )

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )

    # 同时检查论文 ID 和项目 ID。
    #
    # 仅根据 paper_id 查询，会允许客户端把 A 项目的
    # 论文 ID 放进 B 项目的 URL，造成归属信息不一致。
    #
    # where() 中的多个条件默认使用 AND 连接。
    paper = session.scalar(
        select(Paper).where(
            Paper.id == paper_id,
            Paper.project_id == project_id,
        )
    )

    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paper not found in this project",
        )

    # select() 先构造查询语句，此时还没有执行 SQL。
    statement = (
        select(PaperChunkRecord)
        .where(
            PaperChunkRecord.paper_id == paper_id
        )
        .order_by(
            PaperChunkRecord.page_number.asc(),
            PaperChunkRecord.chunk_index.asc(),
        )
        .offset(offset)
        .limit(limit)
    )

    # scalars() 执行查询并提取 ORM 对象；
    # all() 将本次查询结果收集为列表。
    chunk_records = session.scalars(
        statement
    ).all()

    # 把数据库对象转换成 API 响应模型。
    #
    # 没有匹配的文本块时，列表推导式返回 []，
    # 客户端收到 HTTP 200 和空数组。
    return [
        PaperChunkResponse.model_validate(record)
        for record in chunk_records
    ]


@router.get(
    "/{paper_id}/file",
    response_class=FileResponse,
    summary="读取论文 PDF 原文",
)
def get_paper_file(
    project_id: UUID,
    paper_id: UUID,
    session: DatabaseSession,
) -> FileResponse:
    """核对项目归属后，返回服务器保存的 PDF。"""
    if session.get(ResearchProject, project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research project not found",
        )
    paper = session.scalar(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paper not found in this project",
        )

    # 不接受客户端指定磁盘路径，并校验数据库里的路径仍在存储根目录中。
    try:
        file_path = resolve_storage_path(
            storage_root=get_settings().paper_storage_dir,
            storage_key=paper.storage_key,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored PDF path is invalid",
        ) from exc
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored PDF not found",
        )

    filename = paper.original_filename
    # 读取元数据后结束只读事务，不在文件传输期间占用数据库连接。
    session.commit()
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )
