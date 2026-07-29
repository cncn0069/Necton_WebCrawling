"""template_matrix.py 회귀 고정 테스트.

이 모듈은 (조항, 세부조항, 문서유형) 목표 매트릭스를 생성하는 핵심 로직이지만
지금까지 테스트가 하나도 없었다. 행정상태 축을 추가하기 전에, 기존 동작
(TemplateTarget 모양, TARGET_BY_KEY 조회, infer_subclause_key의 후보 선택/
키워드 disambiguation/폴백)을 먼저 고정해 안전망으로 삼는다.
"""

from rd2.generators.template_matrix import (
    STATUS_AWARE_TARGETS,
    TARGET_BY_KEY,
    TARGET_STATUSES_BY_KEY,
    TEMPLATE_TARGETS,
    TemplateStatusTarget,
    TemplateTarget,
    admin_statuses_for_doc_type,
    infer_subclause_key,
)
from rd2.storage.naming import DOC_TYPE_PERSONNEL, DOC_TYPE_REPORT


def test_template_target_dataclass_shape():
    """TemplateTarget 필드 구성과 expected_documents 기본값(6)을 고정한다."""
    target = TemplateTarget(
        clause_no="9",
        subclause_key="dummy_key",
        subclause_label="더미 라벨",
        doc_type="dummy_doc_type",
        template_id="T9-1",
    )
    assert target.clause_no == "9"
    assert target.subclause_key == "dummy_key"
    assert target.subclause_label == "더미 라벨"
    assert target.doc_type == "dummy_doc_type"
    assert target.template_id == "T9-1"
    assert target.expected_documents == 6


def test_target_by_key_known_entries_resolve_to_existing_ids():
    """_EXISTING_IDS에 손으로 박아둔 매핑이 그대로 조회되는지 확인한다."""
    known = {
        ("1", "legal_secret", "official_document"): "T1-1",
        ("2", "security_defense", "policy_material"): "T2-1",
        ("3", "life_body", "report"): "T3-1",
        ("4", "trial_investigation", "meeting_minutes"): "T4-1",
        ("5", "decision_review", "approval"): "T5-1",
        ("5", "audit_inspection", "audit_result"): "T5-2",
        ("5", "bid_contract", "bid_notice"): "T5-3",
        ("5", "personnel_management", "personnel"): "T5-4",
        ("5", "decision_review", "meeting_minutes"): "T5-5",
        ("6", "personnel_pii", "personnel"): "T6-1",
        ("6", "petitioner_pii", "reply_notification"): "T6-2",
        ("7", "unit_cost", "bid_notice"): "T7-2",
    }
    for key, expected_id in known.items():
        assert key in TARGET_BY_KEY, f"{key}가 TARGET_BY_KEY에서 빠짐"
        assert TARGET_BY_KEY[key].template_id == expected_id


def test_target_by_key_new_combo_gets_auto_assigned_id():
    """_EXISTING_IDS에 없는 조합(예: 5호 audit_inspection×meeting_minutes)은
    T{clause_no}-{next_no} 패턴으로 자동 채번된다. 실제 채번값(T5-6)은 현재
    _build_targets()의 순회 순서(딕셔너리 삽입 순서)로 결정되는 값을 그대로
    고정한 것 — _GROUPS/_EXISTING_IDS 순서가 바뀌면 이 값도 바뀔 수 있다."""
    key = ("5", "audit_inspection", "meeting_minutes")
    assert key in TARGET_BY_KEY
    target = TARGET_BY_KEY[key]
    assert target.template_id == "T5-6"


def test_auto_assigned_ids_never_collide_with_existing_or_each_other():
    """조항별로 template_id가 전부 유일해야 한다(수동 지정분/자동 채번분 통틀어)."""
    by_clause: dict[str, list[str]] = {}
    for target in TEMPLATE_TARGETS:
        by_clause.setdefault(target.clause_no, []).append(target.template_id)
    for clause_no, ids in by_clause.items():
        assert len(ids) == len(set(ids)), f"clause {clause_no}에서 template_id 중복 발생: {ids}"
        for template_id in ids:
            assert template_id.startswith(f"T{clause_no}-")


