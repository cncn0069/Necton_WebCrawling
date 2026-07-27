"""doc_templates(조항×문서유형 조합 템플릿) — T5-1 결재선 미완료 템플릿 검증.

2026-07-16: 제5호(내부검토 과정) 승인/품의 문서의 결재란이 항상 완결
상태로 렌더링되던 모순을 템플릿 선언으로 고친 것을 검증한다.

결재선 형식은 실제 orginl_info 수집 공문("9급 공채 임용예정자 실무수습
발령" 등)에서 확인된 관례를 따른다 — 직위명(주무관/사무관/과장/국장)
컬럼 + 아래 행에 서명자 성명, 미결재 직위는 성명 공란.
"""

from rd2.generators.doc_templates import (
    AdminStatus,
    ApprovalState,
    find_status_variant,
    find_template,
    TEMPLATE_VARIANTS,
    validate_row,
)
from rd2.generators.pdf_render import (
    _build_approval_box,
    _build_audit_interim_body_flowables,
    _build_bid_review_body_flowables,
    _build_personnel_order_body_flowables,
    _build_signature_line,
    _build_unit_price_body_flowables,
    Table,
)
from rd2.generators.template_matrix import infer_subclause_key
from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_REPORT,
)
_BODY_STYLE = None
_DIVIDER_STYLE = None


def _table_cells(flowables) -> list[list[str]]:
    tables = [f for f in flowables if isinstance(f, Table)]
    assert len(tables) == 1
    return tables[0]._cellvalues


class TestFindTemplate:
    def test_all_61_subclause_document_targets_have_unique_templates(self):
        """61 = 56 + 5호 bid_contract/decision_review 그룹 확장분(T5-10~T5-15,
        2026-07-21 신규 문서유형-조건부 행정상태 축 추가로 5호가 10개→16개)."""
        assert len(TEMPLATE_VARIANTS) == 61
        ids = [spec.template_id for spec in TEMPLATE_VARIANTS.values()]
        assert len(ids) == len(set(ids))

    def test_same_clause_and_doc_type_can_select_different_subclauses(self):
        life = find_template("3", DOC_TYPE_REPORT, "life_body")
        property_doc = find_template("3", DOC_TYPE_REPORT, "property")
        assert life is not None and property_doc is not None
        assert life.template_id != property_doc.template_id

    def test_clause5_approval_combination_has_template(self):
        spec = find_template("5", DOC_TYPE_APPROVAL)
        assert spec is not None
        assert spec.template_id == "T5-1"
        assert spec.approval_state is ApprovalState.PENDING
        assert spec.recipient == "내부결재"

    def test_clauses_1_to_4_have_dedicated_templates(self):
        cases = (
            ("1", DOC_TYPE_OFFICIAL_DOCUMENT, "T1-1", "legal_confidential"),
            ("2", DOC_TYPE_POLICY_MATERIAL, "T2-1", "national_security"),
            ("3", DOC_TYPE_REPORT, "T3-1", "public_safety"),
            ("4", DOC_TYPE_MEETING_MINUTES, "T4-1", "legal_proceeding"),
        )
        for clause_no, doc_type, template_id, body_format in cases:
            spec = find_template(clause_no, doc_type)
            assert spec is not None
            assert spec.template_id == template_id
            assert spec.body_format == body_format
            assert spec.disclosure_label == f"비공개({clause_no})"

    def test_undeclared_combination_returns_none(self):
        assert find_template("5", DOC_TYPE_OFFICIAL_DOCUMENT) is None

    def test_clause4_rejects_completed_proceeding_phrases(self):
        spec = find_template("4", DOC_TYPE_MEETING_MINUTES)
        assert spec is not None
        violations = validate_row(spec, {"body_text": "사건 종결 후 결과를 보고함"})
        assert len(violations) == 1
        assert "사건 종결" in violations[0]

    def test_clause_no_is_normalized(self):
        # CSV에서 읽은 값에 공백이 있어도 매칭돼야 한다.
        assert find_template(" 5 ", DOC_TYPE_APPROVAL) is not None

    def test_subclause_inference_disambiguates_same_doc_type(self):
        assert infer_subclause_key("3", DOC_TYPE_REPORT, keyword_text="재산 피해 보상") == "property"
        assert infer_subclause_key("3", DOC_TYPE_REPORT, keyword_text="생명 보호 대책") == "life_body"

    def test_subclause_inference_uses_unique_matrix_match(self):
        assert infer_subclause_key("6", DOC_TYPE_PERSONNEL) == "personnel_pii"


