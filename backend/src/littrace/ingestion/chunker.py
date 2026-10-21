from littrace.domain.models import PageText, PaperChunk

def chunk_pages(
        pages: list[PageText],
        paper_id: str,
        max_chars: int = 1200,
        overlap: int = 150
) -> list[PaperChunk]:

    if not paper_id.strip():
        raise ValueError("paper_id不能为空")

    if max_chars <= 0:
        raise ValueError("max_chars必须大于0")

    if overlap < 0:
        raise ValueError("overlap必须大于等于0")

    if overlap >= max_chars:
        raise ValueError("overlap必须小于max_chars")

    chunks: list[PaperChunk] = []

    for page in pages:

        normalized_text = " ".join(page.text.split())

        if not normalized_text:
            continue

        start = 0
        chunk_number = 1

        while start < len(normalized_text):
            end = min(
                start + max_chars,
                len(normalized_text)
            )

            chunk_text = normalized_text[start:end].strip()

            if chunk_text:
                chunk_id = (
                    f"{paper_id}"
                    f"-p{page.page_number}"
                    f"-c{chunk_number}"
                )

                chunks.append(
                    PaperChunk(
                        chunk_id=chunk_id,
                        paper_id=paper_id,
                        page_number=page.page_number,
                        # chunk_number 每进入新页面都会从 1 开始，
                        # 因此它表示“当前页中的块序号”。
                        chunk_index=chunk_number,
                        text=chunk_text
                    )
                )

            if end == len(normalized_text):
                break

            start = end - overlap
            chunk_number += 1

    return chunks