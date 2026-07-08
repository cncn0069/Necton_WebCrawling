from datetime import date

import pytest
from pydantic import ValidationError

from rd2.schema.models import CsoClassification, DisclosureStatus, Document


def _base_kwargs(**overrides):
    kwargs = dict(
        title="2026년 성과관리 지표 고도화 용역",
        ordering_agency="강원특별자치도",
        disclosure_status=DisclosureStatus.OPEN,
        body_text="본문 내용...",
        cso_classification=CsoClassification.O,
        source="PRISM",
        source_url="https://www.prism.go.kr/homepage/asmt/1234",
        is_synthetic=False,
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_o_track_document():
    doc = Document(**_base_kwargs())
    assert doc.cso_classification == CsoClassification.O
    assert doc.is_synthetic is False


def test_cs_track_allows_is_synthetic_false_for_real_partial_disclosure_docs():
    """원문정보 어댑터(부분공개 필터): 실제 수집 문서가 C/S로 분류될 수 있다."""
    doc = Document(
        **_base_kwargs(
            cso_classification=CsoClassification.C,
            cso_sub_clause="1",
            source="원문정보",
            source_url="https://open.go.kr/wonmun/1",
            is_synthetic=False,
        )
    )
    assert doc.is_synthetic is False
    assert doc.cso_classification == CsoClassification.C


def test_cs_track_valid_with_is_synthetic_true():
    doc = Document(
        **_base_kwargs(
            cso_classification=CsoClassification.C,
            cso_sub_clause="1",
            source="synthetic-llm",
            source_url=None,
            is_synthetic=True,
        )
    )
    assert doc.is_synthetic is True
    assert doc.cso_sub_clause == "1"


def test_non_open_document_requires_non_disclosure_reason():
    with pytest.raises(ValidationError, match="non_disclosure_reason"):
        Document(
            **_base_kwargs(
                disclosure_status=DisclosureStatus.PARTIAL,
                non_disclosure_reason=None,
            )
        )


def test_document_without_body_text_still_valid():
    """정보공개포털 사전정보공개 목록: 국장급 이상 결재문서가 아니면 본문 미제공."""
    doc = Document(**_base_kwargs(body_text=None))
    assert doc.body_text is None


def test_non_open_document_with_reason_passes():
    doc = Document(
        **_base_kwargs(
            disclosure_status=DisclosureStatus.PARTIAL,
            non_disclosure_reason="관세국경안전 현안대응 사업기획 연구용역 관련 비공개",
        )
    )
    assert doc.non_disclosure_reason is not None