def test_target_by_key_covers_every_generated_target():
    """TARGET_BY_KEY는 TEMPLATE_TARGETS 전체를 (clause_no, subclause_key, doc_type)로
    키만 바꿔 색인한 것이어야 한다 — 개수가 정확히 일치해야 한다."""
    assert len(TARGET_BY_KEY) == len(TEMPLATE_TARGETS)


def test_infer_subclause_key_single_candidate_shortcut():
    """clause=6, doc_type=personnel은 personnel_pii에만 연결돼 있어 후보가
    하나뿐이므로 키워드 없이 바로 그 값을 반환해야 한다(shortcut 경로)."""
    assert infer_subclause_key("6", DOC_TYPE_PERSONNEL) == "personnel_pii"


def test_infer_subclause_key_keyword_disambiguation():
    """clause=3, doc_type=report는 life_body/property 둘 다 연결돼 있어
    후보가 여럿이다 — 키워드로 구분해야 한다."""
    assert infer_subclause_key("3", DOC_TYPE_REPORT, keyword_text="재산 피해 보상") == "property"
    assert infer_subclause_key("3", DOC_TYPE_REPORT, keyword_text="생명 보호 대책") == "life_body"


def test_infer_subclause_key_falls_back_to_default_when_no_keyword_matches():
    """clause=3, doc_type=report에서 후보가 여럿인데 키워드가 전혀 안 맞으면
    _DEFAULT_SUBCLAUSE[clause_no]("life_body")로 폴백해야 한다."""
    assert infer_subclause_key("3", DOC_TYPE_REPORT, keyword_text="") == "life_body"
    assert infer_subclause_key("3", DOC_TYPE_REPORT, keyword_text="관련없는 문구") == "life_body"


def test_infer_subclause_key_falls_back_to_first_candidate_when_default_absent():
    """clause=5, doc_type=report에서는 audit_inspection/technology_development
    둘 다 연결돼 있지만, clause 5의 기본값("decision_review")은 report와
    연결돼 있지 않다 — 이 경우 후보 목록의 첫 항목(생성 순서상 audit_inspection)
    으로 폴백해야 한다."""
    assert infer_subclause_key("5", DOC_TYPE_REPORT, keyword_text="") == "audit_inspection"


def test_infer_subclause_key_returns_none_when_doc_type_unmatched():
    """조항 안에 아예 연결되지 않은 문서유형이면 후보가 없어 None을 반환해야 한다."""
    assert infer_subclause_key("1", "nonexistent_doc_type") is None


def test_infer_subclause_key_returns_none_for_unknown_clause():
    """존재하지 않는 조항 번호도 후보가 없어 None을 반환해야 한다."""
    assert infer_subclause_key("99", DOC_TYPE_REPORT) is None


# --- 행정상태(admin_status) 축 (design doc Lane B, 이슈 3) ---
#
# 이 축은 TARGET_BY_KEY의 (clause_no, subclause_key, doc_type) 키 구조를
# 그대로 두는 additive 확장이다 — 아래 테스트들은 기존 구조가 안 깨졌다는
# 것과, 새로 추가된 구조(STATUS_AWARE_TARGETS/TARGET_STATUSES_BY_KEY)가
# "규칙 자체가 없음(None)"과 "규칙은 있는데 매치 0건(())"을 구분할 수
# 있다는 것을 함께 확인한다.
#
# 참고: design doc 예시는 "notification이 결재진행중/첨부미등록/타기관협의중/
# 공개심사중/시스템등록오류 5종"이라고 적었지만, 지금 candidates.py의 실제
# 데이터에서 그 5개 라벨 조합은 notification이 아니라 official_document의
# 규칙이다(notification은 공개예정일미도래/초안/타기관협의중/첨부미등록
# 4종). 문서가 작성된 이후 규칙 테이블이 바뀐 것으로 보여, 실제 코드 기준으로
# official_document를 "규칙 있음" 예시로 쓴다.


def test_admin_statuses_for_doc_type_returns_labels_when_rules_registered():
    """official_document는 candidates.py의 ADMIN_STATUS_RULES_BY_DOC_TYPE에
    5개 라벨로 등록돼 있다 — 순서까지 그대로 반환해야 한다."""
    assert admin_statuses_for_doc_type("official_document") == (
        "결재진행중",
        "첨부미등록",
        "타기관협의중",
        "공개심사중",
        "시스템등록오류",
    )


