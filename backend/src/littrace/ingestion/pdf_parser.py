from pathlib import Path
import pymupdf
from littrace.domain.models import PageText

def extract_pages(pdf_path: str | Path) -> list[PageText]:
    """
    读取pdf，按页数返回文本
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF未找到: {pdf_path}")

    if not path.is_file():
        raise ValueError(f"路径不是文件: {pdf_path}")

    pages: list[PageText] = []

    with pymupdf.open(pdf_path) as doc:
        for page_number, page in enumerate(doc, start=1):
            text = page.get_text("text")

            pages.append(
                PageText(
                    page_number=page_number,
                    text=text
                )
            )
    if not pages:
        raise ValueError(f"PDF没有可提取的文本: {pdf_path}")

    if not any(page.text.strip() for page in pages):
        raise ValueError(f"可能是扫描版，PDF没有可提取的文本: {pdf_path}")

    return pages