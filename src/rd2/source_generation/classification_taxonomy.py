"""템플릿과 독립된 원본문서 분류 taxonomy.

문서유형의 문자열 값은 수집 계층의 단일 진실 공급원인 ``storage.naming``을
재사용한다. 법적 세부조항은 기존 ``generators.template_matrix``에서 분리해 이
모듈이 직접 소유한다. 새 생성 경로가 기존 16종 템플릿 또는 규칙 기반
``infer_subclause_key``에 의존하지 않게 하는 것이 이 경계의 목적이다.
"""

from __future__ import annotations

from dataclasses import dataclass
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

TAXONOMY_VERSION = "source-generation-taxonomy-v2"


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

@dataclass(frozen=True)
class SubclauseDefinition:
    """모델이 세부조항을 판정할 때 쓰는 라벨·정의·포함·제외 기준.

    ``label``만으로는 enum 키의 번역에 그쳐 혼동 쌍을 구분할 수 없다.
    ``definition``은 판정 기준 한 문장이고, ``includes``는 이 세부조항을
    지지하는 전형적 근거, ``excludes``는 다른 세부조항이나 공개 대상으로
    보내야 하는 경우다.
    """

    label: str
    definition: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]


SUBCLAUSE_DEFINITIONS: Mapping[SubclauseKey, SubclauseDefinition] = MappingProxyType(
    {
        SubclauseKey.LEGAL_SECRET: SubclauseDefinition(
            label="법률상 비밀·비공개 규정",
            definition=(
                "다른 법률 또는 법률이 위임한 명령이 해당 정보를 비밀 또는 "
                "비공개로 규정한 근거가 문서에 드러나는 경우"
            ),
            includes=(
                "근거 법령의 조문을 명시한 비공개 처리",
                "법령상 비밀 취급 의무가 적시된 자료",
            ),
            excludes=(
                "'대외비'·'비밀유지' 표기만 있고 근거 법령이 없는 경우",
                "실제 보호 대상이 개인정보나 경영상 비밀이면 제6호 또는 제7호",
            ),
        ),
        SubclauseKey.SECURITY_DEFENSE: SubclauseDefinition(
            label="국가안전보장·국방",
            definition=(
                "군사·방위 관련 사항으로 공개 시 국가의 중대한 이익을 "
                "현저히 해칠 우려가 있는 정보"
            ),
            includes=(
                "부대·전력 배치와 작전계획",
                "군사시설 제원과 방호 취약점",
                "방위산업 소요와 전력 증강 계획",
            ),
            excludes=(
                "기관·기업 정보시스템의 취약점은 security_diagnosis",
                "이미 공표된 국방백서·예산 총액",
            ),
        ),
        SubclauseKey.UNIFICATION_DIPLOMACY: SubclauseDefinition(
            label="통일·외교관계",
            definition=(
                "통일정책·대외협상·재외공관 관련 사항으로 공개 시 국가의 "
                "중대한 이익을 현저히 해칠 우려가 있는 정보"
            ),
            includes=(
                "협상 전략과 상대국 입장 분석",
                "미공개 외교 전문과 접촉 경위",
                "남북 협의 진행 상황",
            ),
            excludes=(
                "이미 발표된 공동성명·협정문의 공개 내용",
                "일반 국제교류 행사 안내",
            ),
        ),
        SubclauseKey.LIFE_BODY: SubclauseDefinition(
            label="국민 생명·신체 보호",
            definition=(
                "공개 시 사람의 생명 또는 신체 안전 보호에 현저한 지장을 "
                "초래할 우려가 있는 정보"
            ),
            includes=(
                "위험물·감염병 시설의 구체적 위치와 방호 취약점",
                "보호 대상자·증인의 소재",
                "공개 시 모방 위험이 큰 사고 수법",
            ),
            excludes=(
                "이미 공지된 일반 안전수칙과 통계",
                "재산 피해가 핵심이면 property",
            ),
        ),
        SubclauseKey.PROPERTY: SubclauseDefinition(
            label="국민 재산 보호",
            definition=(
                "공개 시 국민의 재산 보호에 현저한 지장을 초래할 우려가 "
                "있는 정보"
            ),
            includes=(
                "판정 확정 전 재해위험 구조물 정보",
                "공개 시 재산 피해를 유발할 미공개 행정조치",
            ),
            excludes=(
                "투기·매점매석 유발이 핵심이면 제8호",
                "이미 고시된 안전등급과 공시 정보",
            ),
        ),
        SubclauseKey.TRIAL_INVESTIGATION: SubclauseDefinition(
            label="진행 중 재판·수사",
            definition=(
                "진행 중인 재판에 관련된 정보와 범죄 예방·수사에 관한 "
                "사항으로 공개 시 직무수행을 현저히 곤란하게 하는 정보"
            ),
            includes=(
                "수사 진행 상황과 내사 단서",
                "압수수색·체포 계획",
                "재판 계속 중 사건의 쟁점 검토 자료",
            ),
            excludes=(
                "확정 판결문의 공개된 주문과 이유",
                "공소 제기·유지 전략이 핵심이면 prosecution",
            ),
        ),
        SubclauseKey.PROSECUTION: SubclauseDefinition(
            label="공소 제기·유지",
            definition=(
                "공소 제기 여부 판단과 공소 유지 전략에 관한 사항으로 "
                "공개 시 형사절차의 공정을 해칠 우려가 있는 정보"
            ),
            includes=(
                "기소 의견과 법리 검토",
                "공판 대응 방침과 증인 신문 계획",
            ),
            excludes=(
                "이미 공표된 기소 사실과 죄명",
                "수사 착수 단계 자체는 trial_investigation",
            ),
        ),
        SubclauseKey.CORRECTION_SECURITY: SubclauseDefinition(
            label="형 집행·교정·보안처분",
            definition=(
                "형의 집행, 교정시설 운영, 보안처분에 관한 사항으로 공개 시 "
                "그 직무수행을 현저히 곤란하게 하는 정보"
            ),
            includes=(
                "수용자 처우·계호 계획",
                "교정시설 보안 체계와 경비 배치",
                "가석방 심사 내부자료",
            ),
            excludes=(
                "공표된 교정 통계와 일반 시설 안내",
                "수용자 개인의 사생활 정보가 핵심이면 subject_pii",
            ),
        ),
        SubclauseKey.AUDIT_INSPECTION: SubclauseDefinition(
            label="감사·검사",
            definition=(
                "감사·감독·검사·시험·규제의 수행 절차와 기준에 관한 "
                "사항으로 공개 시 업무의 공정한 수행에 지장을 주는 정보"
            ),
            includes=(
                "감사 계획과 표본 선정 기준",
                "확정 전 지적사항과 처분 의견",
                "검사·시험의 문항과 채점 기준",
            ),
            excludes=(
                "확정·공표된 감사 결과와 처분",
                "일반 정책 검토는 decision_review",
                "조사 대상 개인의 신원·진술이 핵심이면 subject_pii",
            ),
        ),
        SubclauseKey.BID_CONTRACT: SubclauseDefinition(
            label="입찰계약",
            definition=(
                "입찰·계약 절차의 공정한 수행과 직접 관련된 평가기준, 배점, "
                "예정가격, 협상 내용"
            ),
            includes=(
                "예정가격 산정 근거",
                "평가위원 구성과 배점표",
                "낙찰자 결정 전 협상 경과",
            ),
            excludes=(
                "이미 공고된 입찰공고문의 일반 조건",
                "계약 체결 후 공개 대상인 계약금액",
                "사업자의 원가·단가 자체가 보호 대상이면 unit_cost",
            ),
        ),
        SubclauseKey.PERSONNEL_MANAGEMENT: SubclauseDefinition(
            label="인사관리",
            definition=(
                "채용·시험·승진·전보 절차의 공정성과 직접 관련된 사항으로 "
                "공개 시 업무의 공정한 수행에 지장을 주는 정보"
            ),
            includes=(
                "출제·채점 기준과 면접위원 구성",
                "승진 심사 기준과 서열 자료",
                "확정 전 인사 이동 계획",
            ),
            excludes=(
                "식별 가능한 개인의 사생활 정보가 핵심이면 personnel_pii",
                "이미 공표된 채용 공고와 최종 합격자 발표",
            ),
        ),
        SubclauseKey.DECISION_REVIEW: SubclauseDefinition(
            label="의사결정·내부검토",
            definition=(
                "특정 전문업무에 속하지 않는 진행 중 정책결정, 회의, 결재 전 "
                "일반 내부검토"
            ),
            includes=(
                "확정 전 정책 대안 비교와 부서 검토 의견",
                "결재 전 초안 단계의 판단",
            ),
            excludes=(
                "핵심 업무가 감사면 audit_inspection",
                "핵심 업무가 입찰이면 bid_contract",
                "핵심 업무가 인사면 personnel_management",
            ),
        ),
        SubclauseKey.TECHNOLOGY_DEVELOPMENT: SubclauseDefinition(
            label="기술개발",
            definition=(
                "공공기관이 수행·관리하는 연구개발의 심사·평가 절차에 관한 "
                "사항으로 공개 시 연구·개발에 현저한 지장을 주는 정보"
            ),
            includes=(
                "과제 선정 평가표와 심사위원 의견",
                "확정 전 연구 중간 결과",
                "미공개 개발 로드맵",
            ),
            excludes=(
                "기업이 보유한 독점 기술·특허 비밀은 technology_patent",
                "공표된 과제 목록과 최종 보고서 공개분",
            ),
        ),
        SubclauseKey.PETITIONER_PII: SubclauseDefinition(
            label="민원인 개인정보",
            definition=(
                "민원·신고를 제기한 사람의 신원이 민원 내용과 결합되어 "
                "사생활의 비밀 또는 자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "민원인·신고자의 성명, 연락처, 주소",
                "신고 경위에 담긴 개인 사정",
            ),
            excludes=(
                "개인 식별이 불가능한 민원 처리 절차와 통계",
                "복지·급여 수급자의 자격 판정 자료는 welfare_pii",
            ),
        ),
        SubclauseKey.PERSONNEL_PII: SubclauseDefinition(
            label="인사·채용 개인정보",
            definition=(
                "소속 직원 또는 지원자 개인의 신상·평가·징계 정보로 공개 시 "
                "사생활의 비밀 또는 자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "개인별 근무평정과 징계 사유·처분 내역",
                "지원자 이력과 개인별 시험 점수",
            ),
            excludes=(
                "절차의 공정성이 핵심이고 개인 식별이 부수적이면 "
                "personnel_management",
                "직위·부서 등 직무상 공개되는 정보",
            ),
        ),
        SubclauseKey.WELFARE_PII: SubclauseDefinition(
            label="복지·급여 수급자 개인정보",
            definition=(
                "복지·급여·지원 수급 자격 판정에 쓰인 개인의 사정으로 공개 시 "
                "사생활의 비밀 또는 자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "수급자의 소득·재산·건강 상태",
                "가구 구성과 부양 관계",
                "지원 사유에 기재된 개인 사정",
            ),
            excludes=(
                "민원을 제기한 사람의 신원 보호가 핵심이면 petitioner_pii",
                "개인 식별이 불가능한 수급 현황 통계",
            ),
        ),
        SubclauseKey.SUBJECT_PII: SubclauseDefinition(
            label="조사대상자 개인정보",
            definition=(
                "조사·점검·심의 대상이 된 개인의 신원과 조사 내용으로 공개 시 "
                "사생활의 비밀 또는 자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "조사대상자의 성명과 진술 내용",
                "위반 혐의 사실과 심의 대상자 신상",
            ),
            excludes=(
                "조사·감사의 절차와 기준이 핵심이면 audit_inspection",
                "개인 식별이 불가능한 적발 통계",
            ),
        ),
        SubclauseKey.TECHNOLOGY_PATENT: SubclauseDefinition(
            label="기술·특허 비밀",
            definition=(
                "법인·단체·개인이 보유한 독점 기술과 출원 전 특허 정보로 "
                "공개 시 정당한 이익을 현저히 해칠 우려가 있는 정보"
            ),
            includes=(
                "미공개 공정·설계 자료",
                "출원 전 발명 내용",
                "기술이전 대상 노하우",
            ),
            excludes=(
                "공공기관의 연구개발 심사·평가 절차는 technology_development",
                "이미 공개된 등록특허의 명세서",
            ),
        ),
        SubclauseKey.MA_TERMS: SubclauseDefinition(
            label="M&A·협상조건",
            definition=(
                "인수·합병·출자·제휴 협상의 조건과 진행 상황으로 공개 시 "
                "법인등의 정당한 이익을 현저히 해칠 우려가 있는 정보"
            ),
            includes=(
                "인수가격 산정과 협상 상대·조건",
                "실사 결과와 미공개 우발채무",
            ),
            excludes=(
                "공시된 계약 체결 사실과 공개 조건",
                "공공 입찰 절차의 협상이면 bid_contract",
            ),
        ),
        SubclauseKey.SECURITY_DIAGNOSIS: SubclauseDefinition(
            label="보안진단·취약점",
            definition=(
                "기관·기업 정보시스템의 취약점과 보안점검 결과로 공개 시 "
                "침해 위험을 높이는 정보"
            ),
            includes=(
                "모의해킹 결과와 미조치 취약점 목록",
                "보안장비 구성도와 네트워크 경로",
            ),
            excludes=(
                "국가안보·국방 작전 정보는 security_defense",
                "일반 보안 정책 안내와 교육 자료",
            ),
        ),
        SubclauseKey.UNIT_COST: SubclauseDefinition(
            label="원가·납품단가",
            definition=(
                "원가 구성과 납품단가 등 공개 시 사업자의 거래상 지위와 "
                "정당한 이익을 현저히 해칠 우려가 있는 정보"
            ),
            includes=(
                "품목별 원가 명세와 마진",
                "납품단가와 하도급 대금 구조",
            ),
            excludes=(
                "입찰 절차의 예정가격이 핵심이면 bid_contract",
                "공표된 표준단가와 공시 가격",
            ),
        ),
        SubclauseKey.BUSINESS_STRATEGY: SubclauseDefinition(
            label="경영전략",
            definition=(
                "법인·단체의 미공개 경영 계획과 영업 전략으로 공개 시 "
                "정당한 이익을 현저히 해칠 우려가 있는 정보"
            ),
            includes=(
                "사업 철수·확장 계획과 미공개 재무 전망",
                "거래처 관리 전략과 가격 정책",
            ),
            excludes=(
                "공시된 경영 실적과 사업보고서 공개분",
                "인수·합병 협상 조건이면 ma_terms",
            ),
        ),
        SubclauseKey.REAL_ESTATE_SPECULATION: SubclauseDefinition(
            label="부동산 투기",
            definition=(
                "공개 시 부동산 투기를 유발해 특정인에게 이익 또는 불이익을 "
                "줄 우려가 있는 정보"
            ),
            includes=(
                "확정 전 개발계획과 구역 지정 검토",
                "보상 기준과 매입 예정지",
            ),
            excludes=(
                "이미 고시된 개발계획과 공시지가",
                "물자 수급 조절이면 cornering",
            ),
        ),
        SubclauseKey.CORNERING: SubclauseDefinition(
            label="매점매석",
            definition=(
                "공개 시 물자의 매점매석을 유발해 특정인에게 이익 또는 "
                "불이익을 줄 우려가 있는 정보"
            ),
            includes=(
                "비축물자 방출 시기와 물량",
                "수급 조절 계획과 조달 예정 물량",
            ),
            excludes=(
                "부동산 관련이면 real_estate_speculation",
                "이미 공표된 비축 현황 통계",
            ),
        ),
    }
)

