from collections.abc import Generator

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from littrace.database.models.agent_step import AgentStepRecord
from littrace.research.agent_actions import (
    AgentDecision,
    SearchAction,
    FinishAction,
    SynthesizeProjectAction,
)
from littrace.research.agent_loop import AgentStepTrace
from littrace.research.agent_step_persistence import save_agent_steps
from littrace.research.agent_step_persistence import append_agent_step

from littrace.api.app import create_app
from littrace.database.session import (
    engine,
    get_db_session,
)

from littrace.research.agent_loop import AgentLoopResult

from unittest.mock import MagicMock

from littrace.retriever.full_text_retriever import (
    search_chunks_by_full_text,
)

from littrace.config import get_settings
from hashlib import sha256
from uuid import UUID,uuid4

from sqlalchemy import select

from littrace.database.models.paper_chunk import (
    PaperChunkRecord,
)

from littrace.database.models.research_run import (
    ResearchRun,
    ResearchRunStatus,
)

from littrace.database.models.evidence import (
    EvidenceDecision,
    EvidenceRecord,
)
from littrace.database.models.evidence_review import EvidenceReview, ReviewDecision
from littrace.database.models.run_memory import ResearchRunMemory
from littrace.domain.models import (
    AcceptedEvidence,
    CitationCheck,
    ClaimSupportCheck,
    ChunkSearchResult,
    EvidenceCard,
    EvidenceGateResult,
    PaperChunk,
    RejectedEvidence,
)
from littrace.evidence.persistence import save_evidence_records

from littrace.database.models.paper import Paper
from littrace.database.models.project import ResearchProject
from littrace.research.agent_actions import ExtractEvidenceAction
from littrace.research.agent_tools import resolve_evidence_chunks
from littrace.research.report_sources import build_report_evidence
from littrace.research.cross_run_memory import load_cross_run_memory

