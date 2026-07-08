import pytest

from rd2.generators import generate
from rd2.generators.clause_data import CLAUSES, ClauseDefinition
from rd2.schema.models import CsoClassification, DisclosureStatus


def test_every_non_on_hold_clause_has_scenario_prompts():
    """CLAUSES 데이터 무결성: on_hold가 아닌 조항은 시나리오가 최소 1개 있어야
    generate_clause_document가 실제로 동작할 수 있다."""
    for clause_no, clause in CLAUSES.items():
        assert clause.clause_no == clause_no
        if not clause.on_hold:
            assert clause.scenario_prompts, f"clause {clause_no} has no scenario prompts"


def test_on_hold_clause_is_marked_and_has_no_prompts():
    assert CLAUSES["기타"].on_hold is True
    assert CLAUSES["기타"].scenario_prompts == []


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **kwargs):
        class _Response:
            choices = [_FakeChoice(self._content)]

        return _Response()


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class FakeOpenAIClient:
    """OpenAI SDK가 노출하는 client.chat.completions.create(...).choices[0].message.content
    경로만 흉내내는 테스트용 더블 — 실제 API 호출 없이 generate_clause_document를 검증한다."""

    def __init__(self, content: str = "가상의 공공기관 문서 본문") -> None:
        self.chat = _FakeChat(content)


def test_generate_clause_document_builds_expected_document():
    doc = generate.generate_clause_document(
        "1", client=FakeOpenAIClient("테스트용 합성 본문"), scenario_index=0
    )
    assert doc.body_text == "테스트용 합성 본문"
    assert doc.cso_classification == CsoClassification.C
    assert doc.cso_sub_clause == "1"
    assert doc.source == "synthetic-llm"
    assert doc.source_url is None
    assert doc.is_synthetic is True
    assert doc.disclosure_status == DisclosureStatus.CLOSED
    assert doc.non_disclosure_reason is not None
    assert doc.title.startswith("[합성]")


def test_generate_clause_document_rejects_on_hold_clause():
    with pytest.raises(ValueError, match="on hold"):
        generate.generate_clause_document("기타", client=FakeOpenAIClient())


def test_generate_clause_document_rejects_clause_without_scenarios(monkeypatch):
    empty_clause = ClauseDefinition(
        clause_no="9",
        classification=CsoClassification.S,
        title="테스트용 빈 조항",
        description="시나리오가 없는 임시 조항",
        scenario_prompts=[],
        on_hold=False,
    )
    monkeypatch.setitem(generate.CLAUSES, "9", empty_clause)

    with pytest.raises(ValueError, match="no scenario prompts"):
        generate.generate_clause_document("9", client=FakeOpenAIClient())