if set(SUBCLAUSE_DEFINITIONS) != set(SubclauseKey):
    raise RuntimeError("every subclause requires a definition")

SUBCLAUSE_LABELS: Mapping[SubclauseKey, str] = MappingProxyType(
    {key: definition.label for key, definition in SUBCLAUSE_DEFINITIONS.items()}
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
    "민원·신고를 제기한 사람의 신원 보호가 핵심이면 petitioner_pii, 복지·급여 "
    "수급 자격 판정에 쓰인 개인 사정이면 welfare_pii를 선택한다. 두 세부조항 "
    "모두 민원 업무에서 나올 수 있으므로 '민원'이라는 낱말로 구분하지 않는다.",
    "입찰·계약 절차의 공정성이 핵심이면 bid_contract, 사업자의 원가 구성과 "
    "납품단가 자체가 보호 대상이면 unit_cost를 선택한다.",
    "조사 대상이 된 개인의 신원과 진술이 핵심이면 subject_pii, 조사·감사의 "
    "절차와 기준이 핵심이면 audit_inspection을 선택한다.",
    "'비밀', '대외비', 비밀유지협약이라는 표현만으로 legal_secret을 선택하지 "
    "않는다. 제1호는 별도 법률이 비밀·비공개를 명시한 근거가 문서에 있을 때만 "
    "적용하고, 그렇지 않으면 실제 보호 대상에 맞는 세부조항을 선택한다.",
)