@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """
    为每个测试提供独立的数据库事务。

    API 和测试函数使用同一个 Session，
    因此测试可以查询接口刚刚写入的数据。
    """

    with engine.connect() as connection:
        outer_transaction = connection.begin()

        session = Session(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        try:
            yield session
        finally:
            session.close()

            # 撤销测试期间写入的项目、论文和文本块。
            outer_transaction.rollback()
 
    
@pytest.fixture
def client(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """提供使用测试 Session 和临时文件目录的客户端。"""

    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "paper_storage_dir",
        tmp_path,
    )

    def override_get_db_session() -> Generator[
        Session,
        None,
        None,
    ]:
        # 将 API 的数据库依赖替换成测试 Session。
        yield db_session

    # 上传论文使用 create_embeddings：一次处理多个文本块。
    # 搜索问题使用 create_embedding：一次处理一个问题。
    # 两个调用入口需要分别模拟，不能互相替代。
    def fake_create_embedding(text: str) -> list[float]:
        """返回固定查询向量，避免搜索测试调用真实服务。"""
        return [0.01] * 1024

    monkeypatch.setattr(
        "littrace.retriever.vector_retriever.create_embedding",
        fake_create_embedding,
    )


    def fake_create_embeddings(
        texts: list[str],
    ) -> list[list[float]]:
        """模拟批量生成向量，不访问真实 Embedding 服务。"""

        # 输入有多少个文本块，就返回多少个向量。
        # 例如输入 3 个文本块，就返回 3 个独立的向量列表。
        #
        # 每个向量的长度必须是 1024，
        # 与数据库中 embedding 列的 Vector(1024) 保持一致。
        #
        # 使用非零向量，避免后续计算余弦距离时遇到零向量。
        return [
            [0.01] * 1024
            for _ in texts
        ]

    # 上传接口使用的是 papers 模块中的 create_embeddings 名称，
    # 因此必须替换“调用方模块里的函数引用”。
    #
    # 测试期间，上传接口调用这个名称时，
    # 实际执行的是上面定义的模拟函数。
    monkeypatch.setattr(
        "littrace.api.routes.papers.create_embeddings",
        fake_create_embeddings,
    )


    app = create_app()
    app.dependency_overrides[
        get_db_session
    ] = override_get_db_session

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()

        # Session 的关闭和事务回滚由 db_session
        # fixture 负责，这里只清理客户端依赖。
    
   


def test_create_project(
    client: TestClient,
) -> None:
    """测试创建研究项目。"""

    response = client.post(
        "/projects",
        json={
            "title": "OmniAgent 文献研究",
            "research_question": (
                "主动感知代理解决了什么问题？"
            ),
        },
    )

    assert response.status_code == 201

    response_data = response.json()

    assert response_data["title"] == (
        "OmniAgent 文献研究"
    )
    assert response_data["research_question"] == (
        "主动感知代理解决了什么问题？"
    )
    assert response_data["status"] == "draft"

    assert response_data["id"]
    assert response_data["created_at"]
    assert response_data["updated_at"]


def test_list_projects_is_paginated_and_validated(client: TestClient) -> None:
    """项目选择页需要稳定分页；无效分页参数应由 FastAPI 拒绝。"""
    first = client.post(
        "/projects",
        json={"title": "项目甲", "research_question": "问题甲"},
    ).json()["id"]
    second = client.post(
        "/projects",
        json={"title": "项目乙", "research_question": "问题乙"},
    ).json()["id"]

    all_projects = client.get("/projects")
    assert all_projects.status_code == 200
    # 测试数据库可能已有用户项目；只验证新建项目也出现在列表中。
    assert {first, second}.issubset({item["id"] for item in all_projects.json()})
    page = client.get("/projects", params={"offset": 1, "limit": 1})
    assert page.status_code == 200
    assert len(page.json()) == 1
    assert page.json()[0]["id"] == all_projects.json()[1]["id"]
    assert client.get("/projects", params={"limit": 0}).status_code == 422
    assert client.get("/projects", params={"offset": -1}).status_code == 422


def test_get_project(
    client: TestClient,
) -> None:
    """
    测试根据 ID 查询一个存在的项目。

    测试首先通过 POST 创建项目，
    然后使用返回的 ID 调用 GET 接口。
    """

    create_response = client.post(
        "/projects",
        json={
            "title": "可追溯证据研究",
            "research_question": (
                "如何验证大模型生成内容的证据来源？"
            ),
        },
    )

    assert create_response.status_code == 201

    created_project = create_response.json()
    project_id = created_project["id"]

    get_response = client.get(
        f"/projects/{project_id}"
    )

    assert get_response.status_code == 200

    returned_project = get_response.json()

    # 查询结果应该与刚创建的项目相同。
    assert returned_project["id"] == project_id
    assert returned_project["title"] == (
        "可追溯证据研究"
    )
    assert returned_project["research_question"] == (
        "如何验证大模型生成内容的证据来源？"
    )
    assert returned_project["status"] == "draft"


def test_get_missing_project_returns_404(
    client: TestClient,
) -> None:
    """查询不存在的项目时应该返回 404。"""

    missing_project_id = (
        "00000000-0000-0000-0000-000000000000"
    )

    response = client.get(
        f"/projects/{missing_project_id}"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Research project not found",
    }

def create_test_pdf_bytes(
    text: str = (
        "LitTrace test paper "
        "with extractable text."
    ),
) -> bytes:
    """
    在内存中生成一个只有一页的文本型 PDF。

    测试不依赖 samples 目录中的真实论文，
    因此测试速度更快，也更容易重复执行。
    """

    document = pymupdf.open()
    page = document.new_page()

    page.insert_text(
        (72, 72),
        text,
    )

    pdf_content = document.tobytes()
    document.close()

    return pdf_content

def test_upload_paper(
    client: TestClient,
    tmp_path: Path,
    db_session: Session,
) -> None:
    """
    测试完整上传流程：

    创建项目、上传 PDF、解析页面、
    保存数据库记录和保存磁盘文件。
    """

    project_response = client.post(
        "/projects",
        json={
            "title": "PDF 上传测试",
            "research_question": (
                "测试论文解决了什么问题？"
            ),
        },
    )

    assert project_response.status_code == 201

    project_id = project_response.json()["id"]
    pdf_content = create_test_pdf_bytes()

    upload_response = client.post(
        f"/projects/{project_id}/papers",
        files={
            "file": (
                "TestPaper.PDF",
                pdf_content,
                "application/pdf",
            ),
        },
    )

    assert upload_response.status_code == 201

    paper = upload_response.json()

    assert paper["project_id"] == project_id
    assert paper["original_filename"] == (
        "TestPaper.PDF"
    )
    assert paper["status"] == "parsed"
    assert paper["page_count"] == 1
    assert len(paper["content_hash"]) == 64

    # 文件应该保存到测试临时目录，
    # 路径结构是：项目ID/论文ID.pdf。
    saved_file = (
        tmp_path
        / project_id
        / f"{paper['id']}.pdf"
    )

    assert saved_file.exists()
    assert saved_file.read_bytes() == pdf_content

    # 成功上传论文后，项目状态应该更新。
    updated_project_response = client.get(
        f"/projects/{project_id}"
    )

    assert updated_project_response.status_code == 200
    assert (
        updated_project_response.json()["status"]
        == "papers_uploaded"
    )
     # JSON 中的 id 是字符串，
    # 数据库 paper_id 列使用的是 UUID 类型。
    paper_id = UUID(paper["id"])

    chunk_records = db_session.scalars(
        select(PaperChunkRecord)
        .where(
            PaperChunkRecord.paper_id == paper_id
        )
        .order_by(
            PaperChunkRecord.page_number,
            PaperChunkRecord.chunk_index,
        )
    ).all()

    # 测试 PDF 只有一页，正文很短，
    # 所以应该只产生一个文本块。
    assert len(chunk_records) == 1

    chunk = chunk_records[0]

    assert chunk.paper_id == paper_id
    assert chunk.page_number == 1
    assert chunk.chunk_index == 1
    assert chunk.chunk_id == (
        f"{paper_id}-p1-c1"
    )

    expected_text = (
        "LitTrace test paper "
        "with extractable text."
    )

    # 检查数据库中的正文与测试 PDF 内容一致。
    assert chunk.text == expected_text

    # 独立计算预期哈希，验证上传逻辑保存的值。
    expected_hash = sha256(
        expected_text.encode("utf-8")
    ).hexdigest()

    assert chunk.content_hash == expected_hash
    assert chunk.created_at is not None

    # 前面的测试已经确认数据库保存了一个文本块。
    # 现在通过 HTTP 接口读取，验证路由和响应模型
    # 能否把数据库记录正确返回给客户端。
    chunks_url = (
        f"/projects/{project_id}"
        f"/papers/{paper_id}/chunks"
    )

    chunks_response = client.get(
        chunks_url,
        params={
            "offset": 0,
            "limit": 1,
        },
    )

    assert chunks_response.status_code == 200

    # 比较完整 JSON，检查数据库对象转换成
    # PaperChunkResponse 后是否保留了正确字段。
    #
    # UUID 在 Python 中是 UUID 对象，
    # 在 JSON 中会被序列化成字符串。
    assert chunks_response.json() == [
        {
            "chunk_id": f"{paper_id}-p1-c1",
            "paper_id": str(paper_id),
            "page_number": 1,
            "chunk_index": 1,
            "text": expected_text,
            "content_hash": expected_hash,
        }
    ]

    # 测试 PDF 只有一个块。
    # offset=1 跳过唯一的块后，应该返回空数组，
    # 而不是将“没有更多结果”视为错误。
    next_page_response = client.get(
        chunks_url,
        params={
            "offset": 1,
            "limit": 1,
        },
    )

    assert next_page_response.status_code == 200
    assert next_page_response.json() == []

    # 路由中的 Query(ge=1) 禁止 limit=0。
    # FastAPI 应在执行查询函数之前返回 422。
    invalid_limit_response = client.get(
        chunks_url,
        params={
            "limit": 0,
        },
    )

    assert invalid_limit_response.status_code == 422

    assert chunk.embedding is not None
    assert len(chunk.embedding) == 1024

def test_duplicate_paper_is_rejected(
    client: TestClient,
) -> None:
    """同一个项目不能重复上传内容相同的 PDF。"""

    project_response = client.post(
        "/projects",
        json={
            "title": "重复论文测试",
            "research_question": "如何识别重复论文？",
        },
    )

    project_id = project_response.json()["id"]
    pdf_content = create_test_pdf_bytes()

    files = {
        "file": (
            "paper.pdf",
            pdf_content,
            "application/pdf",
        ),
    }

    first_response = client.post(
        f"/projects/{project_id}/papers",
        files=files,
    )

    second_response = client.post(
        f"/projects/{project_id}/papers",
        files=files,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json() == {
        "detail": (
            "This PDF has already been "
            "uploaded to the project"
        ),
    }

def test_invalid_pdf_is_rejected(
    client: TestClient,
) -> None:
    """扩展名是 PDF，但内容不是 PDF 时应拒绝。"""

    project_response = client.post(
        "/projects",
        json={
            "title": "无效文件测试",
            "research_question": "这个文件是否有效？",
        },
    )

    project_id = project_response.json()["id"]

    response = client.post(
        f"/projects/{project_id}/papers",
        files={
            "file": (
                "fake.pdf",
                b"This is not a real PDF.",
                "application/pdf",
            ),
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "File content is not a valid PDF",
    }

def test_list_project_papers(
    client: TestClient,
) -> None:
    """
    测试查询项目中的论文列表。

    同时验证：
    1. 新项目的论文列表为空。
    2. 上传两篇论文后可以查询到两条记录。
    3. 不会向客户端暴露 storage_key。
    """

    project_response = client.post(
        "/projects",
        json={
            "title": "论文列表测试",
            "research_question": (
                "这些论文研究了什么问题？"
            ),
        },
    )

    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    # 项目存在但没有论文时，应返回空列表。
    empty_response = client.get(
        f"/projects/{project_id}/papers"
    )

    assert empty_response.status_code == 200
    assert empty_response.json() == []

    first_upload = client.post(
        f"/projects/{project_id}/papers",
        files={
            "file": (
                "first-paper.pdf",
                create_test_pdf_bytes(
                    "This is the first test paper."
                ),
                "application/pdf",
            ),
        },
    )

    second_upload = client.post(
        f"/projects/{project_id}/papers",
        files={
            "file": (
                "second-paper.pdf",
                create_test_pdf_bytes(
                    "This is the second test paper."
                ),
                "application/pdf",
            ),
        },
    )

    assert first_upload.status_code == 201
    assert second_upload.status_code == 201

    list_response = client.get(
        f"/projects/{project_id}/papers"
    )

    assert list_response.status_code == 200

    papers = list_response.json()

    assert len(papers) == 2

    filenames = {
        paper["original_filename"]
        for paper in papers
    }

    assert filenames == {
        "first-paper.pdf",
        "second-paper.pdf",
    }

    assert all(
        paper["project_id"] == project_id
        for paper in papers
    )

    # 服务器内部存储路径不应出现在 API 响应中。
    assert all(
        "storage_key" not in paper
        for paper in papers
    )


def test_list_papers_for_missing_project_returns_404(
    client: TestClient,
) -> None:
    """查询不存在项目的论文列表时返回 404。"""

    missing_project_id = (
        "00000000-0000-0000-0000-000000000000"
    )

    response = client.get(
        f"/projects/{missing_project_id}/papers"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Research project not found",
    }

def test_cannot_read_chunks_from_another_project(
    client: TestClient,
) -> None:
    """
    验证论文必须属于 URL 指定的研究项目。

    准备两个项目 A、B，并向 A 上传论文：
    - 使用 A 的地址查询，应成功。
    - 使用 B 的地址查询同一篇论文，应返回 404。
    """

    # 第一步：创建两个真实存在的项目。
    #
    # B 项目必须存在，这样才能确认 404 是因为
    # “论文不属于 B”，而不是“B 项目不存在”。
    project_a_response = client.post(
        "/projects",
        json={
            "title": "项目 A",
            "research_question": "论文讨论了什么方法？",
        },
    )

    project_b_response = client.post(
        "/projects",
        json={
            "title": "项目 B",
            "research_question": "论文有什么局限性？",
        },
    )

    assert project_a_response.status_code == 201
    assert project_b_response.status_code == 201

    project_a_id = project_a_response.json()["id"]
    project_b_id = project_b_response.json()["id"]

    # 第二步：只向项目 A 上传论文。
    #
    # 复用已有的 PDF 生成函数。
    # 上传接口会解析 PDF、切块并保存数据库记录。
    upload_response = client.post(
        f"/projects/{project_a_id}/papers",
        files={
            "file": (
                "project-a-paper.pdf",
                create_test_pdf_bytes(),
                "application/pdf",
            ),
        },
    )

    assert upload_response.status_code == 201
    paper_id = upload_response.json()["id"]

    # 第三步：先验证正确地址可以读到文本块。
    #
    # 如果接口对所有请求都错误地返回 404，
    # 后面的拒绝测试也可能通过。
    # 因此先检查正常访问，避免这种误判。
    correct_response = client.get(
        f"/projects/{project_a_id}"
        f"/papers/{paper_id}/chunks"
    )

    assert correct_response.status_code == 200

    chunks = correct_response.json()

    assert len(chunks) == 1
    assert chunks[0]["paper_id"] == paper_id

    # 第四步：保留论文 ID，只把 URL 中的项目换成 B。
    #
    # 论文确实存在，但不属于 B，
    # 因此接口应拒绝返回它的正文。
    wrong_project_response = client.get(
        f"/projects/{project_b_id}"
        f"/papers/{paper_id}/chunks"
    )

    assert wrong_project_response.status_code == 404
    assert wrong_project_response.json() == {
        "detail": "Paper not found in this project",
    }


def test_full_text_search(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    验证全文检索的匹配、排序、数量限制和项目隔离。

    测试数据由当前测试自己创建，
    不依赖数据库中已有的真实论文。
    """

    # 创建两个项目。
    # 项目 A 用于主要检索，项目 B 用于验证数据隔离。
    project_a_response = client.post(
        "/projects",
        json={
            "title": "全文检索项目 A",
            "research_question": "如何检索论文证据？",
        },
    )
    project_b_response = client.post(
        "/projects",
        json={
            "title": "全文检索项目 B",
            "research_question": "其他项目的研究问题",
        },
    )

    assert project_a_response.status_code == 201
    assert project_b_response.status_code == 201

    project_a_id = project_a_response.json()["id"]
    project_b_id = project_b_response.json()["id"]

    # 每个元组依次保存：项目 ID、文件名、正文。
    #
    # 项目 A 有：
    # - 一篇只出现一次 retrieval 的论文；
    # - 一篇出现三次 retrieval 的论文；
    # - 一篇不包含 retrieval 的论文。
    #
    # 项目 B 也放入匹配正文，确保项目隔离检查有效。
    paper_specs = [
        (
            project_a_id,
            "low-match.pdf",
            "Vector retrieval improves evidence search.",
        ),
        (
            project_a_id,
            "high-match.pdf",
            "Retrieval retrieval retrieval improves evidence search.",
        ),
        (
            project_a_id,
            "unrelated.pdf",
            "Astronomy studies stars and galaxies.",
        ),
        (
            project_b_id,
            "other-project.pdf",
            "Retrieval retrieval retrieval retrieval in another project.",
        ),
    ]

    # 保存上传返回的论文 ID，供后面独立检查结果。
    paper_ids: dict[str, str] = {}

    for project_id, filename, body in paper_specs:
        upload_response = client.post(
            f"/projects/{project_id}/papers",
            files={
                "file": (
                    filename,
                    create_test_pdf_bytes(body),
                    "application/pdf",
                ),
            },
        )

        assert upload_response.status_code == 201
        paper_ids[filename] = upload_response.json()["id"]

    # API 和检索函数使用同一个测试 Session。
    # 因此这里可以读取刚上传的数据，
    # 测试结束后这些数据仍会被外层事务回滚。
    results = search_chunks_by_full_text(
        session=db_session,
        project_id=UUID(project_a_id),
        query_text="retrieval",
        top_k=8,
    )

    # A 项目应只有两篇论文匹配。
    # 不相关论文和 B 项目的论文都不能出现在结果中。
    assert len(results) == 2

    # 两篇测试论文都很短，每篇只有一个文本块。
    # 当前排序规则下，多次出现关键词的块应排在前面。
    assert [
        result.chunk.paper_id
        for result in results
    ] == [
        paper_ids["high-match.pdf"],
        paper_ids["low-match.pdf"],
    ]

    # 不比较固定的小数值，只验证分数大小关系。
    assert results[0].score > results[1].score
    assert results[1].score > 0

    # 检索结果必须保留原文定位信息，
    # 后续 Evidence Card 和引用验证需要这些字段。
    for result in results:
        assert result.chunk.page_number == 1
        assert result.chunk.chunk_index == 1
        assert result.chunk.chunk_id == (
            f"{result.chunk.paper_id}-p1-c1"
        )

    # 限制只取一个结果时，应取相关性最高的块。
    limited_results = search_chunks_by_full_text(
        session=db_session,
        project_id=UUID(project_a_id),
        query_text="retrieval",
        top_k=1,
    )

    assert len(limited_results) == 1
    assert limited_results[0].chunk.paper_id == (
        paper_ids["high-match.pdf"]
    )

    # 查询所有测试正文都没有的词，
    # 应返回空列表，而不是任意补充几个无关块。
    missing_results = search_chunks_by_full_text(
        session=db_session,
        project_id=UUID(project_a_id),
        query_text="volcanology",
        top_k=8,
    )

    assert missing_results == []
     # 使用刚才创建的项目和论文，调用 HTTP 搜索接口。
    #
    # params 会由 TestClient 编码成查询参数：
    # ?q=retrieval&top_k=1
    search_response = client.get(
        f"/projects/{project_a_id}/search",
        params={
            "q": "retrieval",
            "top_k": 1,
        },
    )

    assert search_response.status_code == 200

    # limited_results 已在前面的断言中验证过，
    # 包含相关性最高的那一个文本块。
    #
    # model_dump(mode="json") 将 Pydantic 对象
    # 转换为适合 JSON 的字典。
    # 这里检查接口是否完整返回相同的数据。
    assert search_response.json() == [
        result.model_dump(mode="json")
        for result in limited_results
    ]

    # 合法关键词没有匹配结果时，
    # 应返回 200 和 []，而不是 404。
    no_match_response = client.get(
        f"/projects/{project_a_id}/search",
        params={
            "q": "volcanology",
            "top_k": 8,
        },
    )

    assert no_match_response.status_code == 200
    assert no_match_response.json() == []

def test_search_missing_project_returns_404(
    client: TestClient,
) -> None:
    """搜索不存在的研究项目时应返回 404。"""

    # 生成一个新的 UUID，但不在数据库中创建该项目。
    missing_project_id = uuid4()

    response = client.get(
        f"/projects/{missing_project_id}/search",
        params={
            "q": "retrieval",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Research project not found",
    }

@pytest.mark.parametrize(
    "params",
    [
        {},                              # 没有提供必填参数 q。
        {"q": ""},                       # 提供了空字符串。
        {"q": "   "},                    # 只包含空白字符。
        {"q": "retrieval", "top_k": 0},   # 数量小于最小值。
        {"q": "retrieval", "top_k": 101}, # 数量超过最大值。
    ],
    ids=[
        "missing-query",
        "empty-query",
        "blank-query",
        "top-k-too-small",
        "top-k-too-large",
    ],
)
def test_search_rejects_invalid_parameters(
    client: TestClient,
    params: dict[str, str | int],
) -> None:
    """
    每组无效参数都应该返回 422。

    parametrize 会用不同参数执行同一个测试函数，
    这里一共生成五个独立测试用例。
    """

    # 创建真实项目，让本测试集中验证参数问题，
    # 不受到“项目不存在”的影响。
    project_response = client.post(
        "/projects",
        json={
            "title": "搜索参数校验",
            "research_question": "如何搜索论文？",
        },
    )

    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    response = client.get(
        f"/projects/{project_id}/search",
        params=params,
    )

    assert response.status_code == 422

@pytest.fixture
def research_project_id(
    client: TestClient,
) -> str:
    """为每个使用此 fixture 的测试创建一个项目。"""

    response = client.post(
        "/projects",
        json={
            "title": "研究运行接口测试",
            "research_question": "如何提取可追溯的论文证据？",
        },
    )

    # 准备阶段失败时，立即指出问题，
    # 避免后面的测试因为缺少项目而出现难以理解的错误。
    assert response.status_code == 201

    return response.json()["id"]

@pytest.mark.parametrize(
    ("parameters", "expected_top_k", "expected_max_cards"),
    [
        # 不提交可选参数，检查默认值。
        ({}, 8, 3),

        # 提交自定义参数，检查实际保存的是用户传入值。
        ({"top_k": 5, "max_cards": 2}, 5, 2),
    ],
)
def test_research_run_can_be_created_and_queried(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    parameters: dict[str, int],
    expected_top_k: int,
    expected_max_cards: int,
) -> None:
    """验证创建响应、数据库记录和查询结果保持一致。"""

    question = "这篇论文有哪些局限？"

    response = client.post(
        f"/projects/{research_project_id}/runs",
        json={
            # 顺便验证问题两端的空白会被清除。
            "question": f"  {question}  ",

            # ** 将 parameters 中的键值对展开到请求正文。
            # 第一组参数为空，因此请求只包含 question。
            **parameters,
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert data["project_id"] == research_project_id
    assert data["question"] == question
    assert data["top_k"] == expected_top_k
    assert data["max_cards"] == expected_max_cards
    assert data["status"] == "pending"
    assert data["created_at"] is not None
    assert data["updated_at"] is not None

    # 新创建的运行尚未开始执行。
    assert data["error_message"] is None
    assert data["started_at"] is None
    assert data["finished_at"] is None

    # 将响应中的字符串 ID 转换成数据库模型使用的 UUID。
    run_id = UUID(data["id"])

    # 清除 Session 中已加载的属性缓存，
    # 让下面的查询重新读取数据库。
    db_session.expire_all()

    saved_run = db_session.get(
        ResearchRun,
        run_id,
    )

    # 接口返回成功之外，还要确认测试事务中确实存在记录。
    assert saved_run is not None
    assert saved_run.project_id == UUID(research_project_id)
    assert saved_run.question == question
    assert saved_run.top_k == expected_top_k
    assert saved_run.max_cards == expected_max_cards
    assert saved_run.status == ResearchRunStatus.PENDING

    # 再通过 GET 接口读取同一条记录。
    get_response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}"
    )

    assert get_response.status_code == 200

    # 查询过程没有更新记录，响应应该与创建时一致。
    assert get_response.json() == data

@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   "},
        {"question": "论文有哪些局限？", "top_k": 0},
        {"question": "论文有哪些局限？", "top_k": 101},
        {"question": "论文有哪些局限？", "max_cards": 0},
        {"question": "论文有哪些局限？", "max_cards": 11},

        # status 不属于创建请求允许提交的字段。
        # extra="forbid" 应当拒绝它。
        {"question": "论文有哪些局限？", "status": "completed"},
    ],
)
def test_research_run_rejects_invalid_input(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    payload: dict[str, object],
) -> None:
    """无效输入应返回 422，并且不创建运行记录。"""

    response = client.post(
        f"/projects/{research_project_id}/runs",
        json=payload,
    )

    assert response.status_code == 422

    # 当前测试的项目刚刚创建，原本没有任何运行记录。
    # 请求被拒绝后，也不应该留下记录。
    saved_runs = db_session.scalars(
        select(ResearchRun).where(
            ResearchRun.project_id == UUID(research_project_id),
        )
    ).all()

    assert saved_runs == []

def test_research_run_cannot_be_read_from_another_project(
    client: TestClient,
    research_project_id: str,
) -> None:
    """项目 B 的路径不能读取属于项目 A 的运行。"""

    # 在项目 A 中创建运行。
    create_response = client.post(
        f"/projects/{research_project_id}/runs",
        json={
            "question": "这篇论文主要解决了什么问题？",
        },
    )

    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    # 创建一个确实存在的项目 B。
    # 这样才能验证“运行归属检查”，
    # 而不是提前触发“项目不存在”。
    other_project_response = client.post(
        "/projects",
        json={
            "title": "另一个研究项目",
            "research_question": "模型推理成本如何？",
        },
    )

    assert other_project_response.status_code == 201
    other_project_id = other_project_response.json()["id"]

    # 使用项目 B 的路径，查询项目 A 的运行 ID。
    response = client.get(
        f"/projects/{other_project_id}/runs/{run_id}"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Research run not found",
    }

@pytest.mark.parametrize(
    "verdict",
    ["unsupported", "uncertain"],
)
def test_save_evidence_records_and_rollback(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    verdict: str,
) -> None:
    """验证检查结果保存、JSONB 读取和事务回滚。"""

    question = "这篇论文有哪些局限？"

    # 先通过已有接口创建运行，取得真实的 run_id。
    response = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": question},
    )

    assert response.status_code == 201
    run_id = UUID(response.json()["id"])

    # 人工构造检查结果，测试聚焦于持久化。
    # 引文真实性和论断检查逻辑已有单独的 Gate 测试。
    card = EvidenceCard(
        question=question,
        claim="该方法需要额外的计算。",
        relation="supports",
        quote="The method requires extra computation.",
        page_number=1,
        chunk_id="test-paper-p1-c1",
        limitations=[],
    )

    result = EvidenceGateResult(
        accepted_details=[
            AcceptedEvidence(
                evidence=card,
                citation_check=CitationCheck(valid=True),
                claim_support_check=ClaimSupportCheck(
                    verdict="supported",
                    reason="引用明确说明需要额外计算。",
                ),
            ),
        ],
        rejected=[
            # 情况一：引文不存在，因此没有执行论断检查。
            RejectedEvidence(
                evidence=card.model_copy(
                    update={
                        "claim": "该方法不需要额外计算。",
                        "quote": "The method requires no computation.",
                    },
                ),
                citation_check=CitationCheck(
                    valid=False,
                    reason="quote_not_found",
                ),
                claim_support_check=None,
            ),

            # 情况二：引文存在，但不足以确认完整论断。
            RejectedEvidence(
                evidence=card.model_copy(
                    update={
                        "claim": "该方法将计算成本降低了20%。",
                    },
                ),
                citation_check=CitationCheck(valid=True),
                claim_support_check=ClaimSupportCheck(
                    verdict=verdict,
                    reason="引用没有给出成本降低幅度。",
                ),
            ),
        ],
    )

    records = save_evidence_records(
        session=db_session,
        run_id=run_id,
        result=result,
    )

    assert len(records) == 3

    # 清除属性缓存后重新查询，
    # 检查数据库实际保存并读取出的 JSONB 内容。
    db_session.expire_all()

    saved_records = db_session.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.run_id == run_id,
        )
    ).all()

    assert len(saved_records) == 3

    # 本测试中的三个 claim 各不相同，
    # 因此可以用 claim 建立查找表。
    # 测试不依赖数据库返回记录的顺序。
    records_by_claim = {
        record.evidence["claim"]: record
        for record in saved_records
    }

    expected_records = [
        (EvidenceDecision.ACCEPTED, result.accepted_details[0]),
        (EvidenceDecision.REJECTED, result.rejected[0]),
        (EvidenceDecision.REJECTED, result.rejected[1]),
    ]

    for decision, detail in expected_records:
        saved = records_by_claim[detail.evidence.claim]

        assert saved.id is not None
        assert saved.run_id == run_id
        assert saved.decision == decision
        assert saved.created_at is not None

        # 验证完整卡片及检查理由没有在转换时丢失。
        assert saved.evidence == detail.evidence.model_dump(
            mode="json",
        )
        assert saved.citation_check == detail.citation_check.model_dump(
            mode="json",
        )

        expected_support_check = (
            detail.claim_support_check.model_dump(mode="json")
            if detail.claim_support_check is not None
            else None
        )

        assert saved.claim_support_check == expected_support_check

    # SQL NULL 和 JSON null 读取后都可能表现为 Python None。
    # 使用 IS NULL 查询，进一步确认这里保存的是数据库 NULL。
    null_check_ids = db_session.scalars(
        select(EvidenceRecord.id).where(
            EvidenceRecord.run_id == run_id,
            EvidenceRecord.claim_support_check.is_(None),
        )
    ).all()

    assert len(null_check_ids) == 1

    # 保存函数只调用 flush，没有提交。
    # 因此当前事务回滚后，这三条证据应该全部消失。
    db_session.rollback()

    remaining_records = db_session.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.run_id == run_id,
        )
    ).all()

    assert remaining_records == []

    # 运行由创建接口提前提交过，仍应存在。
    # 测试结束时，最外层测试事务会继续清理它。
    assert db_session.get(ResearchRun, run_id) is not None

def test_save_evidence_records_rejects_missing_run(
    db_session: Session,
) -> None:
    """指定的运行不存在时，应明确报错。"""

    # 即使结果为空，也要求关联的运行确实存在。
    with pytest.raises(
        ValueError,
        match="Research run not found",
    ):
        save_evidence_records(
            session=db_session,
            run_id=uuid4(),
            result=EvidenceGateResult(),
        )


def test_execute_research_run_persists_evidence_and_rejects_repeat(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行接口应保存证据，且同一运行只能启动一次。"""

    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？", "top_k": 5, "max_cards": 2},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    # 只模拟外部检索与模型产出，保留真实执行器和数据库写入。
    # 这样测试能够覆盖“路由 -> 执行器 -> 证据持久化”的连接。
    chunk = PaperChunk(
        chunk_id="sample-p1-c1",
        paper_id=str(uuid4()),
        page_number=1,
        chunk_index=1,
        text="The method improves accuracy.",
    )
    card = EvidenceCard(
        question="论文解决什么问题？",
        claim="该方法提高了准确率。",
        relation="supports",
        quote="The method improves accuracy.",
        page_number=1,
        chunk_id=chunk.chunk_id,
    )
    gate_result = EvidenceGateResult(
        accepted_details=[
            AcceptedEvidence(
                evidence=card,
                citation_check=CitationCheck(valid=True),
                claim_support_check=ClaimSupportCheck(
                    verdict="supported",
                    reason="引文明确支持该说法。",
                ),
            )
        ]
    )

    def fake_search(*, session, project_id, query_text, top_k):
        assert project_id == UUID(research_project_id)
        assert query_text == "论文解决什么问题？"
        assert top_k == 5
        return [ChunkSearchResult(chunk=chunk, score=0.1)]

    def fake_gate(*, question, chunks, max_cards):
        assert question == "论文解决什么问题？"
        assert chunks == [chunk]
        assert max_cards == 2
        return gate_result

    # executor.py 在导入时绑定了这两个函数名，故替换调用方的名称。
    monkeypatch.setattr(
        "littrace.research.executor.search_chunks_hybrid", fake_search
    )
    monkeypatch.setattr("littrace.research.executor.run_evidence_gate", fake_gate)

    executed = client.post(
        f"/projects/{research_project_id}/runs/{run_id}/execute"
    )
    assert executed.status_code == 200
    data = executed.json()
    assert data["status"] == "completed"
    assert data["started_at"] is not None
    assert data["finished_at"] is not None
    assert data["error_message"] is None

    # 检查证据确实写入数据库，而非仅出现在模拟结果里。
    records = db_session.scalars(
        select(EvidenceRecord).where(EvidenceRecord.run_id == UUID(run_id))
    ).all()
    assert len(records) == 1
    assert records[0].decision == EvidenceDecision.ACCEPTED
    assert records[0].evidence["chunk_id"] == chunk.chunk_id

    repeated = client.post(
        f"/projects/{research_project_id}/runs/{run_id}/execute"
    )
    assert repeated.status_code == 409
    assert len(
        db_session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.run_id == UUID(run_id))
        ).all()
    ) == 1


def test_execute_research_run_failure_is_queryable(
    client: TestClient,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型输出失败时，接口报错，同时保留可查询的 failed 状态。"""

    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    chunk = PaperChunk(
        chunk_id="sample-p1-c1",
        paper_id=str(uuid4()),
        page_number=1,
        chunk_index=1,
        text="The method improves accuracy.",
    )
    monkeypatch.setattr(
        "littrace.research.executor.search_chunks_hybrid",
        lambda **kwargs: [ChunkSearchResult(chunk=chunk, score=0.1)],
    )

    def fail_gate(**kwargs):
        raise ValueError("Model returned invalid evidence")

    monkeypatch.setattr("littrace.research.executor.run_evidence_gate", fail_gate)

    failed_response = client.post(
        f"/projects/{research_project_id}/runs/{run_id}/execute"
    )
    assert failed_response.status_code == 502
    assert failed_response.json()["detail"] == "Research execution failed"

    stored = client.get(f"/projects/{research_project_id}/runs/{run_id}")
    assert stored.status_code == 200
    data = stored.json()
    assert data["status"] == "failed"
    assert data["started_at"] is not None
    assert data["finished_at"] is not None
    assert data["error_message"] == "Research execution failed."


def test_failed_agent_run_preserves_completed_steps(
    client: TestClient,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent 后续失败时，先前步骤与 failed 状态仍可查询。"""
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    def fail_after_first_step(**kwargs):
        # 模拟第一轮成功，随后规划器在下一轮报错。
        kwargs["on_step"](
            AgentStepTrace(
                step_number=1,
                action=SearchAction(
                    kind="search",
                    strategy="keyword",
                    query="OmniAgent",
                ),
                observation_summary='{"tool":"search","matches":[]}',
            )
        )
        raise ValueError("Planner returned invalid action")

    monkeypatch.setattr(
        "littrace.research.executor.run_agent_loop",
        fail_after_first_step,
    )

    executed = client.post(
        f"/projects/{research_project_id}/runs/"
        f"{run_id}/execute?execution_mode=agent"
    )
    assert executed.status_code == 502

    run_response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}"
    )
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "failed"

    steps_response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}/steps"
    )
    assert steps_response.status_code == 200
    steps = steps_response.json()
    assert len(steps) == 1
    assert steps[0]["step_number"] == 1
    assert steps[0]["action_kind"] == "search"
    assert steps[0]["action_payload"]["query"] == "OmniAgent"


