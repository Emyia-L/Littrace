from uuid import uuid4

from littrace.research.report_draft import ReportEvidence, render_report_draft


def test_report_draft_explains_empty_evidence() -> None:
    report = render_report_draft("论文解决了什么问题？", [])

    assert report == (
        "# 文献证据草稿\n\n"
        "研究问题：论文解决了什么问题？\n\n"
        "暂无经过人工确认的证据。"
    )


def test_report_draft_keeps_claim_source_and_multiline_quote() -> None:
    evidence_id = uuid4()
    item = ReportEvidence(
        evidence_id=evidence_id,
        claim="方法提升了准确率。",
        quote="The method improves accuracy.\nIt needs no training.",
        original_filename="paper.pdf",
        page_number=3,
    )

    report = render_report_draft("方法效果如何？", [item])

    assert "## 证据 1" in report
    assert "论断：方法提升了准确率。" in report
    assert "来源：paper.pdf，第 3 页" in report
    assert f"证据 ID：{evidence_id}" in report
    assert "> The method improves accuracy.\n> It needs no training." in report


def test_report_draft_preserves_evidence_order() -> None:
    first = ReportEvidence(
        evidence_id=uuid4(),
        claim="第一条论断",
        quote="First quote.",
        original_filename="first.pdf",
        page_number=1,
    )
    second = ReportEvidence(
        evidence_id=uuid4(),
        claim="第二条论断",
        quote="Second quote.",
        original_filename="second.pdf",
        page_number=5,
    )

    report = render_report_draft("研究问题", [first, second])

    assert report.count("## 证据 ") == 2
    assert report.index("## 证据 1") < report.index("## 证据 2")
    assert report.index("第一条论断") < report.index("第二条论断")
    assert "来源：first.pdf，第 1 页" in report
    assert "来源：second.pdf，第 5 页" in report