class TestAdministrativeStatusDefinitions:
    def test_all_administrative_statuses_have_a_variant_definition(self):
        for status in AdminStatus:
            variant = find_status_variant(status.value)
            assert variant is not None
            assert variant.status is status


class TestSignatureLine:
    """T5-1 핵심 — 직위명 컬럼 + 성명 행, 미결재 직위는 성명 공란."""

    _POSITIONS = ("주무관", "사무관", "과장", "국장")

    def test_pending_leaves_unsigned_positions_blank(self):
        flowables = _build_signature_line(self._POSITIONS, 1, _BODY_STYLE, seed=3)
        cells = _table_cells(flowables)
        # 기안자 ★ 접두 — 실물 승인 문서(경남교육청, 2026-07-16 수집)에서 확인된 관례.
        assert cells[0] == ["★주무관", "사무관", "과장", "국장"]
        assert cells[1][0] != ""  # 기안자(주무관)는 서명됨
        assert cells[1][1:] == ["", "", ""]  # 사무관 이상은 공란

    def test_fully_signed_line_has_all_names(self):
        flowables = _build_signature_line(self._POSITIONS, len(self._POSITIONS), _BODY_STYLE, seed=3)
        cells = _table_cells(flowables)
        assert all(name != "" for name in cells[1])

    def test_signer_names_are_deterministic_per_seed(self):
        a = _table_cells(_build_signature_line(self._POSITIONS, 2, _BODY_STYLE, seed=7))
        b = _table_cells(_build_signature_line(self._POSITIONS, 2, _BODY_STYLE, seed=7))
        assert a[1] == b[1]

    def test_empty_positions_render_nothing(self):
        assert _build_signature_line((), 0, _BODY_STYLE) == []


class TestAuditInterimTemplate:
    """T5-2 — 진행중 감사 중간보고: 결과 단계 요소(지적사항 확정·처분) 금지."""

    def test_clause5_audit_combination_has_template(self):
        spec = find_template("5", DOC_TYPE_AUDIT_RESULT)
        assert spec is not None
        assert spec.template_id == "T5-2"
        assert spec.body_format == "audit_interim"
        assert spec.approval_positions == ("감사담당", "감사팀장", "감사실장")

    def test_interim_body_has_overview_progress_and_plan_sections(self):
        row = {
            "body_text": "복무점검 실시\n대상 부서 자료 징구 완료",
            "production_date": "2026-06-01",
            "department": "총무과",
        }
        texts = [f.text for f in _build_audit_interim_body_flowables(row, _BODY_STYLE) if hasattr(f, "text")]
        assert any("1. 감사개요" in t for t in texts)
        assert any("2. 진행 경과" in t for t in texts)
        assert any("3. 향후 계획" in t for t in texts)
        assert any("(진행중)" in t for t in texts)  # 감사기간이 열려 있어야 한다

    def test_interim_body_masks_names_with_ooo(self):
        texts = [f.text for f in _build_audit_interim_body_flowables({}, _BODY_STYLE) if hasattr(f, "text")]
        assert any("OOO" in t for t in texts)

    def test_interim_body_has_no_result_stage_sections(self):
        # 실제 감사"결과"보고서의 결과 단계 섹션이 중간보고에 나오면 안 된다.
        texts = [f.text for f in _build_audit_interim_body_flowables({}, _BODY_STYLE) if hasattr(f, "text")]
        assert not any("지적사항 일람표" in t for t in texts)
        assert not any("처분 내역" in t for t in texts)

    def test_result_stage_phrase_in_body_is_reported(self):
        spec = find_template("5", DOC_TYPE_AUDIT_RESULT)
        row = {"body_text": "감사 종결 처리하고 관련자 처분 확정함."}
        violations = validate_row(spec, row)
        assert len(violations) == 2  # "감사 종결" + "처분 확정"
        assert all("T5-2" in v for v in violations)