def test_failed_agent_run_still_records_status_if_partial_trace_is_invalid(
    client: TestClient,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """轨迹校验失败不应阻止执行器保存 failed 状态。"""
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    def fail_with_invalid_step(**kwargs):
        # 模拟编号不从 1 开始的损坏轨迹，触发保存函数的校验错误。
        kwargs["on_step"](
            AgentStepTrace(
                step_number=2,
                action=SearchAction(
                    kind="search",
                    strategy="keyword",
                    query="OmniAgent",
                ),
                observation_summary='{"tool":"search","matches":[]}',
            )
        )
        raise ValueError("Planner returned invalid action")

    monkeypatch.setattr(
        "littrace.research.executor.run_agent_loop",
        fail_with_invalid_step,
    )

    executed = client.post(
        f"/projects/{research_project_id}/runs/"
        f"{run_id}/execute?execution_mode=agent"
    )
    assert executed.status_code == 502

    run_response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}"
    )
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "failed"

    steps_response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}/steps"
    )
    assert steps_response.status_code == 200
    assert steps_response.json() == []


def test_execute_research_run_is_scoped_to_its_project(
    client: TestClient,
    research_project_id: str,
) -> None:
    """另一个项目的 URL 不能启动当前项目的运行。"""

    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    other_project = client.post(
        "/projects",
        json={
            "title": "另一个项目",
            "research_question": "论文有什么局限？",
        },
    )
    assert other_project.status_code == 201
    other_project_id = other_project.json()["id"]

    blocked = client.post(
        f"/projects/{other_project_id}/runs/{run_id}/execute"
    )
    assert blocked.status_code == 404

    # 同一个归属规则也必须应用于轨迹读取接口。
    blocked_steps = client.get(
        f"/projects/{other_project_id}/runs/{run_id}/steps"
    )
    assert blocked_steps.status_code == 404

    blocked_retry = client.post(
        f"/projects/{other_project_id}/runs/{run_id}/retry"
    )
    assert blocked_retry.status_code == 404

    # 归属检查失败不应把原项目里的运行改成 running。
    original = client.get(
        f"/projects/{research_project_id}/runs/{run_id}"
    )
    assert original.status_code == 200
    assert original.json()["status"] == "pending"

