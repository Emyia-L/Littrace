from pathlib import Path
from uuid import uuid4

import pytest

from littrace.storage.paper_storage import (
    calculate_content_hash,
    delete_paper_file,
    resolve_storage_path,
    save_paper_file,
)


def test_content_hash_is_deterministic() -> None:
    """
    相同内容必须产生相同的 SHA-256 哈希。

    deterministic 表示输入相同，输出始终相同。
    """

    content = b"same PDF content"

    first_hash = calculate_content_hash(content)
    second_hash = calculate_content_hash(content)

    assert first_hash == second_hash

    # SHA-256 的十六进制字符串固定为 64 个字符。
    assert len(first_hash) == 64


def test_different_content_has_different_hash() -> None:
    """不同文件内容应该产生不同哈希。"""

    first_hash = calculate_content_hash(
        b"first PDF"
    )
    second_hash = calculate_content_hash(
        b"second PDF"
    )

    assert first_hash != second_hash


def test_save_paper_file(
    tmp_path: Path,
) -> None:
    """
    PDF 应该被保存到项目目录下。

    tmp_path 是 pytest 自动创建的临时目录，
    测试结束后会被自动清理。
    """

    project_id = uuid4()
    paper_id = uuid4()
    content = b"%PDF-1.7 fake PDF content"

    storage_key = save_paper_file(
        content=content,
        project_id=project_id,
        paper_id=paper_id,
        storage_root=tmp_path,
    )

    expected_key = (
        f"{project_id}/{paper_id}.pdf"
    )

    assert storage_key == expected_key

    saved_file = tmp_path / storage_key

    assert saved_file.exists()
    assert saved_file.read_bytes() == content


def test_delete_paper_file(
    tmp_path: Path,
) -> None:
    """
    删除函数应该移除文件。

    重复删除同一文件也不应该报错，
    这种特性称为幂等。
    """

    project_id = uuid4()
    paper_id = uuid4()

    storage_key = save_paper_file(
        content=b"%PDF-1.7 test",
        project_id=project_id,
        paper_id=paper_id,
        storage_root=tmp_path,
    )

    saved_file = tmp_path / storage_key
    assert saved_file.exists()

    delete_paper_file(
        storage_root=tmp_path,
        storage_key=storage_key,
    )

    assert not saved_file.exists()

    # 第二次删除不存在的文件，也不应抛出异常。
    delete_paper_file(
        storage_root=tmp_path,
        storage_key=storage_key,
    )


def test_empty_file_is_rejected(
    tmp_path: Path,
) -> None:
    """空文件不应该被保存。"""

    with pytest.raises(
        ValueError,
        match="Paper file is empty",
    ):
        save_paper_file(
            content=b"",
            project_id=uuid4(),
            paper_id=uuid4(),
            storage_root=tmp_path,
        )


def test_path_traversal_is_rejected(
    tmp_path: Path,
) -> None:
    """
    相对路径不能逃离存储根目录。

    ../../secret.txt 是典型的路径穿越输入。
    """

    with pytest.raises(
        ValueError,
        match=(
            "Storage key points outside "
            "storage root"
        ),
    ):
        resolve_storage_path(
            storage_root=tmp_path,
            storage_key="../../secret.txt",
        )