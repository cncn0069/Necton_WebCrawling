"""제9조 세부조항×문서유형별 전용 템플릿 목표 매트릭스."""

from __future__ import annotations

from dataclasses import dataclass

from rd2.augmentation.candidates import ADMIN_STATUS_RULES_BY_DOC_TYPE


@dataclass(frozen=True)
class TemplateTarget:
    clause_no: str
    subclause_key: str
    subclause_label: str
    doc_type: str
    template_id: str
    expected_documents: int = 6


_GROUPS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    "1": (("legal_secret", "법률상 비밀·비공개 규정", ("official_document", "policy_material", "report", "meeting_minutes")),),
    "2": (
        ("security_defense", "국가안전보장·국방", ("policy_material", "official_document", "press_release")),
        ("unification_diplomacy", "통일·외교관계", ("report", "meeting_minutes", "plan")),
    ),
    "3": (
        ("life_body", "국민 생명·신체 보호", ("report", "official_document", "policy_material", "meeting_minutes", "press_release")),
        ("property", "국민 재산 보호", ("report", "official_document", "plan")),
    ),
    "4": (
        ("trial_investigation", "진행 중 재판·수사", ("meeting_minutes", "official_document", "report")),
        ("prosecution", "공소 제기·유지", ("approval", "reply_notification")),
        ("correction_security", "형 집행·교정·보안처분", ("plan", "report")),
    ),
    "5": (
        ("audit_inspection", "감사·검사", ("audit_result", "meeting_minutes", "official_document", "report")),
        ("bid_contract", "입찰계약", ("bid_notice", "approval", "bid_renotice", "public_offering", "pre_spec_notice")),
        ("personnel_management", "인사관리", ("personnel",)),
        ("decision_review", "의사결정·내부검토", ("approval", "meeting_minutes", "press_release", "notice", "interpretation_compilation")),
        ("technology_development", "기술개발", ("report",)),
    ),
    "6": (
        ("petitioner_pii", "민원인 개인정보", ("reply_notification",)),
        ("personnel_pii", "인사·채용 개인정보", ("personnel", "report")),
        ("welfare_pii", "복지·민원 개인정보", ("official_document", "approval")),
        ("subject_pii", "조사대상자 개인정보", ("meeting_minutes",)),
    ),
    "7": (
        ("technology_patent", "기술·특허 비밀", ("report", "official_document")),
        ("ma_terms", "M&A·협상조건", ("approval", "meeting_minutes")),
        ("security_diagnosis", "보안진단·취약점", ("report",)),
        ("unit_cost", "원가·납품단가", ("bid_notice",)),
        ("business_strategy", "경영전략", ("policy_material",)),
    ),
    "8": (
        ("real_estate_speculation", "부동산 투기", ("plan", "policy_material", "report", "meeting_minutes")),
        ("cornering", "매점매석", ("official_document", "report", "approval")),
    ),
}