class TestPersonnelOrderTemplate:
    """T6-1 — 개인정보 포함 인사발령: 성명 마스킹 없음 + 결재선 완결."""

    def test_clause6_personnel_combination_has_template(self):
        spec = find_template("6", DOC_TYPE_PERSONNEL)
        assert spec is not None
        assert spec.template_id == "T6-1"
        assert spec.approval_state is ApprovalState.COMPLETE
        assert spec.body_format == "personnel_order"

    def test_order_table_has_unmasked_synthetic_names(self):
        row = {"row_id": "6-prism-0", "ordering_agency": "인천지방조달청", "production_date": "2026-07-01"}
        flowables = _build_personnel_order_body_flowables(row, _BODY_STYLE, _DIVIDER_STYLE)
        tables = [f for f in flowables if isinstance(f, Table)]
        assert len(tables) == 1
        cells = tables[0]._cellvalues
        assert cells[0] == ["소  속", "직  급", "성  명", "주민등록번호", "발 령 사 항"]
        for data_row in cells[1:]:
            name = data_row[2]
            rrn = data_row[3]
            assert name and "O" not in name and "○" not in name  # 마스킹 없는 합성 인명
            assert rrn and "O" not in rrn and "○" not in rrn  # 마스킹 없는 합성 주민등록번호

    def test_masking_marker_in_body_is_reported(self):
        # 6호는 "개인정보가 그대로 있어서 비공개"가 시나리오 — 마스킹돼 있으면 모순.
        spec = find_template("6", DOC_TYPE_PERSONNEL)
        row = {"body_text": "발령 대상자 OOO에 대하여 비식별 처리 완료함."}
        violations = validate_row(spec, row)
        assert len(violations) == 2  # "OOO" + "비식별 처리"

    def test_order_rows_are_deterministic_per_row_id(self):
        row = {"row_id": "6-prism-1", "ordering_agency": "조달청"}
        a = _build_personnel_order_body_flowables(row, _BODY_STYLE, _DIVIDER_STYLE)
        b = _build_personnel_order_body_flowables(row, _BODY_STYLE, _DIVIDER_STYLE)
        cells_a = [f for f in a if isinstance(f, Table)][0]._cellvalues
        cells_b = [f for f in b if isinstance(f, Table)][0]._cellvalues
        assert cells_a == cells_b


class TestBidReviewTemplate:
    """T5-3 — 입찰공고 전 내부검토안: (안) 상태 + 절차 완료 표현 금지."""

    def test_clause5_bid_combination_has_template(self):
        spec = find_template("5", DOC_TYPE_BID_NOTICE)
        assert spec is not None
        assert spec.template_id == "T5-3"
        assert spec.approval_state is ApprovalState.PENDING

    def test_review_body_keeps_draft_markers(self):
        row = {"body_text": "차세대 행정정보시스템 구축 사업", "production_date": "2026-07-01"}
        texts = [f.text for f in _build_bid_review_body_flowables(row, _BODY_STYLE) if hasattr(f, "text")]
        assert any("평가위원회 구성(안)" in t for t in texts)
        assert any("배점 기준(안)" in t for t in texts)
        assert any("비공개 관리" in t for t in texts)  # 위원 명단 비공개

    def test_review_body_has_score_table(self):
        tables = [f for f in _build_bid_review_body_flowables({}, _BODY_STYLE) if isinstance(f, Table)]
        assert len(tables) == 1
        assert tables[0]._cellvalues[0] == ["구분", "배점비율", "평가요소"]

    def test_completed_procedure_phrase_is_reported(self):
        spec = find_template("5", DOC_TYPE_BID_NOTICE)
        row = {"body_text": "적격심사 완료 후 낙찰자 선정을 마쳤음."}
        assert len(validate_row(spec, row)) == 2


