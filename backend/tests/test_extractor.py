import json
from types import SimpleNamespace

import pytest

import littrace.evidence.extractor as extractor_module
from littrace.domain.models import PaperChunk


class FakeCompletions:
    """
    模拟 client.chat.completions。
    """

    def __init__(self, content: str) -> None:
        self.content = content

    def create(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=self.content,
                    )
                )
            ]
        )


class FakeClient:
    """
    模拟 OpenAI 客户端。
    """

    def __init__(self, content: str) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCompletions(content),
        )


def configure_fake_model(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    """
    将真实模型客户端替换成假的模型客户端。
    """

    monkeypatch.setattr(
        extractor_module,
        "create_model_client",
        lambda: FakeClient(content),
    )

    monkeypatch.setattr(
        extractor_module,
        "get_settings",
        lambda: SimpleNamespace(
            deepseek_model="fake-model",
        ),
    )


def create_chunk() -> PaperChunk:
    return PaperChunk(
        chunk_id="paper-001-p8-c3",
        paper_id="paper-001",
        page_number=8,
        text=(
            "The primary limitation is "
            "the computational cost."
        ),
    )


def test_extract_evidence_parses_valid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_content = json.dumps(
        {
            "evidence_cards": [
                {
                    "question": (
                        "What limitation did the authors mention?"
                    ),
                    "claim": (
                        "The method has a computational cost."
                    ),
                    "relation": "supports",
                    "quote": (
                        "The primary limitation is "
                        "the computational cost."
                    ),
                    "page_number": 8,
                    "chunk_id": "paper-001-p8-c3",
                    "limitations": [],
                }
            ]
        }
    )

    configure_fake_model(
        monkeypatch,
        model_content,
    )

    cards = extractor_module.extract_evidence(
        question=(
            "What limitation did the authors mention?"
        ),
        chunks=[create_chunk()],
    )

    assert len(cards) == 1
    assert cards[0].page_number == 8
    assert cards[0].chunk_id == "paper-001-p8-c3"
    assert cards[0].relation == "supports"


def test_extract_evidence_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_fake_model(
        monkeypatch,
        "This is not JSON.",
    )

    with pytest.raises(
        ValueError,
        match="不是有效的 JSON",
    ):
        extractor_module.extract_evidence(
            question=(
                "What limitation did the authors mention?"
            ),
            chunks=[create_chunk()],
        )