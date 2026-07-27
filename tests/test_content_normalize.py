"""rd2.audit.content_normalize 회귀 테스트.

설계 문서 §14.3(exact canonicalization), §14.6(normalization 규칙)의 계약을
고정한다: 법 조항 번호는 절대 치환하지 않고, 문서번호/사건번호/전화번호/날짜/
금액/인원/일반숫자는 각 typed placeholder로 치환한다.
"""

from rd2.audit.content_normalize import (
    normalize_placeholders,
    normalized_content_hash,
    raw_content_hash,
)


class TestRawContentHash:
    def test_deterministic(self):
        assert raw_content_hash("제목", "본문") == raw_content_hash("제목", "본문")

    def test_whitespace_and_linebreak_insensitive(self):
        a = raw_content_hash("제목", "첫 줄\n\n\n둘째 줄")
        b = raw_content_hash("제목  ", "첫   줄\r\n \r\n \n둘째   줄  ")
        assert a == b

    def test_nfc_normalizes_decomposed_unicode(self):
        import unicodedata

        decomposed_title = unicodedata.normalize("NFD", "가나다")
        assert raw_content_hash(decomposed_title, "본문") == raw_content_hash("가나다", "본문")

    def test_different_body_differs(self):
        assert raw_content_hash("제목", "본문 A") != raw_content_hash("제목", "본문 B")

    def test_leading_trailing_blank_lines_removed(self):
        a = raw_content_hash("제목", "본문")
        b = raw_content_hash("제목", "\n\n본문\n\n")
        assert a == b


class TestNormalizePlaceholders:
    def test_law_article_reference_not_replaced(self):
        text = "제9조 제1항 제2호에 따라 공개하지 않는다."
        assert normalize_placeholders(text) == text

    def test_document_number_replaced(self):
        result = normalize_placeholders("문서번호 제2024-1234호로 접수됨.")
        assert "<DOCNO>" in result
        assert "2024" not in result
        assert "1234" not in result

    def test_case_number_replaced(self):
        result = normalize_placeholders("사건번호 2024고단1234 관련 자료이다.")
        assert result == "사건번호 <CASE_NO> 관련 자료이다."

    def test_phone_number_replaced(self):
        result = normalize_placeholders("담당자 전화번호는 02-1234-5678이다.")
        assert result == "담당자 전화번호는 <PHONE>이다."

    def test_date_formats_replaced(self):
        assert normalize_placeholders("2024-03-15") == "<DATE>"
        assert normalize_placeholders("2024.03.15") == "<DATE>"
        assert normalize_placeholders("2024년 3월 15일") == "<DATE>"

    def test_money_replaced(self):
        result = normalize_placeholders("지원금액 1,234,000원을 지급한다.")
        assert result == "지원금액 <MONEY>을 지급한다."

    def test_count_of_people_replaced(self):
        result = normalize_placeholders("총 5명에게 배분한다.")
        assert result == "총 <COUNT>에게 배분한다."

    def test_ssn_like_number_falls_back_to_general_number_not_phone_or_date(self):
        """설계 문서 §14.6 "주민번호처럼 보이는 숫자" 충돌 fixture — 전화번호/날짜로
        오분류되지 않고 일반 숫자 두 개로 처리돼야 한다."""
        result = normalize_placeholders("주민등록번호로 보이는 900101-1234567 이 포함된 문서.")
        assert "<PHONE>" not in result
        assert "<DATE>" not in result
        assert result == "주민등록번호로 보이는 <NUM>-<NUM> 이 포함된 문서."

    def test_law_reference_survives_alongside_other_placeholders(self):
        text = "제9조에 따라 2024-03-15까지 5명을 조사한다."
        result = normalize_placeholders(text)
        assert "제9조" in result
        assert "<DATE>" in result
        assert "<COUNT>" in result


class TestNormalizedContentHash:
    def test_agency_name_substitution_makes_hashes_equal(self):
        a = normalized_content_hash("t", "고용노동부 발표 내용입니다.", ordering_agency="고용노동부")
        b = normalized_content_hash("t", "환경부 발표 내용입니다.", ordering_agency="환경부")
        assert a == b

    def test_date_only_difference_collapses(self):
        a = normalized_content_hash("t", "생산일자 2024-03-15 문서")
        b = normalized_content_hash("t", "생산일자 2024-05-20 문서")
        assert a == b

    def test_real_content_difference_still_differs(self):
        a = normalized_content_hash("t", "예산 100억원을 승인한다.")
        b = normalized_content_hash("t", "예산 승인을 보류한다.")
        assert a != b

    def test_no_agency_kwarg_still_works(self):
        assert normalized_content_hash("t", "본문") == normalized_content_hash("t", "본문")
