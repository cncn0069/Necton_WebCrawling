import pytest

from rd2.augmentation.annotate import annotate_document_in_place
from rd2.augmentation.candidates import (
    _CLAUSE_KEYWORDS,
    _MAX_CANDIDATES_PER_DOC,
    _matches_clause_5_or_7_or_8,
    _matches_clause_6,
    find_administrative_candidates,
    find_all_candidates,
    find_candidates,
)
from rd2.extraction.pdf_text import _merge_wrapped_lines


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
        "7": ["특허", "영업"],
        "8": ["도시재생", "용도변경", "부동산"],
    }
    for clause_no, words in pruned.items():
        for word in words:
            assert word not in _CLAUSE_KEYWORDS[clause_no], (
                f"'{word}'는 과매칭으로 실측 제거된 키워드라 clause {clause_no}에 다시 들어가면 안 됨"
            )


def test_clause_5_matches_diversified_administrative_keywords():
    assert _matches_clause_5_or_7_or_8("다. 수의계약대상소액용역으로적격심사는하지않습니다.", "5") is True
    assert _matches_clause_5_or_7_or_8("7. 인사위원회", "5") is True


def test_clause_5_short_keywords_ignore_unrelated_compound_words():
    for text in (
        "무기계약직 퇴직금",
        "노고에 감사드리며 인사 합니다",
        "감사원 지적사항",
        "심사청구 안내",
        "인사말",
    ):
        assert _matches_clause_5_or_7_or_8(text, "5") is False
    assert _matches_clause_5_or_7_or_8("감사원 종합감사 결과", "5") is True


def test_clause_7_and_8_reject_overbroad_single_words_but_keep_specific_compounds():
    assert _matches_clause_5_or_7_or_8("영업인가를 취소할 수 있습니다", "7") is False
    assert _matches_clause_5_or_7_or_8("법인의 영업비밀", "7") is True
    assert _matches_clause_5_or_7_or_8("부동산 일반 현황", "8") is False
    assert _matches_clause_5_or_7_or_8("부동산 재개발 계획", "8") is True


def test_clause_7_matches_money_pattern():
    assert _matches_clause_5_or_7_or_8("총 사업비 46,000,000원", "7") is True


def test_clause_8_matches_real_estate_keywords():
    assert _matches_clause_5_or_7_or_8("신규 정비구역 지정 검토", "8") is True


def _annotated_doc(lines: list[dict], *, doc_type: str = "budget_material") -> dict:
    return {
        "schema_version": 2,
        "extraction_id": f"extract-{doc_type}-1",
        "source_path": f"data/moe/{doc_type}/test.pdf",
        "source": "moe",
        "doc_type": doc_type,
        "doc_id": "1",
        "source_format": "pdf",
        "pages": [{"page": 1, "width_pt": 595.0, "height_pt": 842.0, "lines": lines}],
    }


def _span(span_id: int, text: str, *, is_boilerplate: bool = False) -> dict:
    return {
        "line_id": span_id,
        "block_id": span_id,
        "order": span_id,
        "text": text,
        "cleaned_text": text,
        "bbox_pt": [0.0, float(span_id * 20), 10.0, float(span_id * 20 + 10)],
        "style_runs": [],
        "is_boilerplate": is_boilerplate,
    }


def _raw_v2_document(pages: list[dict], *, source_format: str = "pdf") -> dict:
    return {
        "schema_version": 2,
        "extraction_id": f"raw-{source_format}-1",
        "source_path": f"data/moe/report/raw.{source_format}",
        "source": "moe",
        "doc_type": "report",
        "doc_id": "raw",
        "source_format": source_format,
        "pages": pages,
    }


def test_annotation_mutates_loaded_lines_only_and_returns_same_document():
    line = {
        "line_id": 0,
        "block_id": 0,
        "order": 0,
        "text": "본문\ufffd\ue000",
        "bbox_pt": [10.0, 20.0, 100.0, 30.0],
        "style_runs": [],
    }
    document = _raw_v2_document(
        [{"page": 1, "width_pt": 595.0, "height_pt": 842.0, "lines": [line]}]
    )
    original_document_keys = set(document)
    original_line_keys = set(line)

    result = annotate_document_in_place(document)

    assert result is document
    assert document["pages"][0]["lines"][0] is line
    assert set(document) == original_document_keys
    assert set(line) - original_line_keys == {"cleaned_text", "is_boilerplate"}
    assert line["cleaned_text"] == "본문"
    assert line["is_boilerplate"] is False