_EXISTING_IDS = {
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


def _build_targets() -> tuple[TemplateTarget, ...]:
    targets: list[TemplateTarget] = []
    for clause_no, groups in _GROUPS.items():
        reserved = {int(v.split("-")[1]) for k, v in _EXISTING_IDS.items() if k[0] == clause_no}
        next_no = 1
        for subclause_key, label, doc_types in groups:
            for doc_type in doc_types:
                key = (clause_no, subclause_key, doc_type)
                template_id = _EXISTING_IDS.get(key)
                if template_id is None:
                    while next_no in reserved:
                        next_no += 1
                    template_id = f"T{clause_no}-{next_no}"
                    reserved.add(next_no)
                    next_no += 1
                targets.append(TemplateTarget(clause_no, subclause_key, label, doc_type, template_id))
    return tuple(targets)


TEMPLATE_TARGETS = _build_targets()
TARGET_BY_KEY = {(t.clause_no, t.subclause_key, t.doc_type): t for t in TEMPLATE_TARGETS}


@dataclass(frozen=True)
class TemplateStatusTarget:
    """(조항, 세부조항, 문서유형, 행정상태) 셀 하나.

    기존 ``TemplateTarget``/``TARGET_BY_KEY``는 그대로 두고(하위호환), 이 타입은
    거기에 행정상태 축을 "additive"하게 덧붙인 뷰다. 문서유형마다 적용 가능한
    행정상태 목록이 다르므로(``ADMIN_STATUS_RULES_BY_DOC_TYPE`` 참고), 모든
    문서유형에 모든 상태를 곱하는 완전 3중 격자가 아니라 그 문서유형에 실제
    등록된 상태 목록만 곱한 조건부 크로스다.

    ``admin_status``가 ``None``인 행은 행정상태를 부여하지 않는 기본 생성 셀이다.
    모든 문서유형에 이 기본 셀을 하나씩 두고, 적용 가능한 행정상태 셀은 낮은
    비율의 별도 표본으로 추가한다.
    """

    clause_no: str
    subclause_key: str
    subclause_label: str
    doc_type: str
    template_id: str
    admin_status: str | None


def admin_statuses_for_doc_type(doc_type: str) -> tuple[str, ...] | None:
    """문서유형 하나에 등록된 행정상태 라벨 목록을 조회한다.

    반환값 3가지를 구분해서 쓴다(Lane A 측정에서 "규칙 자체가 없음"과 "규칙은
    있는데 매치 0건"을 섞으면 안 되기 때문 — design doc 참고):
      - ``None``: ``ADMIN_STATUS_RULES_BY_DOC_TYPE``에 이 doc_type 항목 자체가
        없음(규칙 미정의, Lane A 표기로 ``no_rule_defined``).
      - ``()``  : 규칙 항목은 있으나 상태 라벨이 0개(현재 데이터에서는 발생하지
        않지만, "규칙 있음 + 매치 0건"을 표현할 수 있어야 한다).
      - ``(label, ...)``: 규칙이 등록돼 있고 상태 라벨이 1개 이상 있음.
    """
    rules = ADMIN_STATUS_RULES_BY_DOC_TYPE.get(doc_type)
    if rules is None:
        return None
    return tuple(rule.document_status for rule in rules)


def _build_status_aware_targets() -> tuple[TemplateStatusTarget, ...]:
    rows: list[TemplateStatusTarget] = []
    for target in TEMPLATE_TARGETS:
        # 행정상태는 예외적인 보조 축이다. 규칙이 있는 문서유형도 대부분의
        # 생성물이 상태 없는 일반 문서가 되도록 기본 셀을 항상 보존한다.
        rows.append(
            TemplateStatusTarget(
                target.clause_no,
                target.subclause_key,
                target.subclause_label,
                target.doc_type,
                target.template_id,
                admin_status=None,
            )
        )
        statuses = admin_statuses_for_doc_type(target.doc_type)
        if not statuses:
            continue
        for status in statuses:
            rows.append(
                TemplateStatusTarget(
                    target.clause_no,
                    target.subclause_key,
                    target.subclause_label,
                    target.doc_type,
                    target.template_id,
                    admin_status=status,
                )
            )
    return tuple(rows)


STATUS_AWARE_TARGETS = _build_status_aware_targets()

# (clause_no, subclause_key, doc_type) -> admin_statuses_for_doc_type(doc_type)와 동일.
# 매번 ADMIN_STATUS_RULES_BY_DOC_TYPE를 다시 조회하지 않도록 미리 계산해둔 캐시.
TARGET_STATUSES_BY_KEY: dict[tuple[str, str, str], tuple[str, ...] | None] = {
    (t.clause_no, t.subclause_key, t.doc_type): admin_statuses_for_doc_type(t.doc_type)
    for t in TEMPLATE_TARGETS
}

_SUBCLAUSE_KEYWORDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "2": (("unification_diplomacy", ("통일", "외교", "남북", "재외공관")),),
    "3": (("property", ("재산", "재해", "손실", "보험", "보상")),),
    "4": (
        ("prosecution", ("공소", "기소", "공판")),
        ("correction_security", ("형 집행", "교정", "수용자", "보안처분")),
    ),
    "5": (
        ("audit_inspection", ("감사", "검사", "점검")),
        ("bid_contract", ("입찰", "계약", "낙찰", "단가")),
        ("personnel_management", ("인사", "승진", "징계", "채용")),
        ("technology_development", ("기술개발", "연구개발", "R&D")),
    ),
    "6": (
        ("petitioner_pii", ("민원", "신청인", "청구인")),
        ("welfare_pii", ("복지", "수급", "급여", "지원대상")),
        ("subject_pii", ("조사대상", "피조사", "사건관계인")),
    ),
    "7": (
        ("ma_terms", ("M&A", "인수합병", "협상", "매각")),
        ("security_diagnosis", ("보안진단", "취약점", "침해", "모의해킹")),
        ("unit_cost", ("원가", "단가", "납품", "낙찰가")),
        ("business_strategy", ("경영전략", "사업전략", "시장점유")),
    ),
    "8": (("cornering", ("매점매석", "사재기", "물량 확보", "가격 담합")),),
}

_DEFAULT_SUBCLAUSE = {
    "1": "legal_secret",
    "2": "security_defense",
    "3": "life_body",
    "4": "trial_investigation",
    "5": "decision_review",
    "6": "personnel_pii",
    "7": "technology_patent",
    "8": "real_estate_speculation",
}


def infer_subclause_key(clause_no: str, doc_type: str, *, keyword_text: str = "") -> str | None:
    """조항·문서유형과 업무 키워드로 목표 매트릭스의 세부조항을 결정한다."""
    clause = str(clause_no).strip()
    candidates = [
        target for target in TEMPLATE_TARGETS
        if target.clause_no == clause and target.doc_type == doc_type
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].subclause_key

    candidate_keys = {target.subclause_key for target in candidates}
    folded_text = keyword_text.casefold()
    for subclause_key, keywords in _SUBCLAUSE_KEYWORDS.get(clause, ()):
        if subclause_key in candidate_keys and any(keyword.casefold() in folded_text for keyword in keywords):
            return subclause_key

    default = _DEFAULT_SUBCLAUSE.get(clause)
    if default in candidate_keys:
        return default
    return candidates[0].subclause_key
