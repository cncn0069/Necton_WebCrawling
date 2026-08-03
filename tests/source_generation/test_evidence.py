from __future__ import annotations

import pytest

from rd2.source_generation.contracts import EvidenceSpan
from rd2.source_generation.evidence import (
    EvidenceResolutionError,
    validate_evidence_quotes,
)


def test_quote_is_accepted_without_the_model_computing_any_offset():
    """모델은 인용문만 낸다. 위치는 코드가 찾는다."""

    text = "· 연구결과의 활용 가능성 · 비공개 시 사유의 적정성"
    span = EvidenceSpan(
        block_id="source-page1:b14",
        quote="비공개 시 사유의 적정성",
    )

    result = validate_evidence_quotes((span,), lambda block_id: text)

    assert result == (span,)
    assert span.locate_in(text) == text.find("비공개 시 사유의 적정성")


def test_hallucinated_and_ambiguous_quotes_still_fail():
    """오프셋을 없애도 '근거가 실재해야 한다'는 보안 속성은 그대로다."""

    with pytest.raises(EvidenceResolutionError, match="not found"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="없음"),),
            lambda block_id: "실제 본문",
        )

    with pytest.raises(EvidenceResolutionError, match="ambiguous"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="반복"),),
            lambda block_id: "반복 후 반복",
        )


def test_duplicate_quotes_cannot_inflate_the_evidence_count():
    with pytest.raises(EvidenceResolutionError, match="duplicate"):
        validate_evidence_quotes(
            (
                EvidenceSpan(block_id="b1", quote="근거"),
                EvidenceSpan(block_id="b1", quote="근거"),
            ),
            lambda block_id: "근거 문장",
        )


def test_whitespace_differences_no_longer_break_table_evidence():
    """실측 회귀: classifier가 PDF 표를 인용할 때 글자는 다 맞히면서
    칸 사이 공백 개수만 달라 ``str.find``에 걸리지 않았다.
    """

    text = "안전검사대상   안전검사수수료  10톤 미만   77,000"
    span = EvidenceSpan(
        block_id="p1:b0",
        quote="안전검사대상 안전검사수수료 10톤 미만 77,000",
    )

    assert validate_evidence_quotes((span,), lambda block_id: text) == (span,)
    # 위치는 여전히 원문 좌표로 돌려준다.
    assert span.locate_in(text) == 0


def test_full_width_space_is_also_forgiven():
    """한글 공문에 흔한 전각 공백(\\u3000)도 공백으로 취급한다."""

    span = EvidenceSpan(block_id="b1", quote="제166조제1항 산업안전보건법")

    assert span.locate_in("제166조제1항　산업안전보건법") == 0


def test_relaxing_whitespace_does_not_permit_paraphrase_or_omission():
    """공백만 풀어줬다. 글자가 빠지거나 바뀌면 여전히 실패한다."""

    text = "수수료가 다음과 같이 변경되어 이를 고시합니다"

    # 요약(중간 생략)
    with pytest.raises(EvidenceResolutionError, match="not found"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="수수료가 고시합니다"),),
            lambda block_id: text,
        )
    # 바꿔쓰기
    with pytest.raises(EvidenceResolutionError, match="not found"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="수수료가 아래와 같이 변경되어"),),
            lambda block_id: text,
        )


def test_whitespace_only_variants_cannot_inflate_the_evidence_count():
    """공백만 다른 두 인용문은 같은 근거를 두 번 센 것이다."""

    with pytest.raises(EvidenceResolutionError, match="duplicate"):
        validate_evidence_quotes(
            (
                EvidenceSpan(block_id="b1", quote="근거 문장"),
                EvidenceSpan(block_id="b1", quote="근거  문장"),
            ),
            lambda block_id: "근거 문장",
        )


def test_ambiguity_is_still_detected_across_whitespace_variants():
    """공백을 무시하면 같아지는 두 곳도 모호한 것이다."""

    with pytest.raises(EvidenceResolutionError, match="ambiguous"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="반복 구간"),),
            lambda block_id: "반복 구간 그리고 반복  구간",
        )


def test_byte_length_offsets_no_longer_break_valid_evidence():
    """실측 회귀: 모델이 3글자 '요약문'에 end=9(UTF-8 바이트 수)를 반환했다.

    오프셋을 계약에서 제거했으므로 이제 이런 산술 오류 자체가 존재할 수 없다.
    """

    text = "요약문"
    span = EvidenceSpan(block_id="source-page1:b0", quote="요약문")

    assert validate_evidence_quotes((span,), lambda block_id: text) == (span,)
    assert "start" not in EvidenceSpan.model_fields
    assert "end" not in EvidenceSpan.model_fields


def test_error_names_the_quote_that_failed():
    """실측(2026-08-01): 오류가 block ID만 말해서 원인을 추측만 했다.

    같은 ``not found``라도 문장을 지어낸 것과 block 경계를 넘어 인용한 것은
    고치는 방법이 다르다. 인용문과 어디까지 맞았는지를 오류에 남겨야 다음
    실행에서 구분할 수 있다.
    """

    with pytest.raises(EvidenceResolutionError, match="점검을 실시하고"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="점검을 실시하고 그 결과를 제출한다"),),
            lambda block_id: "점검을 실시하고 그 결과를 붙임과 같이 알려드립니다.",
        )


def test_error_tells_a_boundary_overrun_apart_from_a_fabrication():
    """앞부분이 맞으면 경계를 넘긴 것, 앞부분조차 없으면 다른 block이거나 지어낸 것."""

    block = "광운대역 물류부지 개발사업-상업용지 현장에 대해 점검을 실시하고 그 결과를 알려드립니다."

    with pytest.raises(EvidenceResolutionError, match="앞 24자는 이 block에 있다"):
        validate_evidence_quotes(
            (
                EvidenceSpan(
                    block_id="b1",
                    quote="광운대역 물류부지 개발사업-상업용지 현장에 대해 점검을 실시하고 인·허가청에 제출하고",
                ),
            ),
            lambda block_id: block,
        )

    with pytest.raises(EvidenceResolutionError, match="앞부분도 이 block에 없다"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="시공사는 조속히 시정하여 그 결과를"),),
            lambda block_id: block,
        )


def test_ambiguous_error_also_names_the_quote():
    with pytest.raises(EvidenceResolutionError, match="반복 구간"):
        validate_evidence_quotes(
            (EvidenceSpan(block_id="b1", quote="반복 구간"),),
            lambda block_id: "반복 구간 그리고 반복 구간",
        )