def test_annotation_uses_pdf_geometry_slots_for_repeated_boilerplate():
    pages = []
    for page_no in range(1, 4):
        pages.append(
            {
                "page": page_no,
                "width_pt": 595.0,
                "height_pt": 842.0,
                "lines": [
                    {
                        "line_id": page_no,
                        "block_id": 0,
                        "order": 0,
                        "text": "반복 머리말",
                        "bbox_pt": [10.1, 20.1, 100.0, 30.0],
                        "style_runs": [],
                    }
                ],
            }
        )
    document = _raw_v2_document(pages)

    annotate_document_in_place(document)

    assert all(page["lines"][0]["is_boilerplate"] for page in document["pages"])


def test_annotation_uses_text_repetition_when_all_geometry_is_null():
    pages = []
    for page_no in range(1, 4):
        pages.append(
            {
                "page": page_no,
                "width_pt": None,
                "height_pt": None,
                "lines": [
                    {
                        "line_id": page_no,
                        "block_id": None,
                        "order": 0,
                        "text": "반복 문구",
                        "bbox_pt": None,
                        "style_runs": [],
                    }
                ],
            }
        )
    document = _raw_v2_document(pages, source_format="hwp")

    annotate_document_in_place(document)

    assert all(page["lines"][0]["is_boilerplate"] for page in document["pages"])


def test_candidate_layer_merges_adjacent_wrapped_pdf_lines_within_one_block():
    first = _span(1, "입찰 계약")
    second = _span(2, "예정가격 5,000만원")
    first.update(block_id=7, order=0, bbox_pt=[10.0, 10.0, 200.0, 20.0])
    second.update(block_id=7, order=1, bbox_pt=[10.0, 20.2, 200.0, 30.2])
    document = _annotated_doc([first, second])

    candidate = find_candidates(document, "5")[0]
    legacy_segment = _merge_wrapped_lines(
        [
            {"text": first["text"], "bbox": first["bbox_pt"]},
            {"text": second["text"], "bbox": second["bbox_pt"]},
        ]
    )[0]

    assert candidate["line_ids"] == [1, 2]
    assert candidate["text"] == legacy_segment["text"] == "입찰 계약 예정가격 5,000만원"
    assert "bbox" not in candidate
    assert "span_id" not in candidate
    assert "source_pdf_path" not in candidate


def test_candidate_layer_keeps_geometry_null_hwp_lines_separate():
    first = _span(1, "입찰")
    second = _span(2, "계약")
    for order, line in enumerate((first, second)):
        line.update(block_id=None, order=order, bbox_pt=None)
    document = _annotated_doc([first, second])
    document["source_format"] = "hwp"

    candidates = find_candidates(document, "5")

    assert {tuple(candidate["line_ids"]) for candidate in candidates} == {(1,), (2,)}


def test_find_all_candidates_matches_compatibility_entrypoints_and_ids_are_stable():
    document = _annotated_doc([_span(1, "입찰 계약 검토안")], doc_type="approval")

    all_first = find_all_candidates(document)
    all_second = find_all_candidates(document)

    assert all_first == all_second
    assert all_first["5"] == find_candidates(document, "5")
    assert all_first["administrative"] == find_administrative_candidates(document)
    assert all_first["5"][0]["candidate_id"] != all_first["administrative"][0]["candidate_id"]
    assert all_first["5"][0]["text_sha256"]


def test_find_candidates_skips_boilerplate_spans():
    doc = _annotated_doc(
        [
            _span(1, "입찰 계약 체결", is_boilerplate=True),
            _span(2, "입찰 계약 체결"),
        ]
    )
    candidates = find_candidates(doc, "5")
    assert [c["line_ids"] for c in candidates] == [[2]]


def test_find_candidates_respects_per_document_cap():
    doc = _annotated_doc([_span(i, "입찰 계약 체결") for i in range(_MAX_CANDIDATES_PER_DOC + 5)])
    candidates = find_candidates(doc, "5")
    assert len(candidates) == _MAX_CANDIDATES_PER_DOC


