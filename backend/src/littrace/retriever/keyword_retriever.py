import re
import unicodedata

from littrace.domain.models import PaperChunk


ENGLISH_WORD_PATTERN = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_-]+"
)

CHINESE_SEQUENCE_PATTERN = re.compile(
    r"[\u4e00-\u9fff]+"
)

ENGLISH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "does",
    "for",
    "in",
    "is",
    "of",
    "the",
    "this",
    "to",
    "what",
}


def normalize_search_text(text: str) -> str:
    """
    统一搜索文本的 Unicode 格式和大小写。
    """

    return unicodedata.normalize(
        "NFKC",
        text,
    ).casefold()


def tokenize_search_text(text: str) -> set[str]:
    """
    从中英文文本中提取搜索词。

    英文按单词处理。
    中文按相邻两个汉字组成的二元词处理。
    """

    normalized = normalize_search_text(text)
    terms: set[str] = set()

    english_words = ENGLISH_WORD_PATTERN.findall(
        normalized
    )

    for word in english_words:
        if (
            len(word) >= 2
            and word not in ENGLISH_STOPWORDS
        ):
            terms.add(word)

    chinese_sequences = (
        CHINESE_SEQUENCE_PATTERN.findall(normalized)
    )

    for sequence in chinese_sequences:
        if len(sequence) == 1:
            terms.add(sequence)
            continue

        for index in range(len(sequence) - 1):
            terms.add(
                sequence[index:index + 2]
            )

    return terms


def select_candidate_chunks(
    question: str,
    chunks: list[PaperChunk],
    top_k: int = 8,
) -> list[PaperChunk]:
    """
    根据研究问题与 chunk 的关键词重合度，
    选择最相关的候选文本块。
    """

    if not question.strip():
        raise ValueError("研究问题不能为空")

    if not chunks:
        raise ValueError("chunks 不能为空")

    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")

    question_terms = tokenize_search_text(
        question
    )

    scored_chunks: list[
        tuple[int, int, PaperChunk]
    ] = []

    for original_index, chunk in enumerate(chunks):
        chunk_terms = tokenize_search_text(
            chunk.text
        )

        matched_terms = (
            question_terms & chunk_terms
        )

        score = len(matched_terms)

        scored_chunks.append(
            (
                score,
                original_index,
                chunk,
            )
        )

    scored_chunks.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    return [
        chunk
        for _, _, chunk in scored_chunks[:top_k]
    ]