class TestUnitPriceTemplate:
    """T7-2 — 업체 납품단가: 합성 업체명+단가표가 그대로 있어야 한다."""

    def test_clause7_bid_combination_has_template(self):
        spec = find_template("7", DOC_TYPE_BID_NOTICE)
        assert spec is not None
        assert spec.template_id == "T7-2"
        assert spec.approval_state is ApprovalState.COMPLETE

    def test_price_table_has_items_and_total(self):
        row = {"row_id": "7-prism-0"}
        flowables = _build_unit_price_body_flowables(row, _BODY_STYLE)
        tables = [f for f in flowables if isinstance(f, Table)]
        assert len(tables) == 1
        cells = tables[0]._cellvalues
        assert cells[0] == ["품목", "규격", "수량", "단가(원)", "금액(원)"]
        assert cells[-1][0] == "합계"
        assert "," in cells[-1][4]  # 천단위 구분 금액

    def test_supplier_name_is_in_body(self):
        texts = [f.text for f in _build_unit_price_body_flowables({"row_id": "x"}, _BODY_STYLE) if hasattr(f, "text")]
        assert any("납품단가 협상" in t for t in texts)
        assert any("영업상 비밀" in t for t in texts)


class TestClause7DocTypeInference:
    def test_price_keywords_route_to_bid_notice(self):
        from rd2.generators.doc_type_inference import infer_doc_type

        assert infer_doc_type("7", keyword_text="협력업체 납품단가 협상") == DOC_TYPE_BID_NOTICE
        assert infer_doc_type("7", keyword_text="기술 특허 명세서") != DOC_TYPE_BID_NOTICE


class TestMeetingPendingTemplate:
    """T5-5 — 심의 진행중 회의록: 논의결과 <보류> + 위원 명단 비공개."""

    def test_clause5_meeting_combination_has_template(self):
        from rd2.storage.naming import DOC_TYPE_MEETING_MINUTES

        spec = find_template("5", DOC_TYPE_MEETING_MINUTES)
        assert spec is not None
        assert spec.template_id == "T5-5"
        assert spec.approval_state is ApprovalState.PENDING

    def test_pending_minutes_defer_conclusion_and_hide_members(self):
        from rd2.generators.pdf_render import _build_meeting_pending_body_flowables

        row = {"row_id": "5-prism-11", "body_text": "남부권 개발사업 심의\n사업 수요 근거가 부족하다는 지적\n수요 조사를 보완하여 제출하겠음", "production_date": "2026-07-01"}
        texts = [f.text for f in _build_meeting_pending_body_flowables(row, _BODY_STYLE) if hasattr(f, "text")]
        assert any("보류" in t for t in texts)
        assert any("비공개" in t for t in texts)  # 위원 명단
        assert any(t.startswith("ㅇ") for t in texts)
        assert any(t.startswith("☞") for t in texts)  # 실제 회의록의 질문↔답변 관례

    def test_final_decision_phrase_is_reported(self):
        from rd2.storage.naming import DOC_TYPE_MEETING_MINUTES

        spec = find_template("5", DOC_TYPE_MEETING_MINUTES)
        row = {"body_text": "금일 안건은 원안 의결로 심의 확정되었음."}
        assert len(validate_row(spec, row)) >= 2


class TestPersonnelEvalTemplate:
    """T5-4 — 인사평가(후보군 검토): 평가기간 열림 + 확정 표현 금지."""

    def test_clause5_personnel_combination_has_template(self):
        spec = find_template("5", DOC_TYPE_PERSONNEL)
        assert spec is not None
        assert spec.template_id == "T5-4"
        assert spec.approval_state is ApprovalState.PENDING

    def test_eval_body_keeps_period_open_with_candidate_table(self):
        from rd2.generators.pdf_render import _build_personnel_eval_body_flowables

        row = {"row_id": "5-prism-12", "production_date": "2026-06-15"}
        flowables = _build_personnel_eval_body_flowables(row, _BODY_STYLE)
        texts = [f.text for f in flowables if hasattr(f, "text")]
        assert any("(진행중)" in t for t in texts)
        tables = [f for f in flowables if isinstance(f, Table)]
        assert len(tables) == 1
        assert all(data_row[-1] == "평가 진행중" for data_row in tables[0]._cellvalues[1:])

    def test_confirmed_appointment_phrase_is_reported(self):
        spec = find_template("5", DOC_TYPE_PERSONNEL)
        row = {"body_text": "평가 결과에 따라 발령 확정 처리함."}
        assert len(validate_row(spec, row)) == 1