BOUNDARY_PAIRS: tuple[tuple[SubclauseKey, SubclauseKey], ...] = (
    (SubclauseKey.BID_CONTRACT, SubclauseKey.DECISION_REVIEW),
    (SubclauseKey.AUDIT_INSPECTION, SubclauseKey.DECISION_REVIEW),
    (SubclauseKey.PERSONNEL_MANAGEMENT, SubclauseKey.PERSONNEL_PII),
    (SubclauseKey.TECHNOLOGY_DEVELOPMENT, SubclauseKey.TECHNOLOGY_PATENT),
    (SubclauseKey.SECURITY_DEFENSE, SubclauseKey.SECURITY_DIAGNOSIS),
    (SubclauseKey.PETITIONER_PII, SubclauseKey.WELFARE_PII),
    (SubclauseKey.BID_CONTRACT, SubclauseKey.UNIT_COST),
    (SubclauseKey.SUBJECT_PII, SubclauseKey.AUDIT_INSPECTION),
)
"""프롬프트 경계 규칙과 held-out 평가가 **같은 목록**을 보게 하는 단일 출처.

설계 초기에는 프롬프트가 안내할 혼동 쌍과 평가가 측정할 혼동 쌍을 각각 손으로
관리했고, 그 결과 `petitioner_pii`/`welfare_pii`가 양쪽에서 동시에 누락됐다 —
가장 헷갈리는 쌍이 가이드도 측정도 없이 남은 것이다. 새 혼동 쌍은 여기에만
추가하고, 경계 규칙과 평가 리포트가 이 목록을 함께 따른다.
"""


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
            definition = SUBCLAUSE_DEFINITIONS[subclause]
            lines.append(
                f"- {subclause.value} ({definition.label}): {definition.definition}"
            )
            lines.append(f"  포함: {' / '.join(definition.includes)}")
            lines.append(f"  제외: {' / '.join(definition.excludes)}")
    lines.append("")
    lines.append("[세부조항 경계 규칙]")
    lines.extend(f"- {rule}" for rule in SUBCLAUSE_BOUNDARY_RULES)
    return "\n".join(lines)
