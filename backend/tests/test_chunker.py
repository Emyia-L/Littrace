from littrace.domain.models import PageText
from littrace.ingestion.chunker import chunk_pages

def test_chunks_do_not_cross_pages() -> None:
    pages = [
        PageText(
            page_number=1,
            text="alpha " * 30,
        ),
        PageText(
            page_number=2,
            text="beta " * 30,
        ),
    ]

    chunks = chunk_pages(
        pages=pages,
        paper_id="paper-001",
        max_chars=40,
        overlap=10,
    )

    page_one_chunks = [
        chunk
        for chunk in chunks
        if chunk.page_number == 1
    ]

    page_two_chunks = [
        chunk
        for chunk in chunks
        if chunk.page_number == 2
    ]

    assert page_one_chunks
    assert page_two_chunks

    assert page_one_chunks[0].chunk_id == "paper-001-p1-c1"
    assert page_two_chunks[0].chunk_id == "paper-001-p2-c1"

    assert all(
        "beta" not in chunk.text
        for chunk in page_one_chunks
    )

    assert all(
        "alpha" not in chunk.text
        for chunk in page_two_chunks
    )

    assert all(
        len(chunk.text) <= 40
        for chunk in chunks
    )

    # 每一页的第一个块都应该从 1 开始编号。
    assert page_one_chunks[0].chunk_index == 1
    assert page_two_chunks[0].chunk_index == 1

    # 每页内部的 chunk_index 应连续递增。
    assert [
        chunk.chunk_index
        for chunk in page_one_chunks
    ] == list(
        range(1, len(page_one_chunks) + 1)
    )

    assert [
        chunk.chunk_index
        for chunk in page_two_chunks
    ] == list(
        range(1, len(page_two_chunks) + 1)
    )


def test_chunks_remove_pdf_nul_before_storage() -> None:
    """PDF 文本层偶尔带 NUL；PostgreSQL text 不接受该字符。"""
    chunks = chunk_pages(
        pages=[PageText(page_number=1, text="alpha\x00beta gamma")],
        paper_id="paper-nul",
    )

    assert len(chunks) == 1
    assert chunks[0].text == "alphabeta gamma"
    assert "\x00" not in chunks[0].text
