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
    span = EvidenceSpan(block_id="p1:b14", quote="비공개 시 사유의 적정성")

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


def test_byte_length_offsets_no_longer_break_valid_evidence():
    """실측 회귀: 모델이 3글자 '요약문'에 end=9(UTF-8 바이트 수)를 반환했다.

    오프셋을 계약에서 제거했으므로 이제 이런 산술 오류 자체가 존재할 수 없다.
    """

    text = "요약문"
    span = EvidenceSpan(block_id="p1:b0", quote="요약문")

    assert validate_evidence_quotes((span,), lambda block_id: text) == (span,)
    assert "start" not in EvidenceSpan.model_fields
    assert "end" not in EvidenceSpan.model_fields
