import json

from littrace.domain.models import ChunkSearchResult
from littrace.research.agent_actions import SearchAction


MAX_VISIBLE_RESULTS = 8
MAX_EXCERPT_CHARS = 180
MAX_OBSERVATION_CHARS = 5000  # 与 agent_planner.py 的输入上限一致

def summarize_search_results(
        *,
        action: SearchAction,
        results: list[ChunkSearchResult],
) -> tuple[str, set[str]]:
    """把检索结果变成有长度限制的观察，并返回实际展示的块 ID。"""

    matches = []

    # results 已由检索器按相关性排序；第一版只展示前 8 条。
    for item in results[:MAX_VISIBLE_RESULTS]:
        chunk = item.chunk

        # 压平换行和多余空格，再截取摘要；不把整篇论文送回规划模型。
        excerpt = " ".join(chunk.text.split())[:MAX_EXCERPT_CHARS]

        matches.append(
            {
                "chunk_id": chunk.chunk_id,
                "paper_id": chunk.paper_id,
                "page_number": chunk.page_number,
                "excerpt": excerpt,
            }
        )

    observation = {
        "tool": "search",
        "strategy": action.strategy,
        "query": action.query,
        # 这是检索工具返回的数量，不是数据库中所有匹配项的总数。
        "returned_count": len(results),
        "visible_count": len(matches),
        "matches": matches,
    }
    summary = json.dumps(observation, ensure_ascii=False)

    if len(summary) > MAX_OBSERVATION_CHARS:
        raise ValueError("Search observation is too long")

    # 只把实际展示给规划模型的 ID 记为“已观察”。
    visible_chunk_ids = {match["chunk_id"] for match in matches}
    return summary, visible_chunk_ids