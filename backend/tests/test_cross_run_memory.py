from uuid import uuid4

from littrace.research.cross_run_memory import (
    CrossRunMemoryItem,
    format_memory_context,
)


def test_format_memory_context_keeps_source_run_traceability():
    source_run_id = uuid4()
    item = CrossRunMemoryItem(
        source_evidence_id=uuid4(),
        source_run_id=source_run_id,
        summary="已确认论断：方法改善了跨模态对齐。",
    )

    context = format_memory_context([item])

    assert str(source_run_id) in context
    assert "方法改善了跨模态对齐" in context


def test_format_memory_context_explains_empty_memory():
    assert format_memory_context([]) == (
        "No confirmed evidence from earlier runs is available."
    )