def test_find_all_candidates_50k_line_stress_keeps_outputs_bounded():
    line_count = 50_000
    doc = _annotated_doc([_span(i, "입찰 계약 체결") for i in range(line_count)])

    results = find_all_candidates(doc)

    assert len(results["5"]) == _MAX_CANDIDATES_PER_DOC
    assert all(len(results[target]) <= _MAX_CANDIDATES_PER_DOC for target in results)
    assert [candidate["rank"] for candidate in results["5"]] == list(
        range(1, _MAX_CANDIDATES_PER_DOC + 1)
    )


def test_find_candidates_ranks_strong_evidence_ahead_of_early_generic_matches():
    spans = [_span(i, f"계약 사업비 {i + 1},000원") for i in range(_MAX_CANDIDATES_PER_DOC + 5)]
    spans.append(_span(999, "수의계약 입찰 예정가격 46,000,000원"))
    doc = _annotated_doc(spans)

    candidates = find_candidates(doc, "5")

    assert candidates[0]["line_ids"] == [999]
    assert candidates[0]["rank"] == 1
    assert "keyword:수의계약" in candidates[0]["matched_rules"]
    assert "keyword:계약" not in candidates[0]["matched_rules"]
    assert candidates[0]["match_score"] > candidates[-1]["match_score"]


def test_find_candidates_rejects_money_only_without_clause_context():
    doc = _annotated_doc(
        [
            _span(1, "사업 개요"),
            _span(2, "총 사업비 46,000,000원"),
            _span(3, "추진 일정"),
        ]
    )

    assert find_candidates(doc, "5") == []
    assert find_candidates(doc, "7") == []


def test_find_candidates_keeps_money_span_when_neighbor_has_clause_context():
    clause_5_doc = _annotated_doc(
        [_span(1, "입찰 계약 예정"), _span(2, "예정가격 46,000,000원")]
    )
    clause_7_doc = _annotated_doc(
        [_span(1, "납품단가 원가구조"), _span(2, "제안금액 46,000,000원")]
    )

    clause_5_money = next(c for c in find_candidates(clause_5_doc, "5") if c["line_ids"] == [2])
    clause_7_money = next(c for c in find_candidates(clause_7_doc, "7") if c["line_ids"] == [2])

    assert "keyword:입찰" in clause_5_money["context_rules"]
    assert "keyword:납품단가" in clause_7_money["context_rules"]


def test_find_candidates_preserves_non_boilerplate_neighbor_context():
    doc = _annotated_doc(
        [
            _span(1, "사업 개요"),
            _span(2, "반복 머리말", is_boilerplate=True),
            _span(3, "입찰 계약 체결"),
            _span(4, "예정가격은 5,000만원입니다."),
        ]
    )

    candidate = next(candidate for candidate in find_candidates(doc, "5") if candidate["line_ids"] == [3])

    assert candidate["context_before"] == ["사업 개요"]
    assert candidate["context_after"] == ["예정가격은 5,000만원입니다."]
    assert "반복 머리말" not in candidate["context_text"]
    assert "[대상] 입찰 계약 체결" in candidate["context_text"]


def test_find_candidates_rejects_non_v2_document():
    with pytest.raises(ValueError, match="schema_version=2"):
        find_candidates({}, "5")


def test_administrative_candidates_use_document_form_specific_attachment_rule():
    doc = _annotated_doc(
        [_span(1, "붙임 관련 검토자료 1부.")],
        doc_type="official_document",
    )

    candidates = find_administrative_candidates(doc)

    assert len(candidates) == 1
    assert candidates[0]["candidate_kind"] == "administrative_status"
    assert candidates[0]["document_status"] == "첨부미등록"
    assert candidates[0]["verification_requirement"] == "attachment_inventory"


def test_administrative_candidates_detect_petition_progress_only_for_reply_form():
    reply_doc = _annotated_doc(
        [_span(1, "민원 사항의 사실관계 확인 중입니다.")],
        doc_type="reply_notification",
    )
    official_doc = _annotated_doc(
        [_span(1, "민원 사항의 사실관계 확인 중입니다.")],
        doc_type="official_document",
    )

    reply_candidates = find_administrative_candidates(reply_doc)

    assert [candidate["document_status"] for candidate in reply_candidates] == ["민원처리중"]
    assert find_administrative_candidates(official_doc) == []


