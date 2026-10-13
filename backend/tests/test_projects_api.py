from collections.abc import Generator

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from littrace.api.app import create_app
from littrace.database.session import (
    engine,
    get_db_session,
)

from littrace.config import get_settings
 
    
@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """
    为每个测试提供一个独立的 FastAPI 测试客户端。

    fixture 默认每个测试执行一次，因此不同测试之间
    不会共享数据库事务和测试数据。
    """
    # 上传接口内部通过 get_settings() 获取存储目录。
    # 测试时将其替换为 pytest 创建的临时目录，
    # 防止测试 PDF 写入正式的 storage/papers。
    settings = get_settings()

    monkeypatch.setattr(
        settings,
        "paper_storage_dir",
        tmp_path,
    )

    connection = engine.connect()
    outer_transaction = connection.begin()

    # 使用保存点承接业务代码中的 commit()。
    # 最外层事务仍然由测试控制。
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    def override_get_db_session() -> Generator[
        Session,
        None,
        None,
    ]:
        yield session

    app = create_app()
    app.dependency_overrides[
        get_db_session
    ] = override_get_db_session

    try:
        # yield 之前是测试准备阶段。
        # yield 出去的 TestClient 会传给测试函数。
        with TestClient(app) as test_client:
            yield test_client
    finally:
        # yield 之后是测试清理阶段。
        app.dependency_overrides.clear()
        session.close()

        # 撤销该测试产生的全部数据库数据。
        outer_transaction.rollback()
        connection.close()


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