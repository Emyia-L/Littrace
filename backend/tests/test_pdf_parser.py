from pathlib import Path
import pymupdf
from littrace.ingestion.pdf_parser import extract_pages

def create_test_pdf(pdf_path: Path) -> None:
    """
    创建一个简单的测试PDF文件
    """
    document = pymupdf.open()

    first_page = document.new_page()
    first_page.insert_text(
        (72, 72),
        "This is the first page.",
    )

    second_page = document.new_page()
    second_page.insert_text(
        (72, 72),
        "This is the second page.",
    )

    document.save(pdf_path)
    document.close()

def test_extract_pages_preserves_page_numbers(
        tmp_path: Path
) -> None:
    
    pdf_path = tmp_path / "test-paper.pdf"
    create_test_pdf(pdf_path)

    pages = extract_pages(pdf_path)

    assert len(pages) == 2

    assert pages[0].page_number == 1
    assert "This is the first page." in pages[0].text

    assert pages[1].page_number == 2
    assert "This is the second page." in pages[1].text
