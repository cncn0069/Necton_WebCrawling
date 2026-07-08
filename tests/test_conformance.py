from datetime import date

import pytest

from rd2.adapters.conformance import ConformanceError, assert_conformance
from rd2.schema.models import CsoClassification, DisclosureStatus, Document


def _doc(**overrides):
    kwargs = dict(
        title="테스트 문서",
        ordering_agency="테스트기관",
        department="테스트부서",
        production_date=date(2026, 7, 1),
        disclosure_status=DisclosureStatus.OPEN,
        content_summary="요약",
        cso_classification=CsoClassification.O,
        source="정보공개포털",
        is_synthetic=False,
    )
    kwargs.update(overrides)
    return Document(**kwargs)


def test_conformance_passes_when_always_filled_fields_present():
    docs = [_doc(), _doc(title="다른 문서")]
    assert_conformance("정보공개포털", docs)  # 예외 없이 통과


def test_conformance_fails_when_always_filled_field_missing():
    docs = [_doc(), _doc(department=None)]
    with pytest.raises(ConformanceError, match="department"):
        assert_conformance("정보공개포털", docs)


def test_conformance_fails_on_empty_sample():
    with pytest.raises(ConformanceError, match="비어있어"):
        assert_conformance("정보공개포털", [])


def test_conformance_fails_for_unknown_adapter():
    with pytest.raises(ConformanceError, match="계약이 없음"):
        assert_conformance("존재하지않는어댑터", [_doc()])
