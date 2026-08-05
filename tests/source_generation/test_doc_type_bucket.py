"""형식 17종 → ``doc_type`` 6칸 어댑터.

여기서 지키려는 것은 셋이다.

1. **빠지는 형식이 없다.** 표에 없는 형식은 조용히 NULL이 되는 게 아니라
   import 시점에 드러나야 한다.
2. **일곱 번째 칸이 생기지 않는다.** 값은 언제나 정해진 6개 안이다.
3. **한글 그대로 저장된다.** 이 컬럼의 다른 값(수집분)은 영어 코드라,
   생성분이 영어로 새면 아무도 모른 채 어휘가 섞인다.
"""

from __future__ import annotations

import pytest

from rd2.source_generation.classification_taxonomy import DocumentForm
from rd2.source_generation.doc_type_bucket import (
    DOC_TYPE_BUCKETS,
    DOC_TYPE_BY_FORM,
    doc_type_for_form,
)


def test_every_document_form_has_a_bucket():
    missing = [form for form in DocumentForm if form not in DOC_TYPE_BY_FORM]

    assert missing == []


@pytest.mark.parametrize("form", list(DocumentForm), ids=lambda f: f.value)
def test_bucket_is_always_one_of_the_six(form):
    assert doc_type_for_form(form) in DOC_TYPE_BUCKETS


def test_the_six_buckets_are_the_korean_names():
    assert set(DOC_TYPE_BUCKETS) == {
        "공문",
        "현황보고서",
        "회의록",
        "감사보고서",
        "정책문서",
        "규정",
    }


def test_forms_that_share_a_bucket_actually_share_a_job():
    """합치는 기준은 낱말이 아니라 그 문서가 하는 일이다.

    이 세 묶음이 이 어댑터의 실제 판단이다 — 나머지는 1:1이라 다툴 게 없다.
    바꿀 때 무엇을 바꾸는지 보이게 여기 적어 둔다.
    """

    # 확인한 결과가 내용인 문서: 감사·점검(대상이 사물)·조사(대상이 사람)
    assert {
        doc_type_for_form(form)
        for form in (
            DocumentForm.AUDIT_MATERIAL,
            DocumentForm.INSPECTION_REPORT,
            DocumentForm.INVESTIGATION_REPORT,
        )
    } == {"감사보고서"}

    # 앞으로 할 일을 정하는 문서: 계획안·대응계획서·정책자료
    assert {
        doc_type_for_form(form)
        for form in (
            DocumentForm.PLAN_DRAFT,
            DocumentForm.RESPONSE_PLAN,
            DocumentForm.POLICY_MATERIAL,
        )
    } == {"정책문서"}

    # 개별 사안을 기안·결재·회신하는 문서
    assert {
        doc_type_for_form(form)
        for form in (
            DocumentForm.OFFICIAL_LETTER,
            DocumentForm.APPROVAL_REQUEST,
            DocumentForm.REPLY_NOTICE,
        )
    } == {"공문"}


def test_report_and_status_report_do_not_collapse_into_the_letter_bucket():
    """보고서가 공문으로 가면 6칸 중 한 칸이 절반을 먹는다."""

    assert doc_type_for_form(DocumentForm.REPORT) == "현황보고서"
    assert doc_type_for_form(DocumentForm.MEETING_MINUTES) == "회의록"
    assert doc_type_for_form(DocumentForm.ADMINISTRATIVE_RULE) == "규정"
