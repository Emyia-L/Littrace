import re
import unicodedata

from littrace.domain.models import (
    CitationCheck,
    EvidenceCard,
    PaperChunk
)

def normalize_text(text: str) -> str:
    """
    统一文本格式
    """

    normalized = unicodedata.normalize("NFKC", text)

    # 统一英文弯引号。
    normalized = normalized.replace("“", '"')
    normalized = normalized.replace("”", '"')
    normalized = normalized.replace("‘", "'")
    normalized = normalized.replace("’", "'")

    # 处理 PDF 中被换行拆开的英文单词。
    # 例如：computa-\ntional -> computational
    normalized = re.sub(
        r"(?<=\w)-\s+(?=\w)",
        "",
        normalized,
    )
    # 将换行、制表符和连续空格统一成一个空格。
    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    return normalized.strip().casefold()

def validate_quote(
        evidence: EvidenceCard,
        chunks_by_id: dict[str, PaperChunk]
) -> CitationCheck:
    """
    验证 Evidence Card 的引用是否真实存在。
    """

    chunk = chunks_by_id.get(evidence.chunk_id)

    if chunk is None:
        return CitationCheck(
            valid=False,
            reason="chunk_not_found"
        )

    if chunk.page_number != evidence.page_number:
        return CitationCheck(
            valid=False,
            reason="page_mismatch"
        )

    if not evidence.quote.strip():
        return CitationCheck(
            valid=False,
            reason="empty_quote"
        )

    normalized_quote = normalize_text(evidence.quote)
    normalized_chunk = normalize_text(chunk.text)

    if normalized_quote not in normalized_chunk:
        return CitationCheck(
            valid=False,
            reason="quote_not_found"
        )

    return CitationCheck(
        valid=True,
        reason=None
    )

        
