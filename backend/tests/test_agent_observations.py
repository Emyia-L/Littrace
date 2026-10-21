import json

from littrace.domain.models import ChunkSearchResult, PaperChunk
from littrace.research.agent_actions import SearchAction
from littrace.research.agent_observations import summarize_search_results


def test_search_observation_only_exposes_first_eight_results():
    action = SearchAction(
        kind="search",
        strategy="hybrid",
        query="active perception",
        top_k=10,
    )
    results = [
        ChunkSearchResult(
            chunk=PaperChunk(
                chunk_id=f"chunk-{index}",
                paper_id="paper-1",
                page_number=1,
                chunk_index=index,
                text="  Relevant\ntext  " + "x" * 200,
            ),
            score=1.0,
        )
        for index in range(1, 11)
    ]

    summary, visible_ids = summarize_search_results(
        action=action,
        results=results,
    )
    data = json.loads(summary)

    assert data["returned_count"] == 10
    assert data["visible_count"] == 8
    assert visible_ids == {f"chunk-{index}" for index in range(1, 9)}
    assert all(len(match["excerpt"]) <= 180 for match in data["matches"])
    assert len(summary) <= 5000


def test_search_observation_handles_no_results():
    action = SearchAction(
        kind="search",
        strategy="keyword",
        query="unmatched phrase",
    )

    summary, visible_ids = summarize_search_results(
        action=action,
        results=[],
    )

    assert json.loads(summary)["matches"] == []
    assert visible_ids == set()