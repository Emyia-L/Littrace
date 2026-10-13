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