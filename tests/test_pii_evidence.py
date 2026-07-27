from rd2.generators.pii_evidence import (
    ensure_clause6_pii_evidence,
    validate_clause6_pii_evidence,
)


def test_summary_only_welfare_body_is_rejected():
    body = "성명, 주민등록번호, 가구원 현황을 포함한 자료를 확보하고 있습니다."
    assert validate_clause6_pii_evidence(body, 2)


def test_welfare_evidence_is_added_and_passes_validation():
    body = ensure_clause6_pii_evidence("개인정보 자료를 제출합니다.", 2, "6-fallback-1")
    assert "수급자격 심사 대상자 명부" in body
    assert "가구원 현황" in body
    assert validate_clause6_pii_evidence(body, 2) == []


def test_existing_real_values_are_not_duplicated():
    body = "성명: 김하람\n주민등록번호: 900101-1234567\n가구원 현황: 1인 가구"
    assert ensure_clause6_pii_evidence(body, 2, "row") == body
