from rd2.augmentation.candidates import (
    _CLAUSE_KEYWORDS,
    _MAX_CANDIDATES_PER_DOC,
    _matches_clause_5_or_7_or_8,
    _matches_clause_6,
    find_candidates,
)


def test_clause_6_rejects_bare_department_title():
    """실측(2026-07-15)으로 확인된 문제: "홍보담당관" 같은 단독 직책/부서명은
    조직도 라벨일 뿐 PII 자리가 아니므로 후보가 되면 안 된다."""
    assert _matches_clause_6("홍보담당관") is False
    assert _matches_clause_6("담당") is False


def test_clause_6_accepts_label_value_pattern():
    assert _matches_clause_6("성명:") is True
    assert _matches_clause_6("대표자성명:") is True


def test_clause_6_rejects_label_with_government_title():
    """직위명이 붙은 라벨(예: "...담당자: 소은주 정책관")은 공무원 직무 수행
    관련 정보라 정보공개법 6호 비공개 사유로 보기 어려워 제외해야 한다."""
    assert _matches_clause_6("※ 프로그램 성과 담당자 : 소은주 책임교육정책관") is False


def test_clause_6_accepts_label_without_government_title():
    """직위명 없이 남는 라벨은 입찰참가자·외부 연구자·민간기관 직원 등 사인(私人)의
    정보로 채워질 자리이므로 후보로 남아야 한다. 실제 데이터(policy_material)에서
    라벨과 값이 별개 span인 경우가 많아, 후보는 라벨 span 단독으로 잡힌다(값은
    LLM이 치환 시 채워넣음) — "작성책임자:"가 실제로 32건 나온 라벨이다."""
    assert _matches_clause_6("작성책임자:") is True


def test_clause_6_label_pattern_covers_local_government_forms():
    """지자체 서식(재산세·건축인허가 등)에 흔한 라벨도 인정해야 한다."""
    assert _matches_clause_6("소유자:") is True
    assert _matches_clause_6("세대주:") is True


def test_clause_6_gov_title_exclusion_does_not_collide_with_clinic():
    """"의원"은 지방의회 의원과 병원(예: "내과의원")이 동음이의라, 지역명이
    붙지 않은 단순 "의원"만으로는 공무원 직위 제외 대상이 되면 안 된다."""
    assert _matches_clause_6("담당자: 서울내과의원") is True


def test_clause_5_7_8_pruned_keywords_stay_removed():
    """실측으로 과매칭이 확인돼 뺀 단어들이 나중에 실수로 다시 들어오지
    않도록 하는 가드레일. 이유는 candidates.py 상단 주석 참고."""
    pruned = {
        "5": ["감사원", "실태점검", "인사발령"],
        "7": ["특허"],
        "8": ["도시재생", "용도변경"],
    }
    for clause_no, words in pruned.items():
        for word in words:
            assert word not in _CLAUSE_KEYWORDS[clause_no], (
                f"'{word}'는 과매칭으로 실측 제거된 키워드라 clause {clause_no}에 다시 들어가면 안 됨"
            )


def test_clause_5_matches_diversified_administrative_keywords():
    assert _matches_clause_5_or_7_or_8("다. 수의계약대상소액용역으로적격심사는하지않습니다.", "5") is True
    assert _matches_clause_5_or_7_or_8("7. 인사위원회", "5") is True


def test_clause_7_matches_money_pattern():
    assert _matches_clause_5_or_7_or_8("총 사업비 46,000,000원", "7") is True


def test_clause_8_matches_real_estate_keywords():
    assert _matches_clause_5_or_7_or_8("신규 정비구역 지정 검토", "8") is True


def _annotated_doc(spans: list[dict]) -> dict:
    return {
        "source_pdf_path": "data/moe/budget_material/test.pdf",
        "source": "moe",
        "doc_type": "budget_material",
        "doc_id": "1",
        "pages": [{"page_no": 1, "spans": spans}],
    }


def _span(span_id: int, text: str, *, is_boilerplate: bool = False) -> dict:
    return {
        "span_id": span_id,
        "text": text,
        "cleaned_text": text,
        "is_boilerplate": is_boilerplate,
    }


def test_find_candidates_skips_boilerplate_spans():
    doc = _annotated_doc(
        [
            _span(1, "입찰 계약 체결", is_boilerplate=True),
            _span(2, "입찰 계약 체결"),
        ]
    )
    candidates = find_candidates(doc, "5")
    assert [c["span_id"] for c in candidates] == [2]


def test_find_candidates_respects_per_document_cap():
    doc = _annotated_doc([_span(i, "입찰 계약 체결") for i in range(_MAX_CANDIDATES_PER_DOC + 5)])
    candidates = find_candidates(doc, "5")
    assert len(candidates) == _MAX_CANDIDATES_PER_DOC


def test_find_candidates_returns_empty_when_no_pages_key():
    assert find_candidates({}, "5") == []