class TestCivilReplyTemplate:
    """T6-2 — 개인정보 포함 민원회신: 수신자 개인 + PII 마스킹 없음."""

    def test_clause6_reply_combination_has_template(self):
        from rd2.generators.doc_templates import RECIPIENT_CIVIL_PETITIONER
        from rd2.storage.naming import DOC_TYPE_REPLY_NOTIFICATION

        spec = find_template("6", DOC_TYPE_REPLY_NOTIFICATION)
        assert spec is not None
        assert spec.template_id == "T6-2"
        assert spec.recipient == RECIPIENT_CIVIL_PETITIONER
        assert spec.approval_state is ApprovalState.COMPLETE

    def test_reply_body_has_unmasked_petitioner_info(self):
        from rd2.generators.pdf_render import _build_civil_reply_body_flowables

        row = {"row_id": "6-prism-3", "body_text": "인근 공사장 소음 관련 민원\n현장 점검 결과 기준치 이내로 확인됨"}
        flowables = _build_civil_reply_body_flowables(row, _BODY_STYLE)
        tables = [f for f in flowables if isinstance(f, Table)]
        assert len(tables) == 1
        cells = tables[0]._cellvalues
        labels = [r[0] for r in cells]
        assert labels == ["성  명", "연 락 처", "주  소"]
        assert all(r[1] and "O" not in r[1] for r in cells)  # 마스킹 없는 합성값
        texts = [f.text for f in flowables if hasattr(f, "text")]
        assert any("접수번호" in t for t in texts)

    def test_masking_marker_is_reported(self):
        from rd2.storage.naming import DOC_TYPE_REPLY_NOTIFICATION

        spec = find_template("6", DOC_TYPE_REPLY_NOTIFICATION)
        row = {"body_text": "민원인 ○○○의 정보는 비식별 처리하였음."}
        assert len(validate_row(spec, row)) == 2

    def test_petition_in_progress_does_not_render_as_final_reply(self):
        from rd2.generators.doc_templates import AdminStatus
        from rd2.generators.pdf_render import _build_civil_reply_body_flowables

        row = {
            "row_id": "6-petition-in-progress",
            "document_status": AdminStatus.PETITION_IN_PROGRESS.value,
            "body_text": "생활소음 민원\n현장점검과 사실관계 확인을 진행 중임.",
        }
        texts = [f.text for f in _build_civil_reply_body_flowables(row, _BODY_STYLE) if hasattr(f, "text")]
        assert any("진행 상황" in text for text in texts)
        assert any("별도로 안내드릴 예정" in text for text in texts)
        assert not any("검토 결과를 아래와 같이 회신" in text for text in texts)


class TestLegacyApprovalBox:
    """템플릿이 없는 조합은 기존 부서명 키워드 동작을 그대로 유지해야 한다."""

    def test_no_state_keeps_legacy_keyword_behavior(self):
        assert _build_approval_box("감사담당관실", _BODY_STYLE) != []
        assert _build_approval_box("홍보담당관실", _BODY_STYLE) == []

    def test_none_state_renders_no_box(self):
        assert _build_approval_box("감사담당관실", _BODY_STYLE, state=ApprovalState.NONE) == []


class TestValidateRow:
    def test_forbidden_phrase_in_body_is_reported(self):
        spec = find_template("5", DOC_TYPE_APPROVAL)
        row = {"body_text": "관련 부서 협의를 거쳐 최종 승인 처리하였음."}
        violations = validate_row(spec, row)
        assert len(violations) == 1
        assert "최종 승인" in violations[0]
        assert "T5-1" in violations[0]

    def test_consistent_body_passes(self):
        spec = find_template("5", DOC_TYPE_APPROVAL)
        row = {"body_text": "현재 부서 협의 중이며 결과 확정 전까지 비공개 유지 예정."}
        assert validate_row(spec, row) == []

    def test_missing_body_text_does_not_crash(self):
        spec = find_template("5", DOC_TYPE_APPROVAL)
        assert validate_row(spec, {}) == []

    def test_unknown_explicit_subclause_does_not_fall_back(self):
        assert find_template("5", DOC_TYPE_APPROVAL, "not-a-real-subclause") is None
