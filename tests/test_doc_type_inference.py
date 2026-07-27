from rd2.generators.doc_type_inference import infer_doc_type
from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
)


class TestInferDocType:
    def test_clause_1_is_official_document(self):
        assert infer_doc_type("1") == DOC_TYPE_OFFICIAL_DOCUMENT

    def test_clause_2_is_policy_material(self):
        assert infer_doc_type("2") == DOC_TYPE_POLICY_MATERIAL

    def test_clause_3_is_report(self):
        assert infer_doc_type("3") == DOC_TYPE_REPORT

    def test_clause_4_is_meeting_minutes(self):
        assert infer_doc_type("4") == DOC_TYPE_MEETING_MINUTES

    def test_clause_6_is_personnel(self):
        assert infer_doc_type("6") == DOC_TYPE_PERSONNEL

    def test_clause_6_welfare_submission_is_official_document(self):
        assert (
            infer_doc_type(
                "6",
                keyword_text="2023년 사회복지 급여 수급자 자격 심사 자료 제출",
            )
            == DOC_TYPE_OFFICIAL_DOCUMENT
        )

    def test_clause_6_welfare_approval_is_approval(self):
        assert infer_doc_type("6", keyword_text="복지급여 지급 승인") == DOC_TYPE_APPROVAL

    def test_clause_6_petition_result_is_reply_notification(self):
        assert (
            infer_doc_type("6", keyword_text="민원 처리 결과 통보")
            == DOC_TYPE_REPLY_NOTIFICATION
        )

    def test_clause_7_is_report(self):
        assert infer_doc_type("7") == DOC_TYPE_REPORT

    def test_clause_8_is_plan(self):
        assert infer_doc_type("8") == DOC_TYPE_PLAN

    def test_unknown_clause_falls_back_to_official_document(self):
        assert infer_doc_type("99") == DOC_TYPE_OFFICIAL_DOCUMENT

    def test_clause_5_audit_keyword(self):
        assert infer_doc_type("5", keyword_text="내부 감사 착수 전 검토") == DOC_TYPE_AUDIT_RESULT

    def test_clause_5_inspection_keyword_also_maps_to_audit(self):
        assert infer_doc_type("5", keyword_text="정기 검사 결과 보고") == DOC_TYPE_AUDIT_RESULT

    def test_clause_5_bid_keyword(self):
        assert infer_doc_type("5", keyword_text="대형 입찰 사업 평가위원 선정") == DOC_TYPE_BID_NOTICE

    def test_clause_5_contract_keyword_also_maps_to_bid(self):
        assert infer_doc_type("5", keyword_text="용역 계약 체결 관련 검토") == DOC_TYPE_BID_NOTICE

    def test_clause_5_personnel_keyword(self):
        assert infer_doc_type("5", keyword_text="차기 임원 인사 후보군 평가") == DOC_TYPE_PERSONNEL

    def test_clause_5_no_keyword_match_falls_back_to_approval(self):
        assert infer_doc_type("5", keyword_text="신기술 연구개발 과제 중간평가") == DOC_TYPE_APPROVAL

    def test_clause_5_no_keyword_text_at_all_falls_back_to_approval(self):
        assert infer_doc_type("5") == DOC_TYPE_APPROVAL
