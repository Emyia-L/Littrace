from hashlib import sha256
from pathlib import Path
from uuid import UUID

def calculate_content_hash(
    content: bytes,
) -> str:
    """
    计算文件内容的 SHA-256 哈希。

    相同文件内容一定会产生相同哈希，
    因此可以用它判断 PDF 是否重复上传。
    """

    return sha256(content).hexdigest()

def build_storage_key(
    project_id: UUID,
    paper_id: UUID,
) -> str:
    """
    生成数据库中保存的相对文件路径。

    示例：
        项目UUID/论文UUID.pdf

    数据库只保存相对路径，不保存依赖当前电脑的
    D:\\... 绝对路径。
    """

    return (
        Path(str(project_id))
        / f"{paper_id}.pdf"
    ).as_posix()

def resolve_storage_path(
    storage_root: Path,
    storage_key: str,
) -> Path:
    """
    将相对存储路径转换为磁盘绝对路径。

    同时检查最终路径是否仍位于 storage_root 中，
    防止类似 ../../secret.txt 的路径穿越问题。
    """

    resolved_root = storage_root.resolve()
    resolved_path = (
        resolved_root / storage_key
    ).resolve()

    if not resolved_path.is_relative_to(
        resolved_root
    ):
        raise ValueError(
            "Storage key points outside storage root"
        )

    return resolved_path

def save_paper_file(
    *,
    content: bytes,
    project_id: UUID,
    paper_id: UUID,
    storage_root: Path,
) -> str:
    """
    将 PDF 字节保存到本地磁盘。

    Returns:
        str: 应当保存进数据库的相对存储路径。
    """

    if not content:
        raise ValueError("Paper file is empty")

    storage_key = build_storage_key(
        project_id=project_id,
        paper_id=paper_id,
    )

    destination = resolve_storage_path(
        storage_root=storage_root,
        storage_key=storage_key,
    )

    # parents=True 会同时创建缺失的项目目录；
    # exist_ok=True 表示目录已存在时不报错。
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # 先写临时文件，再替换成正式文件。
    #
    # 这样可以降低程序在写入中途退出时，
    # 留下不完整正式文件的风险。
    temporary_path = destination.with_suffix(
        ".tmp"
    )
    temporary_path.write_bytes(content)
    temporary_path.replace(destination)

    return storage_key

def delete_paper_file(
    *,
    storage_root: Path,
    storage_key: str,
) -> None:
    """
    删除已经保存的论文文件。

    后续如果数据库写入失败，可以调用此函数
    清理磁盘上已经保存的文件。
    """

    file_path = resolve_storage_path(
        storage_root=storage_root,
        storage_key=storage_key,
    )

    # missing_ok=True 表示文件不存在时也不报错，
    # 便于执行重复清理。
    file_path.unlink(missing_ok=True)