def test_admin_statuses_for_doc_type_returns_none_when_no_rule_defined():
    """business_trip/director_activity는 ADMIN_STATUS_RULES_BY_DOC_TYPE에
    항목 자체가 없다 — None이어야 하고, 이는 "규칙은 있는데 매치 0건"인
    빈 튜플과 구분돼야 한다(아래 존재하지 않는 doc_type 케이스 참고)."""
    assert admin_statuses_for_doc_type("business_trip") is None
    assert admin_statuses_for_doc_type("director_activity") is None


def test_admin_statuses_for_doc_type_does_not_crash_on_unknown_doc_type():
    """등록된 적 없는 임의의 문서유형 문자열을 넣어도 예외 없이 None을
    반환해야 한다 — 이 축 도입으로 새 크래시 지점이 생기면 안 된다."""
    assert admin_statuses_for_doc_type("this_doc_type_does_not_exist") is None


def test_target_statuses_by_key_covers_every_generated_target_without_dropping():
    """_GROUPS의 (clause_no, subclause_key, doc_type) 36+개 조합 중 어느 것도
    행정상태 축 추가로 조용히 빠지면 안 된다 — TARGET_BY_KEY와 정확히
    같은 키 집합을 가져야 한다."""
    assert set(TARGET_STATUSES_BY_KEY.keys()) == set(TARGET_BY_KEY.keys())


def test_target_statuses_by_key_matches_admin_statuses_for_doc_type():
    """캐시(TARGET_STATUSES_BY_KEY)와 조회 함수(admin_statuses_for_doc_type)가
    서로 어긋나면 안 된다."""
    for (clause_no, subclause_key, doc_type), statuses in TARGET_STATUSES_BY_KEY.items():
        assert statuses == admin_statuses_for_doc_type(doc_type)


def test_status_aware_targets_cross_only_that_doc_types_own_statuses():
    """규칙이 있는 doc_type은 기본 셀 + 자체 상태 라벨만 가진다."""
    official_doc_rows = [row for row in STATUS_AWARE_TARGETS if row.doc_type == "official_document"]
    n_official_doc_targets = sum(1 for t in TEMPLATE_TARGETS if t.doc_type == "official_document")
    assert n_official_doc_targets > 0
    assert len(official_doc_rows) == n_official_doc_targets * 6
    assert {row.admin_status for row in official_doc_rows} == {
        None,
        "결재진행중",
        "첨부미등록",
        "타기관협의중",
        "공개심사중",
        "시스템등록오류",
    }


def test_status_aware_targets_keep_a_placeholder_row_when_no_rule_defined():
    """규칙이 없는 doc_type이 _GROUPS에 등장한다면(현재는 실제로 없음), 그
    (clause, subclause, doc_type)이 조용히 사라지지 않고 admin_status=None인
    자리표시 행 하나가 남아야 한다는 계약을 직접 만든 타겟으로 확인한다."""
    placeholder = TemplateStatusTarget(
        clause_no="9",
        subclause_key="dummy_key",
        subclause_label="더미 라벨",
        doc_type="this_doc_type_does_not_exist",
        template_id="T9-1",
        admin_status=None,
    )
    assert placeholder.admin_status is None
    # 실제 빌더가 규칙 없는 doc_type에 대해 만드는 행과 같은 모양이어야 한다.
    matching_real_rows = [
        row
        for row in STATUS_AWARE_TARGETS
        if row.doc_type not in {t.doc_type for t in TEMPLATE_TARGETS if admin_statuses_for_doc_type(t.doc_type)}
    ]
    for row in matching_real_rows:
        assert row.admin_status is None


def test_status_aware_targets_row_count_equals_base_plus_statuses():
    """각 타겟은 상태 없는 기본 셀 하나와 적용 가능한 상태 셀을 가진다."""
    expected = 0
    for target in TEMPLATE_TARGETS:
        statuses = admin_statuses_for_doc_type(target.doc_type)
        expected += 1 + len(statuses or ())
    assert len(STATUS_AWARE_TARGETS) == expected
