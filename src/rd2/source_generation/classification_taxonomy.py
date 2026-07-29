"""템플릿과 독립된 원본문서 분류 taxonomy.

문서유형의 문자열 값은 수집 계층의 단일 진실 공급원인 ``storage.naming``을
재사용한다. 법적 세부조항은 기존 ``generators.template_matrix``에서 분리해 이
모듈이 직접 소유한다. 새 생성 경로가 기존 16종 템플릿 또는 규칙 기반
``infer_subclause_key``에 의존하지 않게 하는 것이 이 경계의 목적이다.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping

from rd2.schema.models import CsoClassification
from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_BID_RENOTICE,
    DOC_TYPE_BUDGET_EXECUTION,
    DOC_TYPE_BUDGET_MATERIAL,
    DOC_TYPE_BUSINESS_TRIP,
    DOC_TYPE_DIRECTIVE,
    DOC_TYPE_DIRECTOR_ACTIVITY,
    DOC_TYPE_GUIDE,
    DOC_TYPE_INTERPRETATION_COMPILATION,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_NOTICE,
    DOC_TYPE_NOTIFICATION,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_PRE_SPEC_NOTICE,
    DOC_TYPE_PRESS_RELEASE,
    DOC_TYPE_PUBLIC_OFFERING,
    DOC_TYPE_REGULATION,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
    DOC_TYPE_RESEARCH_REPORT,
    DOC_TYPE_STATUS_REPORT,
)

TAXONOMY_VERSION = "source-generation-taxonomy-v1"


class SemanticDocumentType(str, Enum):
    """실제 source 문서유형 26개와 의미적 폴백 ``other``."""

    RESEARCH_REPORT = DOC_TYPE_RESEARCH_REPORT
    BID_NOTICE = DOC_TYPE_BID_NOTICE
    PRE_SPEC_NOTICE = DOC_TYPE_PRE_SPEC_NOTICE
    BID_RENOTICE = DOC_TYPE_BID_RENOTICE
    PUBLIC_OFFERING = DOC_TYPE_PUBLIC_OFFERING
    NOTICE = DOC_TYPE_NOTICE
    OFFICIAL_DOCUMENT = DOC_TYPE_OFFICIAL_DOCUMENT
    POLICY_MATERIAL = DOC_TYPE_POLICY_MATERIAL
    MEETING_MINUTES = DOC_TYPE_MEETING_MINUTES
    BUDGET_MATERIAL = DOC_TYPE_BUDGET_MATERIAL
    AUDIT_RESULT = DOC_TYPE_AUDIT_RESULT
    DIRECTOR_ACTIVITY = DOC_TYPE_DIRECTOR_ACTIVITY
    REPORT = DOC_TYPE_REPORT
    PERSONNEL = DOC_TYPE_PERSONNEL
    APPROVAL = DOC_TYPE_APPROVAL
    REPLY_NOTIFICATION = DOC_TYPE_REPLY_NOTIFICATION
    BUDGET_EXECUTION = DOC_TYPE_BUDGET_EXECUTION
    PLAN = DOC_TYPE_PLAN
    BUSINESS_TRIP = DOC_TYPE_BUSINESS_TRIP
    PRESS_RELEASE = DOC_TYPE_PRESS_RELEASE
    NOTIFICATION = DOC_TYPE_NOTIFICATION
    DIRECTIVE = DOC_TYPE_DIRECTIVE
    REGULATION = DOC_TYPE_REGULATION
    STATUS_REPORT = DOC_TYPE_STATUS_REPORT
    GUIDE = DOC_TYPE_GUIDE
    INTERPRETATION_COMPILATION = DOC_TYPE_INTERPRETATION_COMPILATION
    OTHER = "other"


class ClauseNumber(str, Enum):
    CLAUSE_1 = "1"
    CLAUSE_2 = "2"
    CLAUSE_3 = "3"
    CLAUSE_4 = "4"
    CLAUSE_5 = "5"
    CLAUSE_6 = "6"
    CLAUSE_7 = "7"
    CLAUSE_8 = "8"


class SubclauseKey(str, Enum):
    LEGAL_SECRET = "legal_secret"
    SECURITY_DEFENSE = "security_defense"
    UNIFICATION_DIPLOMACY = "unification_diplomacy"
    LIFE_BODY = "life_body"
    PROPERTY = "property"
    TRIAL_INVESTIGATION = "trial_investigation"
    PROSECUTION = "prosecution"
    CORRECTION_SECURITY = "correction_security"
    AUDIT_INSPECTION = "audit_inspection"
    BID_CONTRACT = "bid_contract"
    PERSONNEL_MANAGEMENT = "personnel_management"
    DECISION_REVIEW = "decision_review"
    TECHNOLOGY_DEVELOPMENT = "technology_development"
    PETITIONER_PII = "petitioner_pii"
    PERSONNEL_PII = "personnel_pii"
    WELFARE_PII = "welfare_pii"
    SUBJECT_PII = "subject_pii"
    TECHNOLOGY_PATENT = "technology_patent"
    MA_TERMS = "ma_terms"
    SECURITY_DIAGNOSIS = "security_diagnosis"
    UNIT_COST = "unit_cost"
    BUSINESS_STRATEGY = "business_strategy"
    REAL_ESTATE_SPECULATION = "real_estate_speculation"
    CORNERING = "cornering"


SUBCLAUSES_BY_CLAUSE: Mapping[ClauseNumber, frozenset[SubclauseKey]] = MappingProxyType(
    {
        ClauseNumber.CLAUSE_1: frozenset({SubclauseKey.LEGAL_SECRET}),
        ClauseNumber.CLAUSE_2: frozenset(
            {
                SubclauseKey.SECURITY_DEFENSE,
                SubclauseKey.UNIFICATION_DIPLOMACY,
            }
        ),
        ClauseNumber.CLAUSE_3: frozenset(
            {
                SubclauseKey.LIFE_BODY,
                SubclauseKey.PROPERTY,
            }
        ),
        ClauseNumber.CLAUSE_4: frozenset(
            {
                SubclauseKey.TRIAL_INVESTIGATION,
                SubclauseKey.PROSECUTION,
                SubclauseKey.CORRECTION_SECURITY,
            }
        ),
        ClauseNumber.CLAUSE_5: frozenset(
            {
                SubclauseKey.AUDIT_INSPECTION,
                SubclauseKey.BID_CONTRACT,
                SubclauseKey.PERSONNEL_MANAGEMENT,
                SubclauseKey.DECISION_REVIEW,
                SubclauseKey.TECHNOLOGY_DEVELOPMENT,
            }
        ),
        ClauseNumber.CLAUSE_6: frozenset(
            {
                SubclauseKey.PETITIONER_PII,
                SubclauseKey.PERSONNEL_PII,
                SubclauseKey.WELFARE_PII,
                SubclauseKey.SUBJECT_PII,
            }
        ),
        ClauseNumber.CLAUSE_7: frozenset(
            {
                SubclauseKey.TECHNOLOGY_PATENT,
                SubclauseKey.MA_TERMS,
                SubclauseKey.SECURITY_DIAGNOSIS,
                SubclauseKey.UNIT_COST,
                SubclauseKey.BUSINESS_STRATEGY,
            }
        ),
        ClauseNumber.CLAUSE_8: frozenset(
            {
                SubclauseKey.REAL_ESTATE_SPECULATION,
                SubclauseKey.CORNERING,
            }
        ),
    }
)

SUBCLAUSE_LABELS: Mapping[SubclauseKey, str] = MappingProxyType(
    {
        SubclauseKey.LEGAL_SECRET: "법률상 비밀·비공개 규정",
        SubclauseKey.SECURITY_DEFENSE: "국가안전보장·국방",
        SubclauseKey.UNIFICATION_DIPLOMACY: "통일·외교관계",
        SubclauseKey.LIFE_BODY: "국민 생명·신체 보호",
        SubclauseKey.PROPERTY: "국민 재산 보호",
        SubclauseKey.TRIAL_INVESTIGATION: "진행 중 재판·수사",
        SubclauseKey.PROSECUTION: "공소 제기·유지",
        SubclauseKey.CORRECTION_SECURITY: "형 집행·교정·보안처분",
        SubclauseKey.AUDIT_INSPECTION: "감사·검사",
        SubclauseKey.BID_CONTRACT: "입찰계약",
        SubclauseKey.PERSONNEL_MANAGEMENT: "인사관리",
        SubclauseKey.DECISION_REVIEW: "의사결정·내부검토",
        SubclauseKey.TECHNOLOGY_DEVELOPMENT: "기술개발",
        SubclauseKey.PETITIONER_PII: "민원인 개인정보",
        SubclauseKey.PERSONNEL_PII: "인사·채용 개인정보",
        SubclauseKey.WELFARE_PII: "복지·민원 개인정보",
        SubclauseKey.SUBJECT_PII: "조사대상자 개인정보",
        SubclauseKey.TECHNOLOGY_PATENT: "기술·특허 비밀",
        SubclauseKey.MA_TERMS: "M&A·협상조건",
        SubclauseKey.SECURITY_DIAGNOSIS: "보안진단·취약점",
        SubclauseKey.UNIT_COST: "원가·납품단가",
        SubclauseKey.BUSINESS_STRATEGY: "경영전략",
        SubclauseKey.REAL_ESTATE_SPECULATION: "부동산 투기",
        SubclauseKey.CORNERING: "매점매석",
    }
)


def expected_classification(clause_no: ClauseNumber) -> CsoClassification:
    """정보공개법 조항 번호에 대응하는 프로젝트 C/S 분류를 반환한다."""

    if clause_no in {
        ClauseNumber.CLAUSE_1,
        ClauseNumber.CLAUSE_2,
        ClauseNumber.CLAUSE_3,
        ClauseNumber.CLAUSE_4,
    }:
        return CsoClassification.C
    return CsoClassification.S


def subclause_belongs_to_clause(
    clause_no: ClauseNumber,
    subclause_key: SubclauseKey,
) -> bool:
    return subclause_key in SUBCLAUSES_BY_CLAUSE[clause_no]


SUBCLAUSE_BOUNDARY_RULES: tuple[str, ...] = (
    "문서에 '내부 검토'가 있다는 이유만으로 decision_review를 선택하지 않는다. "
    "입찰 평가기준·배점·예정가격·협상 내용이면 bid_contract를 우선한다.",
    "감사·검사의 수행 절차와 기준이면 audit_inspection, 일반 정책·결재 전 검토면 "
    "decision_review를 선택한다.",
    "채용·시험·승진 절차의 공정성이 핵심이면 personnel_management, 식별 가능한 "
    "개인의 사생활 정보 보호가 핵심이면 personnel_pii를 선택한다.",
    "공공기관의 연구개발 심사·평가 절차면 technology_development, 기업의 독점 "
    "기술·특허 비밀이면 technology_patent를 선택한다.",
    "국가안보·국방 작전 정보면 security_defense, 기관·기업 시스템의 취약점과 "
    "보안점검 결과면 security_diagnosis를 선택한다.",
)


def render_taxonomy_guidance() -> str:
    """P1/P2가 같은 조항·세부조항 의미를 보도록 결정론적으로 렌더링한다."""

    lines = ["[정보공개법 제9조 분류 taxonomy]"]
    for clause_no in ClauseNumber:
        classification = expected_classification(clause_no).value
        lines.append(f"제{clause_no.value}호 ({classification})")
        for subclause in sorted(
            SUBCLAUSES_BY_CLAUSE[clause_no],
            key=lambda item: item.value,
        ):
            lines.append(f"- {subclause.value}: {SUBCLAUSE_LABELS[subclause]}")
    lines.append("")
    lines.append("[세부조항 경계 규칙]")
    lines.extend(f"- {rule}" for rule in SUBCLAUSE_BOUNDARY_RULES)
    return "\n".join(lines)
