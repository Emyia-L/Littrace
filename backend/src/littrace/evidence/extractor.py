import json

from pydantic import ValidationError

from littrace.config import get_settings
from littrace.domain.models import (
    EvidenceCard,
    PaperChunk
)
from littrace.model_client import create_model_client
from littrace.research.model_usage import tracked_model_call

SYSTEM_PROMPT = """
You are the evidence extraction component of LitTrace.

You must only use the paper chunks supplied by the user.

Rules:
1. Output one JSON object only.
2. Do not output Markdown or explanations.
3. The top-level JSON key must be "evidence_cards".
4. Every quote must be copied verbatim from a supplied chunk.
5. Do not translate, rewrite, shorten, or correct a quote.
6. page_number and chunk_id must come from the same supplied chunk.
7. A claim may summarize the quote, but the quote must remain verbatim.
8. If there is no direct evidence, return an empty evidence_cards list.
9. Never invent a paper, page number, chunk ID, quotation, or result.
10. Every factual detail in a claim must be directly supported by its quote.
11. Do not add facts to the claim from surrounding text unless the quote itself contains them.
12. If no single verbatim quote supports the complete claim, omit that evidence card.

Required JSON structure:
{
  "evidence_cards": [
    {
      "question": "the research question",
      "claim": "a concise evidence-based claim",
      "relation": "supports",
      "quote": "verbatim text copied from one chunk",
      "page_number": 1,
      "chunk_id": "paper-001-p1-c1",
      "limitations": []
    }
  ]
}

The relation must be one of:
- supports
- contradicts
- mixed
- insufficient
""".strip()

def extract_evidence(
        question: str,
        chunks: list[PaperChunk],
        max_cards: int = 3,
) -> list[EvidenceCard]:
    """
    调用模型，从候选文本块中提取 Evidence Card。
    """

    if not question.strip():
        raise ValueError("question不能为空")

    if not chunks:
        raise ValueError("chunks不能为空")

    if max_cards <= 0:
        raise ValueError("max_cards必须大于0")

    chunks_data = [
        {
            "chunk_id": chunk.chunk_id,
            "page_number": chunk.page_number,
            "text": chunk.text
        }
        for chunk in chunks
    ]

    user_prompt = {
        "research_question": question,
        "maximum_evidence_cards": max_cards,
        "candidate_chunks": chunks_data,
    }

    settings = get_settings()
    # 统一包装调用，以便研究运行汇总 token、耗时和失败次数。
    # 此处保留普通客户端写法，以兼容现有的轻量测试客户端。
    client = create_model_client()
    response = tracked_model_call(
        component="evidence_extractor",
        model=settings.deepseek_model,
        call=lambda: client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        user_prompt,
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=1600,
        ),
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("模型返回的内容为空")

    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"模型返回的内容不是有效的 JSON: {e}"
        )

    if not isinstance(result, dict):
        raise ValueError(
            "模型返回的内容不是一个 JSON 对象"
        )

    raw_cards = result.get("evidence_cards")

    if not isinstance(raw_cards, list):
        raise ValueError(
            "模型返回的 evidence_cards 不是一个列表"
        )

    if len(raw_cards) > max_cards:
        raise ValueError(
            f"模型返回的 evidence_cards 数量超过最大值 {max_cards}"
        )

    try:
        evidence_cards = [
            EvidenceCard.model_validate(card)
            for card in raw_cards
        ]
    except ValidationError as e:
        raise ValueError(
            f"模型返回的 evidence_cards 数据验证失败: {e}"
        )

    return evidence_cards

"""
Prompt 约束
→ 要求只输出 JSON、quote 必须复制原文

JSON 解析
→ 拒绝 Markdown 或非法 JSON

Pydantic 验证
→ 拒绝字段缺失、页码错误和非法 relation
"""