def test_list_research_run_evidence_returns_saved_records(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> None:
    """读取接口应返回已保存的通过和拒绝证据。"""

    # 先创建运行；此操作只写数据库，不调用模型。
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    url = f"/projects/{research_project_id}/runs/{run_id}/evidence"

    # 运行存在但尚无证据时，应返回空列表，而不是 404。
    empty_response = client.get(url)
    assert empty_response.status_code == 200
    assert empty_response.json() == []

    # 直接准备两条数据库记录，以便只测试“读取接口”；
    # 证据生成和保存函数已有各自的测试。
    accepted_card = EvidenceCard(
        question="论文解决什么问题？",
        claim="该方法提高了准确率。",
        relation="supports",
        quote="The method improves accuracy.",
        page_number=1,
        chunk_id="sample-p1-c1",
    )
    rejected_card = EvidenceCard(
        question="论文解决什么问题？",
        claim="该方法提高了准确率 20%。",
        relation="supports",
        quote="Quote not present in the chunk.",
        page_number=1,
        chunk_id="sample-p1-c2",
    )

    db_session.add_all([
        EvidenceRecord(
            run_id=UUID(run_id),
            decision=EvidenceDecision.ACCEPTED,
            evidence=accepted_card.model_dump(mode="json"),
            citation_check=CitationCheck(valid=True).model_dump(mode="json"),
            claim_support_check=ClaimSupportCheck(
                verdict="supported",
                reason="引文明确支持准确率提高。",
            ).model_dump(mode="json"),
        ),
        EvidenceRecord(
            run_id=UUID(run_id),
            decision=EvidenceDecision.REJECTED,
            evidence=rejected_card.model_dump(mode="json"),
            citation_check=CitationCheck(
                valid=False,
                reason="quote_not_found",
            ).model_dump(mode="json"),
            # 引文检查失败后，没有执行论断支持检查。
            claim_support_check=None,
        ),
    ])
    db_session.commit()

    response = client.get(url)
    assert response.status_code == 200

    records = response.json()
    assert len(records) == 2

    # 不依赖两条记录的先后顺序，按处理决定检查内容。
    by_decision = {item["decision"]: item for item in records}
    assert set(by_decision) == {"accepted", "rejected"}

    accepted = by_decision["accepted"]
    assert accepted["run_id"] == run_id
    assert accepted["evidence"]["claim"] == "该方法提高了准确率。"
    assert accepted["citation_check"]["valid"] is True
    assert accepted["claim_support_check"]["verdict"] == "supported"

    rejected = by_decision["rejected"]
    assert rejected["citation_check"]["reason"] == "quote_not_found"
    assert rejected["claim_support_check"] is None

@pytest.fixture
def evidence_source_case(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> dict[str, str]:
    """上传测试 PDF，并准备同一文本块的接受与拒绝证据。"""
    uploaded = client.post(
        f"/projects/{research_project_id}/papers",
        files={
            "file": (
                "source.pdf",
                create_test_pdf_bytes("The method improves accuracy."),
                "application/pdf",
            ),
        },
    )
    assert uploaded.status_code == 201
    paper_id = uploaded.json()["id"]
    chunk = db_session.scalar(
        select(PaperChunkRecord).where(PaperChunkRecord.paper_id == UUID(paper_id))
    )
    assert chunk is not None

    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "方法提高了什么？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    card = EvidenceCard(
        question="方法提高了什么？",
        claim="该方法提高准确率。",
        relation="supports",
        quote="The method improves accuracy.",
        page_number=chunk.page_number,
        chunk_id=chunk.chunk_id,
    )
    accepted = EvidenceRecord(
        run_id=UUID(run_id),
        decision=EvidenceDecision.ACCEPTED,
        evidence=card.model_dump(mode="json"),
        citation_check=CitationCheck(valid=True).model_dump(mode="json"),
        claim_support_check=ClaimSupportCheck(
            verdict="supported", reason="引文支持论断。"
        ).model_dump(mode="json"),
    )
    rejected = EvidenceRecord(
        run_id=UUID(run_id),
        decision=EvidenceDecision.REJECTED,
        evidence=card.model_dump(mode="json"),
        citation_check=CitationCheck(valid=False, reason="rejected").model_dump(
            mode="json"
        ),
        claim_support_check=None,
    )
    db_session.add_all([accepted, rejected])
    db_session.commit()
    return {
        "project_id": research_project_id,
        "paper_id": paper_id,
        "run_id": run_id,
        "chunk_id": chunk.chunk_id,
        "accepted_id": str(accepted.id),
        "rejected_id": str(rejected.id),
    }


def test_evidence_source_resolves_pdf_and_enforces_scope(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """只从所属项目和运行中返回已接受证据的 PDF 位置。"""
    case = evidence_source_case
    url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/source"
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == {
        "evidence_id": case["accepted_id"],
        "paper_id": case["paper_id"],
        "original_filename": "source.pdf",
        "page_number": 1,
        "chunk_id": case["chunk_id"],
        "quote": "The method improves accuracy.",
        "file_url": f"/projects/{case['project_id']}/papers/{case['paper_id']}/file",
    }
    # 返回的链接确实可以读取刚上传的原始 PDF。
    original = client.get(response.json()["file_url"])
    assert original.status_code == 200
    assert original.content.startswith(b"%PDF")

    # 项目、运行或证据 ID 不匹配时，不能借这个接口读取原文定位。
    for project_id, run_id, evidence_id in [
        (str(uuid4()), case["run_id"], case["accepted_id"]),
        (case["project_id"], str(uuid4()), case["accepted_id"]),
        (case["project_id"], case["run_id"], str(uuid4())),
    ]:
        denied = client.get(
            f"/projects/{project_id}/runs/{run_id}/evidence/{evidence_id}/source"
        )
        assert denied.status_code == 404

    another_project = client.post(
        "/projects",
        json={"title": "另一项目", "research_question": "能否跨项目访问？"},
    )
    assert another_project.status_code == 201
    foreign_project_id = another_project.json()["id"]
    foreign_project_response = client.get(
        f"/projects/{foreign_project_id}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/source"
    )
    assert foreign_project_response.status_code == 404


def test_evidence_source_rejects_unaccepted_card(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """Gate 拒绝的卡片即使指向真实文本块，也不提供已验证来源。"""
    case = evidence_source_case
    response = client.get(
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['rejected_id']}/source"
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Only accepted evidence has a verified source"


def test_evidence_source_detects_missing_foreign_and_wrong_page(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """保存内容与文本块不一致时，接口不会给出错误的论文定位。"""
    case = evidence_source_case
    record = db_session.get(EvidenceRecord, UUID(case["accepted_id"]))
    assert record is not None
    url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/source"
    )
    original_card = dict(record.evidence)

    record.evidence = {**original_card, "chunk_id": str(uuid4())}
    db_session.commit()
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Evidence source not found"

    # 建立另一项目的真实块；其 ID 存在，但当前项目不能引用它。
    other_project = ResearchProject(title="其他项目", research_question="其他问题？")
    db_session.add(other_project)
    db_session.flush()
    other_paper = Paper(
        project_id=other_project.id,
        original_filename="other.pdf",
        storage_key="tests/other.pdf",
        content_hash="b" * 64,
    )
    db_session.add(other_paper)
    db_session.flush()
    other_chunk = PaperChunkRecord(
        chunk_id=f"{other_paper.id}-p1-c1",
        paper_id=other_paper.id,
        page_number=1,
        chunk_index=1,
        text="Other project's text.",
        content_hash=sha256(b"Other project's text.").hexdigest(),
    )
    db_session.add(other_chunk)
    record.evidence = {**original_card, "chunk_id": other_chunk.chunk_id}
    db_session.commit()
    foreign = client.get(url)
    assert foreign.status_code == 404
    assert foreign.json()["detail"] == "Evidence source not found"

    record.evidence = {**original_card, "page_number": 2}
    db_session.commit()
    wrong_page = client.get(url)
    assert wrong_page.status_code == 409
    assert wrong_page.json()["detail"] == "Evidence page does not match source"


def test_evidence_reviews_keep_history_and_original_evidence(
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """复核改变的是人工意见，不覆盖 Evidence Gate 保存的判断。"""
    evidence_id = UUID(evidence_source_case["accepted_id"])
    first = EvidenceReview(
        evidence_id=evidence_id,
        decision=ReviewDecision.CONFIRMED,
        note="原文和论断一致。",
    )
    second = EvidenceReview(
        evidence_id=evidence_id,
        decision=ReviewDecision.DISMISSED,
        note="复查后发现该证据不适合回答研究问题。",
    )
    db_session.add_all([first, second])
    db_session.commit()

    reviews = db_session.scalars(
        select(EvidenceReview)
        .where(EvidenceReview.evidence_id == evidence_id)
        .order_by(EvidenceReview.review_order)
    ).all()
    assert {review.id for review in reviews} == {first.id, second.id}
    assert {review.decision for review in reviews} == {
        ReviewDecision.CONFIRMED,
        ReviewDecision.DISMISSED,
    }
    assert {review.note for review in reviews} == {
        "原文和论断一致。",
        "复查后发现该证据不适合回答研究问题。",
    }
    assert first.review_order < second.review_order
    original = db_session.get(EvidenceRecord, evidence_id)
    assert original is not None
    assert original.decision == EvidenceDecision.ACCEPTED


def test_create_evidence_review_appends_history(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """连续提交两次复核，API 返回新记录，原证据不被覆盖。"""
    case = evidence_source_case
    evidence_id = UUID(case["accepted_id"])
    url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{evidence_id}/reviews"
    )

    first = client.post(
        url,
        json={"decision": "confirmed", "note": "引文直接支持结论。"},
    )
    second = client.post(
        url,
        json={"decision": "dismissed", "note": "与研究问题关联较弱。"},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["evidence_id"] == second.json()["evidence_id"] == str(
        evidence_id
    )
    assert first.json()["decision"] == "confirmed"
    assert second.json()["decision"] == "dismissed"
    assert first.json()["note"] == "引文直接支持结论。"
    assert second.json()["note"] == "与研究问题关联较弱。"
    assert first.json()["created_at"] and second.json()["created_at"]
    assert first.json()["review_order"] < second.json()["review_order"]

    saved = db_session.scalars(
        select(EvidenceReview).where(EvidenceReview.evidence_id == evidence_id)
    ).all()
    assert {str(review.id) for review in saved} == {
        first.json()["id"],
        second.json()["id"],
    }
    original = db_session.get(EvidenceRecord, evidence_id)
    assert original is not None
    assert original.decision == EvidenceDecision.ACCEPTED


def test_create_evidence_review_checks_scope_gate_and_input(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """错误归属、Gate 拒绝和非法请求都不能写入复核。"""
    case = evidence_source_case
    base = f"/projects/{case['project_id']}/runs/{case['run_id']}"
    accepted_url = f"{base}/evidence/{case['accepted_id']}/reviews"
    payload = {"decision": "confirmed"}

    rejected = client.post(
        f"{base}/evidence/{case['rejected_id']}/reviews", json=payload
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Only accepted evidence can be reviewed"

    for project_id, run_id, evidence_id in [
        (str(uuid4()), case["run_id"], case["accepted_id"]),
        (case["project_id"], str(uuid4()), case["accepted_id"]),
        (case["project_id"], case["run_id"], str(uuid4())),
    ]:
        response = client.post(
            f"/projects/{project_id}/runs/{run_id}/evidence/{evidence_id}/reviews",
            json=payload,
        )
        assert response.status_code == 404

    foreign_project = client.post(
        "/projects",
        json={"title": "复核隔离测试", "research_question": "可否访问其他项目？"},
    )
    assert foreign_project.status_code == 201
    foreign_response = client.post(
        f"/projects/{foreign_project.json()['id']}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/reviews",
        json=payload,
    )
    assert foreign_response.status_code == 404

    for invalid_payload in [
        {"decision": "maybe"},
        {"decision": "confirmed", "note": "x" * 2001},
    ]:
        assert client.post(accepted_url, json=invalid_payload).status_code == 422

    # 共享测试数据库可能已有其他证据的复核；这里只检查本用例的证据。
    assert db_session.scalars(
        select(EvidenceReview).where(
            EvidenceReview.evidence_id.in_([
                UUID(case["accepted_id"]),
                UUID(case["rejected_id"]),
            ])
        )
    ).all() == []


def test_list_evidence_reviews_returns_empty_and_saved_history(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """GET 能区分未复核的证据，并读回 POST 保存的所有复核。"""
    case = evidence_source_case
    url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/reviews"
    )
    empty = client.get(url)
    assert empty.status_code == 200
    assert empty.json() == []

    first = client.post(url, json={"decision": "confirmed", "note": "引文准确。"})
    second = client.post(url, json={"decision": "dismissed", "note": "不够相关。"})
    assert first.status_code == second.status_code == 201

    # 人为制造相同时间戳，验证排序确实依赖数据库序号。
    first_record = db_session.get(EvidenceReview, UUID(first.json()["id"]))
    second_record = db_session.get(EvidenceReview, UUID(second.json()["id"]))
    assert first_record is not None and second_record is not None
    second_record.created_at = first_record.created_at
    db_session.commit()

    response = client.get(url)
    assert response.status_code == 200
    history = response.json()
    assert len(history) == 2
    assert {item["id"] for item in history} == {
        first.json()["id"],
        second.json()["id"],
    }
    assert {item["decision"] for item in history} == {"confirmed", "dismissed"}
    assert {item["note"] for item in history} == {"引文准确。", "不够相关。"}
    assert all(item["evidence_id"] == case["accepted_id"] for item in history)
    assert [item["id"] for item in history] == [
        first.json()["id"],
        second.json()["id"],
    ]
    assert history[0]["created_at"] == history[1]["created_at"]
    assert history[0]["review_order"] < history[1]["review_order"]
    # 没有复核记录的另一条证据，不会混入当前证据的历史。
    rejected_url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['rejected_id']}/reviews"
    )
    assert client.get(rejected_url).json() == []


def test_list_evidence_reviews_enforces_project_run_and_evidence_scope(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """知道别的项目的复核 URL，也不能跨项目读取记录。"""
    case = evidence_source_case
    url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/reviews"
    )
    assert client.post(url, json={"decision": "confirmed"}).status_code == 201

    other_project = client.post(
        "/projects",
        json={"title": "其他复核项目", "research_question": "其他项目能否读取？"},
    )
    assert other_project.status_code == 201

    for project_id, run_id, evidence_id in [
        (str(uuid4()), case["run_id"], case["accepted_id"]),
        (other_project.json()["id"], case["run_id"], case["accepted_id"]),
        (case["project_id"], str(uuid4()), case["accepted_id"]),
        (case["project_id"], case["run_id"], str(uuid4())),
    ]:
        denied = client.get(
            f"/projects/{project_id}/runs/{run_id}/evidence/{evidence_id}/reviews"
        )
        assert denied.status_code == 404


def test_review_queue_groups_accepted_evidence_and_review_history(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """清单只包含 Gate 接受的证据，复核记录归到对应证据下。"""
    case = evidence_source_case
    queue_url = f"/projects/{case['project_id']}/runs/{case['run_id']}/review-queue"
    initially = client.get(queue_url)
    assert initially.status_code == 200
    assert len(initially.json()) == 1
    assert initially.json()[0]["evidence"]["id"] == case["accepted_id"]
    assert initially.json()[0]["reviews"] == []

    original = db_session.get(EvidenceRecord, UUID(case["accepted_id"]))
    assert original is not None
    # 再加入一张接受证据，验证多个证据不会共用同一份复核列表。
    second_card = {**original.evidence, "claim": "第二条独立论断。"}
    second_evidence = EvidenceRecord(
        run_id=UUID(case["run_id"]),
        decision=EvidenceDecision.ACCEPTED,
        evidence=second_card,
        citation_check=original.citation_check,
        claim_support_check=original.claim_support_check,
    )
    db_session.add(second_evidence)
    db_session.commit()

    first_url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{case['accepted_id']}/reviews"
    )
    second_url = (
        f"/projects/{case['project_id']}/runs/{case['run_id']}"
        f"/evidence/{second_evidence.id}/reviews"
    )
    first_review = client.post(first_url, json={"decision": "confirmed"})
    second_review = client.post(
        first_url, json={"decision": "dismissed", "note": "再次复查。"}
    )
    third_review = client.post(second_url, json={"decision": "confirmed"})
    assert [r.status_code for r in [first_review, second_review, third_review]] == [
        201, 201, 201
    ]

    response = client.get(queue_url)
    assert response.status_code == 200
    by_id = {item["evidence"]["id"]: item for item in response.json()}
    assert set(by_id) == {case["accepted_id"], str(second_evidence.id)}
    assert {review["id"] for review in by_id[case["accepted_id"]]["reviews"]} == {
        first_review.json()["id"],
        second_review.json()["id"],
    }
    assert [review["id"] for review in by_id[case["accepted_id"]]["reviews"]] == [
        first_review.json()["id"],
        second_review.json()["id"],
    ]
    assert [review["id"] for review in by_id[str(second_evidence.id)]["reviews"]] == [
        third_review.json()["id"]
    ]
    # fixture 还创建了一张被 Gate 拒绝的证据，它不属于待复核清单。
    assert case["rejected_id"] not in by_id


def test_review_queue_checks_run_project_and_empty_run(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """无接受证据的运行返回空清单，其他项目不能读取该运行。"""
    case = evidence_source_case
    empty_run = client.post(
        f"/projects/{case['project_id']}/runs",
        json={"question": "尚未执行的新问题？"},
    )
    assert empty_run.status_code == 201
    empty_response = client.get(
        f"/projects/{case['project_id']}/runs/{empty_run.json()['id']}/review-queue"
    )
    assert empty_response.status_code == 200
    assert empty_response.json() == []

    other_project = client.post(
        "/projects",
        json={"title": "隔离项目", "research_question": "能否读取清单？"},
    )
    assert other_project.status_code == 201
    for project_id, run_id in [
        (str(uuid4()), case["run_id"]),
        (other_project.json()["id"], case["run_id"]),
        (case["project_id"], str(uuid4())),
    ]:
        denied = client.get(f"/projects/{project_id}/runs/{run_id}/review-queue")
        assert denied.status_code == 404


def test_report_candidates_follow_latest_review_decision(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """确认使证据入选，后续驳回会移除，再次确认会重新入选。"""
    case = evidence_source_case
    base = f"/projects/{case['project_id']}/runs/{case['run_id']}"
    candidates_url = f"{base}/report-candidates"
    review_url = f"{base}/evidence/{case['accepted_id']}/reviews"

    # fixture 有接受和拒绝证据，但此时没有人工确认。
    initial = client.get(candidates_url)
    assert initial.status_code == 200
    assert initial.json() == []

    confirmed = client.post(review_url, json={"decision": "confirmed"})
    assert confirmed.status_code == 201
    after_confirm = client.get(candidates_url)
    assert after_confirm.status_code == 200
    assert [item["id"] for item in after_confirm.json()] == [case["accepted_id"]]
    assert all(item["decision"] == "accepted" for item in after_confirm.json())
    assert case["rejected_id"] not in {item["id"] for item in after_confirm.json()}

    dismissed = client.post(review_url, json={"decision": "dismissed"})
    assert dismissed.status_code == 201
    assert client.get(candidates_url).json() == []

    reconfirmed = client.post(review_url, json={"decision": "confirmed"})
    assert reconfirmed.status_code == 201
    assert [item["id"] for item in client.get(candidates_url).json()] == [
        case["accepted_id"]
    ]


def test_report_candidates_enforce_project_scope_and_empty_run(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """无证据时返回空列表，不允许通过其他项目读取同一运行。"""
    case = evidence_source_case
    empty_run = client.post(
        f"/projects/{case['project_id']}/runs",
        json={"question": "新的研究问题？"},
    )
    assert empty_run.status_code == 201
    empty = client.get(
        f"/projects/{case['project_id']}/runs/{empty_run.json()['id']}"
        "/report-candidates"
    )
    assert empty.status_code == 200
    assert empty.json() == []

    other_project = client.post(
        "/projects",
        json={"title": "报告隔离测试", "research_question": "是否跨项目？"},
    )
    assert other_project.status_code == 201
    for project_id, run_id in [
        (str(uuid4()), case["run_id"]),
        (other_project.json()["id"], case["run_id"]),
        (case["project_id"], str(uuid4())),
    ]:
        response = client.get(
            f"/projects/{project_id}/runs/{run_id}/report-candidates"
        )
        assert response.status_code == 404


def test_build_report_evidence_resolves_real_paper_and_preserves_order(
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """同一来源可被多张候选证据引用，结果保持候选顺序。"""
    case = evidence_source_case
    project_id = UUID(case["project_id"])
    first = db_session.get(EvidenceRecord, UUID(case["accepted_id"]))
    assert first is not None
    second = EvidenceRecord(
        run_id=first.run_id,
        decision=EvidenceDecision.ACCEPTED,
        evidence={**first.evidence, "claim": "另一条经过确认的论断。"},
        citation_check=first.citation_check,
        claim_support_check=first.claim_support_check,
    )
    db_session.add(second)
    db_session.flush()

    assert build_report_evidence(db_session, project_id, []) == []
    items = build_report_evidence(db_session, project_id, [second, first])

    assert [item.evidence_id for item in items] == [second.id, first.id]
    assert [item.claim for item in items] == [
        "另一条经过确认的论断。",
        "该方法提高准确率。",
    ]
    assert all(item.original_filename == "source.pdf" for item in items)
    assert all(item.page_number == 1 for item in items)
    assert all(item.quote == "The method improves accuracy." for item in items)


def test_build_report_evidence_rejects_missing_foreign_and_wrong_page(
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """文本块缺失、属于别的项目或页码不符时，不生成错误引用。"""
    case = evidence_source_case
    project_id = UUID(case["project_id"])
    record = db_session.get(EvidenceRecord, UUID(case["accepted_id"]))
    assert record is not None
    original = dict(record.evidence)

    # 不自动写回测试对象的临时改动；此处只检查组装函数行为。
    with db_session.no_autoflush:
        record.evidence = {**original, "chunk_id": str(uuid4())}
        with pytest.raises(ValueError, match="Evidence source is invalid"):
            build_report_evidence(db_session, project_id, [record])

        record.evidence = {**original, "page_number": 2}
        with pytest.raises(ValueError, match="Evidence source is invalid"):
            build_report_evidence(db_session, project_id, [record])
    record.evidence = original

    other_project = ResearchProject(
        title="报告来源的其他项目",
        research_question="跨项目来源会怎样？",
    )
    db_session.add(other_project)
    db_session.flush()
    other_paper = Paper(
        project_id=other_project.id,
        original_filename="foreign.pdf",
        storage_key="tests/foreign.pdf",
        content_hash="f" * 64,
    )
    db_session.add(other_paper)
    db_session.flush()
    foreign_chunk = PaperChunkRecord(
        chunk_id=f"{other_paper.id}-p1-c1",
        paper_id=other_paper.id,
        page_number=1,
        chunk_index=1,
        text="Another project's paper.",
        content_hash=sha256(b"Another project's paper.").hexdigest(),
    )
    db_session.add(foreign_chunk)
    db_session.flush()

    with db_session.no_autoflush:
        record.evidence = {**original, "chunk_id": foreign_chunk.chunk_id}
        with pytest.raises(ValueError, match="Evidence source is invalid"):
            build_report_evidence(db_session, project_id, [record])
    record.evidence = original


def test_report_draft_requires_human_confirmation_and_cites_pdf(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """草稿随最后一次人工决定变化，并保留真实文件名和页码。"""
    case = evidence_source_case
    base = f"/projects/{case['project_id']}/runs/{case['run_id']}"
    draft_url = f"{base}/report-draft"
    review_url = f"{base}/evidence/{case['accepted_id']}/reviews"

    empty = client.get(draft_url)
    assert empty.status_code == 200
    assert empty.headers["content-type"].startswith("text/markdown")
    assert "研究问题：方法提高了什么？" in empty.text
    assert "暂无经过人工确认的证据。" in empty.text

    assert client.post(review_url, json={"decision": "confirmed"}).status_code == 201
    confirmed = client.get(draft_url)
    assert confirmed.status_code == 200
    assert "论断：该方法提高准确率。" in confirmed.text
    assert "来源：source.pdf，第 1 页" in confirmed.text
    assert f"证据 ID：{case['accepted_id']}" in confirmed.text
    assert "> The method improves accuracy." in confirmed.text
    assert case["rejected_id"] not in confirmed.text

    assert client.post(review_url, json={"decision": "dismissed"}).status_code == 201
    dismissed = client.get(draft_url)
    assert dismissed.status_code == 200
    assert "暂无经过人工确认的证据。" in dismissed.text
    assert "论断：该方法提高准确率。" not in dismissed.text


def test_report_draft_rejects_invalid_source_without_partial_markdown(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """已确认但来源缺失时返回明确错误，不生成报告正文。"""
    case = evidence_source_case
    base = f"/projects/{case['project_id']}/runs/{case['run_id']}"
    review_url = f"{base}/evidence/{case['accepted_id']}/reviews"
    assert client.post(review_url, json={"decision": "confirmed"}).status_code == 201

    record = db_session.get(EvidenceRecord, UUID(case["accepted_id"]))
    assert record is not None
    record.evidence = {**record.evidence, "chunk_id": str(uuid4())}
    db_session.commit()

    response = client.get(f"{base}/report-draft")
    assert response.status_code == 409
    assert response.json() == {"detail": "Report source is invalid"}


def test_report_draft_enforces_project_and_run_scope(
    client: TestClient,
    evidence_source_case: dict[str, str],
) -> None:
    """其他项目即使知道 run_id，也不能读取报告草稿。"""
    case = evidence_source_case
    foreign_project = client.post(
        "/projects",
        json={"title": "别的报告项目", "research_question": "能否读取？"},
    )
    assert foreign_project.status_code == 201

    for project_id, run_id in [
        (str(uuid4()), case["run_id"]),
        (foreign_project.json()["id"], case["run_id"]),
        (case["project_id"], str(uuid4())),
    ]:
        response = client.get(
            f"/projects/{project_id}/runs/{run_id}/report-draft"
        )
        assert response.status_code == 404


def test_project_report_includes_latest_human_cross_paper_comparison(
    client: TestClient,
    db_session: Session,
    evidence_source_case: dict[str, str],
) -> None:
    """人工比较结果与两条当前确认的证据一起进入项目级报告。"""
    case = evidence_source_case
    project_id = case["project_id"]
    first_run_id = UUID(case["run_id"])
    first_evidence_id = UUID(case["accepted_id"])

    # 报告与比较候选只考虑正常完成的研究运行。
    first_run = db_session.get(ResearchRun, first_run_id)
    assert first_run is not None
    first_run.status = ResearchRunStatus.COMPLETED
    db_session.commit()
    first_review_url = (
        f"/projects/{project_id}/runs/{first_run_id}"
        f"/evidence/{first_evidence_id}/reviews"
    )
    assert client.post(first_review_url, json={"decision": "confirmed"}).status_code == 201

    # 上传第二篇不同内容的论文，为它建立同一问题下的已确认论断。
    uploaded = client.post(
        f"/projects/{project_id}/papers",
        files={
            "file": (
                "second-source.pdf",
                create_test_pdf_bytes("A different study reports no accuracy gain."),
                "application/pdf",
            ),
        },
    )
    assert uploaded.status_code == 201
    second_paper_id = UUID(uploaded.json()["id"])
    second_chunk = db_session.scalar(
        select(PaperChunkRecord).where(PaperChunkRecord.paper_id == second_paper_id)
    )
    assert second_chunk is not None

    second_run_response = client.post(
        f"/projects/{project_id}/runs",
        json={"question": first_run.question},
    )
    assert second_run_response.status_code == 201
    second_run_id = UUID(second_run_response.json()["id"])
    second_run = db_session.get(ResearchRun, second_run_id)
    assert second_run is not None
    second_run.status = ResearchRunStatus.COMPLETED
    second_card = EvidenceCard(
        question=first_run.question,
        claim="另一项研究没有观察到准确率提升。",
        relation="contradicts",
        quote=second_chunk.text,
        page_number=second_chunk.page_number,
        chunk_id=second_chunk.chunk_id,
    )
    second_evidence = EvidenceRecord(
        run_id=second_run_id,
        decision=EvidenceDecision.ACCEPTED,
        evidence=second_card.model_dump(mode="json"),
        citation_check=CitationCheck(valid=True).model_dump(mode="json"),
        claim_support_check=ClaimSupportCheck(
            verdict="supported", reason="测试用证据记录。"
        ).model_dump(mode="json"),
    )
    db_session.add(second_evidence)
    db_session.commit()
    second_review_url = (
        f"/projects/{project_id}/runs/{second_run_id}"
        f"/evidence/{second_evidence.id}/reviews"
    )
    assert client.post(second_review_url, json={"decision": "confirmed"}).status_code == 201

    candidate_response = client.get(f"/projects/{project_id}/comparison-candidates")
    assert candidate_response.status_code == 200
    assert {item["evidence_id"] for item in candidate_response.json()} == {
        str(first_evidence_id), str(second_evidence.id),
    }

    comparison_response = client.post(
        f"/projects/{project_id}/comparisons",
        json={
            "left_evidence_id": str(first_evidence_id),
            "right_evidence_id": str(second_evidence.id),
            "decision": "conflicting",
            "note": "两篇论文对准确率提升的报告不同。",
        },
    )
    assert comparison_response.status_code == 201
    assert comparison_response.json()["decision"] == "conflicting"

    report_response = client.get(f"/projects/{project_id}/report-draft")
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["included_evidence_count"] == 2
    assert report["included_comparison_count"] == 1
    assert "人工判断：存在冲突" in report["markdown"]
    assert "两篇论文对准确率提升的报告不同。" in report["markdown"]


def test_resolve_evidence_chunks_checks_observation_and_project(
    db_session: Session,
) -> None:
    """只允许选择本次见过、且属于当前项目的文本块。"""

    # 准备两个项目。db_session fixture 会在测试结束后回滚数据。
    project_a = ResearchProject(
        title="项目 A",
        research_question="方法解决什么问题？",
    )
    project_b = ResearchProject(
        title="项目 B",
        research_question="方法有什么局限？",
    )
    db_session.add_all([project_a, project_b])
    db_session.flush()  # 写入项目，获得各自的 UUID。

    paper_a = Paper(
        project_id=project_a.id,
        original_filename="a.pdf",
        storage_key="tests/a.pdf",
        content_hash="a" * 64,
    )
    paper_b = Paper(
        project_id=project_b.id,
        original_filename="b.pdf",
        storage_key="tests/b.pdf",
        content_hash="b" * 64,
    )
    db_session.add_all([paper_a, paper_b])
    db_session.flush()  # 文本块需要引用已经生成的论文 ID。

    def make_chunk(
        paper_id: UUID,
        chunk_index: int,
        text: str,
    ) -> PaperChunkRecord:
        """为测试创建一条可查询的论文文本块。"""
        return PaperChunkRecord(
            chunk_id=f"{paper_id}-p1-c{chunk_index}",
            paper_id=paper_id,
            page_number=1,
            chunk_index=chunk_index,
            text=text,
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
        )

    own_first = make_chunk(paper_a.id, 1, "Project A first chunk.")
    own_second = make_chunk(paper_a.id, 2, "Project A second chunk.")
    foreign = make_chunk(paper_b.id, 1, "Project B private chunk.")
    db_session.add_all([own_first, own_second, foreign])
    db_session.flush()

    # 正常情况：两个 ID 都来自本次检索，且都属于项目 A。
    # 返回顺序应遵循 action.chunk_ids，而非数据库查询顺序。
    valid_action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=[own_second.chunk_id, own_first.chunk_id],
    )
    chunks = resolve_evidence_chunks(
        session=db_session,
        project_id=project_a.id,
        action=valid_action,
        observed_chunk_ids={own_first.chunk_id, own_second.chunk_id},
    )
    assert [chunk.chunk_id for chunk in chunks] == [
        own_second.chunk_id,
        own_first.chunk_id,
    ]
    assert [chunk.text for chunk in chunks] == [
        "Project A second chunk.",
        "Project A first chunk.",
    ]

    # 模型指定了真实存在的块，但本次运行没有检索到它。
    unseen_action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=[own_first.chunk_id],
    )
    with pytest.raises(ValueError, match="not observed"):
        resolve_evidence_chunks(
            session=db_session,
            project_id=project_a.id,
            action=unseen_action,
            observed_chunk_ids=set(),
        )

    # 重复选择同一个块，应在读取数据库前被拒绝。
    duplicate_action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=[own_first.chunk_id, own_first.chunk_id],
    )
    with pytest.raises(ValueError, match="unique"):
        resolve_evidence_chunks(
            session=db_session,
            project_id=project_a.id,
            action=duplicate_action,
            observed_chunk_ids={own_first.chunk_id},
        )

    # 故意把项目 B 的块放进“已观察集合”，模拟上层状态出错。
    # 数据库中的项目归属检查仍应阻止读取其正文。
    foreign_action = ExtractEvidenceAction(
        kind="extract_evidence",
        chunk_ids=[foreign.chunk_id],
    )
    with pytest.raises(ValueError, match="unavailable"):
        resolve_evidence_chunks(
            session=db_session,
            project_id=project_a.id,
            action=foreign_action,
            observed_chunk_ids={foreign.chunk_id},
        )

def test_execute_route_explicitly_selects_agent_mode(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 只在显式指定 agent 时，把 Agent 模式交给执行器。"""

    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    # 此测试只检查“路由 → 执行器”的参数传递。
    # 返回现有运行对象，避免调用真实模型或改变运行状态。
    stored_run = db_session.get(ResearchRun, UUID(run_id))
    fake_execute = MagicMock(return_value=stored_run)
    monkeypatch.setattr(
        "littrace.api.routes.research_runs.execute_research_run",
        fake_execute,
    )

    response = client.post(
        f"/projects/{research_project_id}/runs/"
        f"{run_id}/execute?execution_mode=agent"
    )

    assert response.status_code == 200
    fake_execute.assert_called_once_with(
        session=db_session,
        project_id=UUID(research_project_id),
        run_id=UUID(run_id),
        execution_mode="agent",
    )

    # 非法模式应被 FastAPI 拒绝，不应触发执行器。
    fake_execute.reset_mock()
    invalid = client.post(
        f"/projects/{research_project_id}/runs/"
        f"{run_id}/execute?execution_mode=unknown"
    )
    assert invalid.status_code == 422
    fake_execute.assert_not_called()


@pytest.mark.parametrize(
    ("stop_reason", "steps_used"),
    [
        ("finish", 1),
        ("step_limit", 6),
    ],
)
def test_agent_execution_mode_is_saved(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
    stop_reason: str,
    steps_used: int,
) -> None:
    """Agent 模式应与运行状态一起保存，而不只是传给执行器。"""

    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    assert created.json()["execution_mode"] is None
    run_id = created.json()["id"]

    fake_steps = []

    for number in range(1, steps_used + 1):
        is_finish = stop_reason == "finish" and number == steps_used

        if is_finish:
            action = FinishAction(
                kind="finish",
                reason="No further action needed.",
            )
            observation = None
        else:
            action = SearchAction(
                kind="search",
                strategy="keyword",
                query=f"test query {number}",
            )
            observation = '{"tool":"search","matches":[]}'

        fake_steps.append(
            AgentStepTrace(
                step_number=number,
                action=action,
                observation_summary=observation,
            )
        )

    # 只模拟 Agent 循环；路由、执行器和数据库写入均使用真实代码。
    fake_loop = MagicMock(
    return_value=AgentLoopResult(
            evidence=EvidenceGateResult(),
            stop_reason=stop_reason,
            steps_used=steps_used,
            steps=fake_steps,
            finish_reason=(
                "No further action needed."
                if stop_reason == "finish"
                else None
            ),
        )
    )
    monkeypatch.setattr(
        "littrace.research.executor.run_agent_loop",
        fake_loop,
    )

    executed = client.post(
        f"/projects/{research_project_id}/runs/"
        f"{run_id}/execute?execution_mode=agent"
    )

    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert executed.json()["execution_mode"] == "agent"
    fake_loop.assert_called_once()

    # 再从数据库读取，证明模式并非只存在于响应 JSON 中。
    stored = db_session.get(ResearchRun, UUID(run_id))
    db_session.refresh(stored)
    assert stored.execution_mode == "agent"

    assert created.json()["agent_stop_reason"] is None
    assert created.json()["agent_steps_used"] is None

    assert executed.json()["agent_stop_reason"] == stop_reason
    assert executed.json()["agent_steps_used"] == steps_used

    assert stored.agent_stop_reason == stop_reason
    assert stored.agent_steps_used == steps_used
    stored_steps = db_session.scalars(
        select(AgentStepRecord)
        .where(AgentStepRecord.run_id == UUID(run_id))
        .order_by(AgentStepRecord.step_number)
    ).all()

    assert [step.step_number for step in stored_steps] == list(
        range(1, steps_used + 1)
    )
    assert stored_steps[-1].action_kind == (
        "finish" if stop_reason == "finish" else "search"
    )
        # 从 HTTP 接口读取刚才由执行器保存的轨迹。
    response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}/steps"
    )
    assert response.status_code == 200

    steps = response.json()
    assert [step["step_number"] for step in steps] == list(
        range(1, steps_used + 1)
    )
    assert [step["action_payload"]["kind"] for step in steps] == [
        trace.action.kind for trace in fake_steps
    ]
    assert steps[-1]["observation_summary"] == (
        None if stop_reason == "finish"
        else '{"tool":"search","matches":[]}'
    )

def test_save_agent_steps_preserves_order_and_can_rollback(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> None:
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201
    run_id = UUID(created.json()["id"])

    # 模拟执行器领取运行后已写入 agent 模式。
    run = db_session.get(ResearchRun, run_id)
    run.execution_mode = "agent"
    db_session.commit()

    steps = [
        AgentStepTrace(
            step_number=1,
            action=SearchAction(
                kind="search",
                strategy="hybrid",
                query="cross-modal alignment",
                top_k=3,
            ),
            observation_summary='{"tool":"search","matches":[]}',
        ),
        AgentStepTrace(
            step_number=2,
            action=FinishAction(
                kind="finish",
                reason="No further results.",
            ),
            observation_summary=None,
        ),
    ]

    saved = save_agent_steps(
        session=db_session,
        run_id=run_id,
        steps=steps,
    )

    statement = (
        select(AgentStepRecord)
        .where(AgentStepRecord.run_id == run_id)
        .order_by(AgentStepRecord.step_number)
    )
    rows = db_session.scalars(statement).all()

    assert len(saved) == 2
    assert [row.step_number for row in rows] == [1, 2]
    assert [row.action_kind for row in rows] == ["search", "finish"]
    assert rows[0].action_payload["query"] == "cross-modal alignment"
    assert rows[1].observation_summary is None

    # 保存函数没有 commit，因此当前事务可整体撤销。
    db_session.rollback()
    assert db_session.scalars(statement).all() == []


def test_save_agent_steps_rejects_non_agent_run(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> None:
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    assert created.status_code == 201

    with pytest.raises(ValueError, match="agent research run"):
        save_agent_steps(
            session=db_session,
            run_id=UUID(created.json()["id"]),
            steps=[],
        )


def test_research_run_retry_link_survives_source_deletion(
    db_session: Session,
    research_project_id: str,
) -> None:
    """保存重试来源；删除来源时保留子运行并清空关联。"""
    source = ResearchRun(
        project_id=UUID(research_project_id),
        question="论文解决什么问题？",
        status=ResearchRunStatus.FAILED,
    )
    db_session.add(source)
    db_session.flush()
    retry = ResearchRun(
        project_id=source.project_id,
        question=source.question,
        retry_of_run_id=source.id,
    )
    db_session.add(retry)
    db_session.flush()
    db_session.refresh(retry)
    assert retry.retry_of_run_id == source.id
    assert retry.status == ResearchRunStatus.PENDING

    # SET NULL 由数据库完成，刷新 ORM 对象后才能看到新值。
    db_session.delete(source)
    db_session.flush()
    db_session.refresh(retry)
    assert retry.retry_of_run_id is None
    assert retry.status == ResearchRunStatus.PENDING


@pytest.mark.parametrize(
    ("source_kind", "constraint_name"),
    [
        ("self", "ck_research_runs_not_self_retry"),
        ("missing", "fk_research_runs_retry_of"),
    ],
)
def test_research_run_rejects_invalid_retry_source(
    db_session: Session,
    research_project_id: str,
    source_kind: str,
    constraint_name: str,
) -> None:
    run_id = uuid4()
    run = ResearchRun(
        id=run_id,
        project_id=UUID(research_project_id),
        question="论文解决什么问题？",
        retry_of_run_id=run_id if source_kind == "self" else uuid4(),
    )
    # 保存点避免预期中的约束错误破坏测试的其余事务。
    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            db_session.add(run)
            db_session.flush()
    assert error.value.orig.diag.constraint_name == constraint_name


@pytest.mark.parametrize("source_status", list(ResearchRunStatus))
def test_retry_only_accepts_failed_runs(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    source_status: ResearchRunStatus,
) -> None:
    source = ResearchRun(
        project_id=UUID(research_project_id),
        question="论文解决什么问题？",
        top_k=5,
        max_cards=2,
        status=source_status,
        execution_mode="agent" if source_status == ResearchRunStatus.FAILED else None,
        error_message="Original failure" if source_status == ResearchRunStatus.FAILED else None,
    )
    db_session.add(source)
    db_session.commit()
    source_id = source.id

    if source_status == ResearchRunStatus.FAILED:
        # 为旧运行准备一条轨迹，验证重试不会复制或清空它。
        save_agent_steps(
            session=db_session,
            run_id=source_id,
            steps=[AgentStepTrace(
                step_number=1,
                action=SearchAction(kind="search", strategy="keyword", query="OmniAgent"),
                observation_summary='{"tool":"search","matches":[]}',
            )],
        )
        db_session.commit()

    response = client.post(
        f"/projects/{research_project_id}/runs/{source_id}/retry"
    )
    if source_status != ResearchRunStatus.FAILED:
        assert response.status_code == 409
        # 被拒绝的请求不应留下任何新运行。
        children = db_session.scalars(
            select(ResearchRun).where(ResearchRun.retry_of_run_id == source_id)
        ).all()
        assert children == []
        return

    assert response.status_code == 201
    retry = response.json()
    assert retry["id"] != str(source_id)
    assert retry["project_id"] == research_project_id
    assert retry["retry_of_run_id"] == str(source_id)
    assert retry["status"] == "pending"
    assert retry["question"] == source.question
    assert retry["top_k"] == 5
    assert retry["max_cards"] == 2
    for field in (
        "execution_mode", "error_message", "started_at", "finished_at",
        "agent_stop_reason", "agent_steps_used",
    ):
        assert retry[field] is None

    original = client.get(f"/projects/{research_project_id}/runs/{source_id}")
    assert original.status_code == 200
    assert original.json()["status"] == "failed"
    assert original.json()["error_message"] == "Original failure"
    for suffix in ("steps", "evidence"):
        empty = client.get(
            f"/projects/{research_project_id}/runs/{retry['id']}/{suffix}"
        )
        assert empty.status_code == 200
        assert empty.json() == []
    old_steps = client.get(f"/projects/{research_project_id}/runs/{source_id}/steps")
    assert old_steps.status_code == 200
    assert len(old_steps.json()) == 1


def test_failed_agent_run_can_retry_and_complete_independently(
    client: TestClient,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 第一次运行搜索后失败；新运行随后主动结束。
    # 仅模拟规划和搜索，循环、执行器、路由及数据库使用真实代码。
    planner = MagicMock(side_effect=[
        AgentDecision(action=SearchAction(
            kind="search",
            strategy="keyword",
            query="OmniAgent",
        )),
        ValueError("Simulated planner failure"),
        AgentDecision(action=FinishAction(
            kind="finish",
            reason="Retry completed.",
        )),
    ])
    monkeypatch.setattr("littrace.research.agent_loop.choose_next_action", planner)
    monkeypatch.setattr(
        "littrace.research.agent_loop.execute_search_action",
        lambda **kwargs: [],
    )

    prefix = f"/projects/{research_project_id}/runs"
    created = client.post(prefix, json={"question": "论文解决什么问题？"})
    assert created.status_code == 201
    old_id = created.json()["id"]

    failed = client.post(f"{prefix}/{old_id}/execute?execution_mode=agent")
    assert failed.status_code == 502
    old_run = client.get(f"{prefix}/{old_id}")
    old_steps = client.get(f"{prefix}/{old_id}/steps")
    assert old_run.status_code == 200
    assert old_run.json()["status"] == "failed"
    assert old_steps.status_code == 200
    assert len(old_steps.json()) == 1

    retried = client.post(f"{prefix}/{old_id}/retry")
    assert retried.status_code == 201
    new_id = retried.json()["id"]
    assert new_id != old_id
    assert retried.json()["status"] == "pending"
    assert retried.json()["retry_of_run_id"] == old_id

    completed = client.post(f"{prefix}/{new_id}/execute?execution_mode=agent")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["execution_mode"] == "agent"
    assert completed.json()["retry_of_run_id"] == old_id

    new_steps = client.get(f"{prefix}/{new_id}/steps")
    assert new_steps.status_code == 200
    assert len(new_steps.json()) == 1
    assert new_steps.json()[0]["action_kind"] == "finish"
    assert new_steps.json()[0]["run_id"] == new_id

    # 重试完成后，旧运行的状态、错误及原始轨迹完全保留。
    assert client.get(f"{prefix}/{old_id}").json() == old_run.json()
    assert client.get(f"{prefix}/{old_id}/steps").json() == old_steps.json()

    repeated = client.post(f"{prefix}/{new_id}/execute?execution_mode=agent")
    assert repeated.status_code == 409
    assert planner.call_count == 3


def test_list_research_runs_filters_and_paginates(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> None:
    prefix = f"/projects/{research_project_id}/runs"
    empty = client.get(prefix)
    assert empty.status_code == 200
    assert empty.json() == []

    created_runs = []
    for number in range(3):
        response = client.post(prefix, json={"question": f"研究问题 {number}"})
        assert response.status_code == 201
        created_runs.append(response.json())

    failed_id = created_runs[0]["id"]
    run = db_session.get(ResearchRun, UUID(failed_id))
    run.status = ResearchRunStatus.FAILED
    db_session.commit()

    # 另一个项目也有运行，验证列表不会混入它。
    other = client.post(
        "/projects",
        json={"title": "其他项目", "research_question": "其他问题"},
    )
    assert other.status_code == 201
    foreign_run = client.post(
        f"/projects/{other.json()['id']}/runs",
        json={"question": "不应混入列表"},
    )
    assert foreign_run.status_code == 201

    # 测试事务内的创建时间可能相同，因此也按 ID 比较稳定顺序。
    expected = sorted(
        created_runs,
        key=lambda item: (item["created_at"], item["id"]),
        reverse=True,
    )
    listed = client.get(prefix)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [
        item["id"] for item in expected
    ]

    page = client.get(prefix, params={"offset": 1, "limit": 1})
    assert page.status_code == 200
    assert [item["id"] for item in page.json()] == [expected[1]["id"]]

    filtered = client.get(prefix, params={"status": "failed"})
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [failed_id]
    no_match = client.get(prefix, params={"status": "running"})
    assert no_match.status_code == 200
    assert no_match.json() == []

    for invalid in ({"limit": 101}, {"offset": -1}, {"status": "unknown"}):
        assert client.get(prefix, params=invalid).status_code == 422
    assert client.get(f"/projects/{uuid4()}/runs").status_code == 404


def test_paper_file_returns_original_pdf(
    client: TestClient,
    research_project_id: str,
    tmp_path: Path,
) -> None:
    pdf_content = create_test_pdf_bytes()
    uploaded = client.post(
        f"/projects/{research_project_id}/papers",
        files={"file": ("paper.pdf", pdf_content, "application/pdf")},
    )
    assert uploaded.status_code == 201
    paper_id = uploaded.json()["id"]
    url = f"/projects/{research_project_id}/papers/{paper_id}/file"

    response = client.get(url)
    assert response.status_code == 200
    assert response.content == pdf_content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")

    other = client.post(
        "/projects",
        json={"title": "其他项目", "research_question": "其他问题"},
    )
    assert other.status_code == 201
    blocked = client.get(f"/projects/{other.json()['id']}/papers/{paper_id}/file")
    assert blocked.status_code == 404
    assert client.get(
        f"/projects/{research_project_id}/papers/{uuid4()}/file"
    ).status_code == 404
    assert client.get(f"/projects/{uuid4()}/papers/{paper_id}/file").status_code == 404

    # 只删除测试临时目录中的 PDF，模拟元数据尚在但原文件丢失。
    stored_file = tmp_path / research_project_id / f"{paper_id}.pdf"
    stored_file.unlink()
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Stored PDF not found"


def test_async_run_queues_once_and_returns_immediately(
    client: TestClient,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模拟 Redis 队列，验证 API 只提交一次任务且不调用模型。"""
    queue = MagicMock()
    monkeypatch.setattr(
        "littrace.api.routes.research_runs.get_research_queue",
        lambda: queue,
    )
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    run_id = created.json()["id"]
    url = f"/projects/{research_project_id}/runs/{run_id}/execute-async"

    accepted = client.post(url, params={"execution_mode": "agent"})
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"
    assert accepted.json()["execution_mode"] == "agent"
    assert accepted.json()["started_at"] is None
    queue.enqueue.assert_called_once_with(
        "littrace.research.jobs.execute_queued_research_run",
        run_id,
        job_id=run_id,
        unique=True,
        job_timeout=3600,
        result_ttl=86400,
        failure_ttl=604800,
    )
    assert client.get(f"/projects/{research_project_id}/runs/{run_id}").json()["status"] == "queued"
    assert client.post(url).status_code == 409
    queue.enqueue.assert_called_once()
    assert client.post(f"/projects/{research_project_id}/runs/{uuid4()}/execute-async").status_code == 404
    assert client.post(url, params={"execution_mode": "other"}).status_code == 422
    other = client.post(
        "/projects",
        json={"title": "其他项目", "research_question": "其他问题"},
    ).json()["id"]
    assert client.get(f"/projects/{other}/runs/{run_id}/events").status_code == 404
    assert client.post(f"/projects/{other}/runs/{run_id}/execute-async").status_code == 404


def test_async_run_enqueue_failure_does_not_leave_queued_run(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入队失败时将运行标记失败，便于用户从现有重试接口重新开始。"""
    queue = MagicMock()
    queue.enqueue.side_effect = RuntimeError("simulated Redis failure")
    monkeypatch.setattr(
        "littrace.api.routes.research_runs.get_research_queue",
        lambda: queue,
    )
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    run_id = created.json()["id"]

    response = client.post(
        f"/projects/{research_project_id}/runs/{run_id}/execute-async",
        params={"execution_mode": "agent"},
    )

    assert response.status_code == 503
    db_session.expire_all()
    run = db_session.get(ResearchRun, UUID(run_id))
    assert run is not None
    assert run.status == ResearchRunStatus.FAILED
    assert run.started_at is None
    assert run.finished_at is not None
    assert run.error_message == "后台任务提交失败，请创建重试运行"


def test_live_agent_step_can_be_read_before_completion(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每步提交后，已有步骤 API 与事件流均可读取到轨迹。"""
    from contextlib import nullcontext
    from littrace.api.routes import research_runs as run_routes
    from littrace.research.executor import claim_research_run

    monkeypatch.setattr(
        run_routes, "get_research_queue", MagicMock(),
    )
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "论文解决什么问题？"},
    )
    run_id = created.json()["id"]
    response = client.post(
        f"/projects/{research_project_id}/runs/{run_id}/execute-async",
        params={"execution_mode": "agent"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"

    # 在真实流程中由 Worker 领取；测试里手动完成状态转换，
    # 否则 queued 运行不允许持久化 Agent 步骤。
    claim_research_run(
        session=db_session,
        project_id=UUID(research_project_id),
        run_id=UUID(run_id),
        execution_mode="agent",
        expected_status=ResearchRunStatus.QUEUED,
    )

    step = AgentStepTrace(
        step_number=1,
        action=FinishAction(kind="finish", reason="已取得足够证据"),
        observation_summary=None,
    )
    append_agent_step(session=db_session, run_id=UUID(run_id), step=step)
    db_session.commit()
    steps = client.get(f"/projects/{research_project_id}/runs/{run_id}/steps")
    assert steps.status_code == 200
    assert [item["step_number"] for item in steps.json()] == [1]
    with pytest.raises(ValueError, match="last step"):
        append_agent_step(session=db_session, run_id=UUID(run_id), step=step)
    db_session.rollback()

    # 事件生成器平常使用独立数据库会话；测试里让它读取同一事务。
    monkeypatch.setattr(run_routes, "SessionLocal", lambda: nullcontext(db_session))
    live_events = run_routes.stream_run_events(UUID(research_project_id), UUID(run_id))
    assert "event: step" in next(live_events)
    assert '"running"' in next(live_events)
    live_events.close()

    run = db_session.get(ResearchRun, UUID(run_id))
    run.status = ResearchRunStatus.COMPLETED
    db_session.commit()
    events = list(run_routes.stream_run_events(UUID(research_project_id), UUID(run_id)))
    assert any("event: step" in event and '"step_number": 1' in event for event in events)
    assert any("event: status" in event and '"completed"' in event for event in events)
    assert not any("keep-alive" in event for event in events)

    resumed = client.get(
        f"/projects/{research_project_id}/runs/{run_id}/events",
        headers={"Last-Event-ID": "1"},
    )
    assert resumed.status_code == 200
    assert resumed.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in resumed.text
    assert "event: step" not in resumed.text


def test_run_metrics_and_memory_are_auditable(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> None:
    """指标采用当前复核结论，记忆能追溯到来源运行与来源证据。"""

    prefix = f"/projects/{research_project_id}/runs"
    source_id = client.post(
        prefix,
        json={"question": "历史问题"},
    ).json()["id"]
    current_id = client.post(
        prefix,
        json={"question": "当前问题"},
    ).json()["id"]

    source_run = db_session.get(ResearchRun, UUID(source_id))
    source_run.status = ResearchRunStatus.COMPLETED
    current_run = db_session.get(ResearchRun, UUID(current_id))
    current_run.status = ResearchRunStatus.COMPLETED
    current_run.execution_mode = "agent"
    current_run.agent_stop_reason = "finish"
    current_run.agent_steps_used = 2
    current_run.model_call_count = 3
    current_run.model_prompt_tokens = 1_000
    current_run.model_completion_tokens = 250
    current_run.model_total_tokens = 1_250
    current_run.model_duration_ms = 1_800
    current_run.estimated_cost_microusd = 1_234

    source_card = EvidenceCard(
        question="历史问题",
        claim="历史证据已被人工确认。",
        relation="supports",
        quote="The prior evidence was confirmed.",
        page_number=2,
        chunk_id="source-p2-c1",
    )
    current_card = EvidenceCard(
        question="当前问题",
        claim="当前运行找到了一条证据。",
        relation="supports",
        quote="The current run found evidence.",
        page_number=3,
        chunk_id="current-p3-c1",
    )
    source_evidence = EvidenceRecord(
        run_id=UUID(source_id),
        decision=EvidenceDecision.ACCEPTED,
        evidence=source_card.model_dump(mode="json"),
        citation_check=CitationCheck(valid=True).model_dump(mode="json"),
        claim_support_check=ClaimSupportCheck(
            verdict="supported", reason="引文支持论断。"
        ).model_dump(mode="json"),
    )
    current_evidence = EvidenceRecord(
        run_id=UUID(current_id),
        decision=EvidenceDecision.ACCEPTED,
        evidence=current_card.model_dump(mode="json"),
        citation_check=CitationCheck(valid=True).model_dump(mode="json"),
        claim_support_check=ClaimSupportCheck(
            verdict="supported", reason="引文支持论断。"
        ).model_dump(mode="json"),
    )
    db_session.add_all([source_evidence, current_evidence])
    db_session.flush()
    db_session.add_all([
        EvidenceReview(
            evidence_id=source_evidence.id,
            decision=ReviewDecision.CONFIRMED,
        ),
        EvidenceReview(
            evidence_id=current_evidence.id,
            decision=ReviewDecision.CONFIRMED,
        ),
        ResearchRunMemory(
            run_id=UUID(current_id),
            source_evidence_id=source_evidence.id,
            summary="历史证据已被人工确认。",
        ),
    ])
    db_session.commit()

    loaded_memory = load_cross_run_memory(
        session=db_session,
        project_id=UUID(research_project_id),
        current_run_id=UUID(current_id),
    )
    assert [item.source_evidence_id for item in loaded_memory] == [
        source_evidence.id
    ]

    metrics = client.get(f"{prefix}/{current_id}/metrics")
    assert metrics.status_code == 200
    data = metrics.json()
    assert data["accepted_evidence_count"] == 1
    assert data["gate_acceptance_rate"] == 1.0
    assert data["human_confirmed_count"] == 1
    assert data["unique_evidence_chunk_count"] == 1
    assert data["cross_run_memory_count"] == 1
    assert data["warnings"] == []
    assert data["model_usage"] == {
        "call_count": 3,
        "prompt_tokens": 1_000,
        "completion_tokens": 250,
        "total_tokens": 1_250,
        "duration_ms": 1_800,
        "estimated_cost_usd": "0.001234",
    }

    memory = client.get(f"{prefix}/{current_id}/memory")
    assert memory.status_code == 200
    assert len(memory.json()) == 1
    assert memory.json()[0]["source_run_id"] == source_id
    assert memory.json()[0]["source_evidence_id"] == str(source_evidence.id)

    # 即使知道 run_id，也不能借另一个项目路径读取指标或记忆。
    assert client.get(f"/projects/{uuid4()}/runs/{current_id}/metrics").status_code == 404
    assert client.get(f"/projects/{uuid4()}/runs/{current_id}/memory").status_code == 404


def test_project_synthesis_requires_cross_paper_confirmed_evidence(
    client: TestClient,
    research_project_id: str,
) -> None:
    url = f"/projects/{research_project_id}/syntheses"
    assert client.get(url).json() == []

    # 当前项目还没有两篇论文针对同一问题的人工确认证据，必须在调用模型前拒绝。
    response = client.post(url, json={"question": "共同问题？"})
    assert response.status_code == 409
    assert "At least two papers" in response.json()["detail"]
    assert client.get(f"/projects/{uuid4()}/syntheses").status_code == 404


def test_agent_synthesis_action_is_persisted_and_returned(
    client: TestClient,
    db_session: Session,
    research_project_id: str,
) -> None:
    created = client.post(
        f"/projects/{research_project_id}/runs",
        json={"question": "共同问题？"},
    ).json()
    run_id = UUID(created["id"])
    run = db_session.get(ResearchRun, run_id)
    run.status = ResearchRunStatus.RUNNING
    run.execution_mode = "agent"
    db_session.commit()

    append_agent_step(
        session=db_session,
        run_id=run_id,
        step=AgentStepTrace(
            step_number=1,
            action=SynthesizeProjectAction(
                kind="synthesize_project",
                max_sources=10,
                max_points=4,
            ),
            observation_summary=(
                '{"tool":"synthesize_project","accepted_point_count":2}'
            ),
        ),
    )
    db_session.commit()

    response = client.get(
        f"/projects/{research_project_id}/runs/{run_id}/steps"
    )
    assert response.status_code == 200
    assert response.json()[0]["action_kind"] == "synthesize_project"
    assert response.json()[0]["action_payload"]["max_points"] == 4