def test_administrative_candidates_treat_notice_draft_signal_as_draft_not_release_schedule():
    doc = _annotated_doc(
        [_span(1, "고용노동부고시 일부개정고시안")],
        doc_type="notification",
    )

    candidates = find_administrative_candidates(doc)

    assert [candidate["document_status"] for candidate in candidates] == ["초안"]


def test_administrative_candidates_preserve_multiple_statuses_for_one_span():
    doc = _annotated_doc(
        [_span(1, "초안 내부 검토 중으로 결재 중입니다.")],
        doc_type="approval",
    )

    statuses = {candidate["document_status"] for candidate in find_administrative_candidates(doc)}

    assert statuses == {"결재진행중", "초안", "내부검토중"}


def test_administrative_candidates_skip_boilerplate_and_unknown_document_type():
    boilerplate_doc = _annotated_doc(
        [_span(1, "붙임 관련 검토자료", is_boilerplate=True)],
        doc_type="official_document",
    )
    unknown_doc = _annotated_doc(
        [_span(1, "붙임 관련 검토자료")],
        doc_type="unclassified_form",
    )

    assert find_administrative_candidates(boilerplate_doc) == []
    assert find_administrative_candidates(unknown_doc) == []


# 2026-07-21 office-hours/plan-eng-review로 신규 추가된 7개 문서유형 규칙 —
# 아직 실사 미검증 초안이라는 점은 candidates.py의 해당 dict 주석 참고.
def test_administrative_candidates_press_release_detects_embargo_signal():
    doc = _annotated_doc(
        [_span(1, "본 보도자료는 엠바고 해제 전까지 배포 예정입니다.")],
        doc_type="press_release",
    )

    candidates = find_administrative_candidates(doc)

    assert [c["document_status"] for c in candidates] == ["공개예정일미도래"]


def test_administrative_candidates_notice_detects_draft_signal():
    doc = _annotated_doc(
        [_span(1, "공고안에 대한 검토를 진행 중입니다.")],
        doc_type="notice",
    )

    candidates = find_administrative_candidates(doc)

    assert "초안" in [c["document_status"] for c in candidates]


def test_administrative_candidates_bid_renotice_detects_attachment_rule():
    doc = _annotated_doc(
        [_span(1, "붙임 재공고문 1부.")],
        doc_type="bid_renotice",
    )

    candidates = find_administrative_candidates(doc)

    assert candidates[0]["document_status"] == "첨부미등록"
    assert candidates[0]["verification_requirement"] == "attachment_inventory"


def test_administrative_candidates_public_offering_detects_review_signal():
    doc = _annotated_doc(
        [_span(1, "공모요강 검토 중이며 심사기준은 추후 확정될 예정입니다.")],
        doc_type="public_offering",
    )

    candidates = find_administrative_candidates(doc)

    assert "내부검토중" in [c["document_status"] for c in candidates]


def test_administrative_candidates_budget_material_detects_review_and_liaison_signals():
    doc = _annotated_doc(
        [_span(1, "예산(안)에 대해 기획재정부 협의를 진행 중입니다.")],
        doc_type="budget_material",
    )

    statuses = {c["document_status"] for c in find_administrative_candidates(doc)}

    assert statuses == {"내부검토중", "타기관협의중"}


def test_administrative_candidates_interpretation_compilation_detects_revision_signal():
    doc = _annotated_doc(
        [_span(1, "질의회시 내용은 개정 예정으로 정비 중입니다.")],
        doc_type="interpretation_compilation",
    )

    candidates = find_administrative_candidates(doc)

    assert [c["document_status"] for c in candidates] == ["문서정리중"]


def test_administrative_candidates_pre_spec_notice_detects_review_signal():
    doc = _annotated_doc(
        [_span(1, "사전규격 검토 중인 제안요청서 초안입니다.")],
        doc_type="pre_spec_notice",
    )

    candidates = find_administrative_candidates(doc)

    assert "내부검토중" in [c["document_status"] for c in candidates]


def test_administrative_candidates_deferred_doc_types_still_have_no_rules():
    """director_activity(xlsx 미지원)/business_trip·budget_execution(open_go_kr
    P0 차단)은 2026-07-21 설계에서 의도적으로 이번 확장 대상에서 제외됐다 —
    회귀 확인용."""
    for doc_type in ("director_activity", "business_trip", "budget_execution"):
        doc = _annotated_doc([_span(1, "붙임 결재 중 검토안")], doc_type=doc_type)
        assert find_administrative_candidates(doc) == []
