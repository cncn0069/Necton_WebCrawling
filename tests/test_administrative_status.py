import json

from rd2.augmentation.administrative_status import (
    augment_administrative_status,
    build_document_context,
    build_messages,
)
from rd2.augmentation.administrative_status_data import ADMINISTRATIVE_STATUSES


def _span(span_id: int, text: str, *, is_boilerplate: bool = False) -> dict:
    return {"span_id": span_id, "text": text, "cleaned_text": text, "is_boilerplate": is_boilerplate}


def _doc(pages: list[dict]) -> dict:
    return {"source_pdf_path": "data/moe/budget_material/test.pdf", "source": "moe", "pages": pages}


def test_build_document_context_skips_empty_first_page():
    """실측(2026-07-16)으로 확인된 문제: 표지 페이지가 스캔 이미지라 텍스트
    span이 하나도 없는 문서가 흔하다(샘플 45%) — 1페이지가 비어 있으면 다음
    페이지로 넘어가야 한다."""
    doc = _doc(
        [
            {"page_no": 1, "spans": [_span(1, "")]},
            {"page_no": 2, "spans": [_span(2, "실제 내용 시작")]},
        ]
    )
    context = build_document_context(doc)
    assert [c["span_id"] for c in context] == [2]
    assert context[0]["page_no"] == 2


def test_build_document_context_includes_boilerplate_spans():
    """문서 식별용 정형 필드(문서번호·시행일자 등)가 반복 헤더로 분류된 경우가
    많아, candidates.py의 5~8호와 달리 boilerplate span도 컨텍스트에 포함해야
    한다."""
    doc = _doc([{"page_no": 1, "spans": [_span(1, "문서번호: 12-345", is_boilerplate=True)]}])
    context = build_document_context(doc)
    assert [c["span_id"] for c in context] == [1]


def test_build_document_context_respects_max_spans_cap():
    doc = _doc([{"page_no": 1, "spans": [_span(i, f"span {i}") for i in range(10)]}])
    context = build_document_context(doc, max_spans=3)
    assert len(context) == 3


def test_build_document_context_returns_empty_for_all_blank_document():
    doc = _doc([{"page_no": 1, "spans": [_span(1, "")]}, {"page_no": 2, "spans": [_span(2, "   ")]}])
    assert build_document_context(doc) == []


def test_build_messages_includes_category_title_and_description():
    context = [{"span_id": 1, "text": "생산일자: 2024-03-15", "page_no": 1}]
    messages = build_messages(context, "pending_disclosure_date")
    status = ADMINISTRATIVE_STATUSES["pending_disclosure_date"]
    assert status.title in messages[0]["content"]
    assert status.description in messages[1]["content"]
    assert "생산일자: 2024-03-15" in messages[1]["content"]


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
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


_CONTEXT = [
    {"span_id": 1, "text": "생산일자: 2024-03-15", "page_no": 1},
    {"span_id": 2, "text": "본 사업은 2026년부터 시행한다", "page_no": 1},
]


def test_augment_administrative_status_returns_selection_with_category_field():
    content = json.dumps(
        {
            "selections": [
                {
                    "span_id": 1,
                    "synthetic": "공개예정일: 2027-03-01",
                    "transformation": "pending_date",
                    "reason": "공개 예정일 전이라 비공개",
                }
            ]
        }
    )
    results = augment_administrative_status(
        _CONTEXT, "pending_disclosure_date", client=FakeOpenAIClient(content)
    )
    assert len(results) == 1
    assert results[0]["category"] == "pending_disclosure_date"
    assert "clause" not in results[0]  # 5~8호 필드와 절대 섞이지 않아야 함
    assert results[0]["original"] == "생산일자: 2024-03-15"
    assert results[0]["synthetic"] == "공개예정일: 2027-03-01"


def test_augment_administrative_status_rejects_hallucinated_span_id():
    content = json.dumps(
        {"selections": [{"span_id": 999, "synthetic": "x", "transformation": "t", "reason": "r"}]}
    )
    results = augment_administrative_status(_CONTEXT, "draft", client=FakeOpenAIClient(content))
    assert results == []


def test_augment_administrative_status_rejects_when_length_ratio_invalid():
    content = json.dumps(
        {
            "selections": [
                {
                    "span_id": 1,
                    "synthetic": "매우 길게 늘어난 치환 문구로 길이비 상한을 확실히 넘기기 위한 문장입니다",
                    "transformation": "t",
                    "reason": "r",
                }
            ]
        }
    )
    results = augment_administrative_status(_CONTEXT, "draft", client=FakeOpenAIClient(content))
    assert results == []


def test_augment_administrative_status_returns_empty_for_empty_context():
    assert augment_administrative_status([], "draft", client=FakeOpenAIClient("{}")) == []


def test_every_category_has_title_and_description():
    for key, status in ADMINISTRATIVE_STATUSES.items():
        assert status.category == key
        assert status.title
        assert status.description
