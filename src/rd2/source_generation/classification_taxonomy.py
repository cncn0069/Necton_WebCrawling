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
                "소속 직원 또는 지원자의 고위험 신상정보와 지원자의 개인 사정으로 "
                "공개 시 사생활의 비밀 또는 자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "직원의 주민등록번호·개인 연락처·주소·계좌·급여·건강정보",
                "지원자의 개인 연락처·주소·이력과 개인별 시험 점수",
            ),
            excludes=(
                "절차의 공정성이 핵심이고 개인 식별이 부수적이면 "
                "personnel_management",
                "직위·부서 등 직무상 공개되는 정보",
                "직원의 근무평정과 징계 사유·처분 내역은 이 연구 라벨에서 O",
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


def clause_of_subclause(subclause_key: SubclauseKey) -> ClauseNumber:
    """세부조항이 속한 조항을 돌려준다.

    ``SUBCLAUSES_BY_CLAUSE``가 이미 단일 출처이므로 역방향을 손으로 또
    적지 않는다 — 두 목록이 어긋나면 조용히 틀린 조항이 붙는다.
    """

    for clause, subclauses in SUBCLAUSES_BY_CLAUSE.items():
        if subclause_key in subclauses:
            return clause
    raise KeyError(f"subclause {subclause_key.value!r} belongs to no clause")


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


def render_taxonomy_guidance(
    clause_numbers: tuple[ClauseNumber, ...] | None = None,
) -> str:
    """classifier와 validator가 같은 세부조항 의미를 보도록 렌더링한다.

    각 세부유형 이름 뒤에 ``[제N호]``를 항상 붙인다. 실측 회귀: validator가
    ``classification=S``와 ``subclause_key=business_strategy``(제7호 소속)는
    맞혔는데 ``clause_no=5``를 내 계약 검증(``subclause_belongs_to_clause``)에서
    응답 전체가 버려졌다. `제5호 (S)` 같은 절 머리는 한 번만 나오고 목록이 길어
    개별 세부유형을 읽을 때는 잊히기 쉽다 — 항목마다 소속 호를 반복하면 그 줄만
    보고도 조합을 맞힐 수 있다.
    """

    selected_clauses = clause_numbers or tuple(ClauseNumber)
    selected_subclauses = {
        subclause
        for clause_no in selected_clauses
        for subclause in SUBCLAUSES_BY_CLAUSE[clause_no]
    }
    excluded_subclauses = set(SubclauseKey) - selected_subclauses
    lines = ["[정보공개법 제9조 분류 taxonomy]"]
    for clause_no in selected_clauses:
        classification = expected_classification(clause_no).value
        lines.append(f"제{clause_no.value}호 ({classification})")
        for subclause in sorted(
            SUBCLAUSES_BY_CLAUSE[clause_no],
            key=lambda item: item.value,
        ):
            definition = SUBCLAUSE_DEFINITIONS[subclause]
            lines.append(
                f"- {subclause.value} [제{clause_no.value}호] ({definition.label}): "
                f"{definition.definition}"
            )
            lines.append(f"  포함: {' / '.join(definition.includes)}")
            lines.append(f"  제외: {' / '.join(definition.excludes)}")
    lines.append("")
    lines.append("[세부조항 경계 규칙]")
    lines.extend(
        f"- {rule}"
        for rule in SUBCLAUSE_BOUNDARY_RULES
        if not any(key.value in rule for key in excluded_subclauses)
    )
    return "\n".join(lines)


class DocumentForm(str, Enum):
    """본문만 보고 구분할 수 있는 **문서 형식**. 채점 축은 이것이다.

    ``SemanticDocumentType`` 26개는 수집 휴리스틱의 산물이다 — 출처별 기본값과
    제목 키워드로 붙였고, ``notice``는 "키워드 미매칭 시 최종 폴백"이며
    ``press_release``는 한 출처 전용이다. 본문만 보고 그걸 맞히라는 것은 문서
    형식이 아니라 **출처를 맞히라는 요구**라 채점이 성립하지 않는다.

    실측이 이를 보여줬다 — P2는 생성물 37건 중 35건을 ``other``로 판정했고,
    구체적 유형을 낸 경우에도 "국가안전보장 및 국방 규정"처럼 **형식이 아니라
    주제**를 적었다.

    아래 목록은 실무에서 실제로 쓰이는 문서 형식명을 받아 정리한 것이다.
    수집 라벨 26개는 버리지 않고 provenance 메타데이터로 계속 보존한다 —
    역할을 나누는 것이지 폐기하는 것이 아니다.
    """

    MEETING_MINUTES = "meeting_minutes"
    OFFICIAL_LETTER = "official_letter"
    REPORT = "report"
    AUDIT_MATERIAL = "audit_material"
    PERSONNEL_MATERIAL = "personnel_material"
    BID_MATERIAL = "bid_material"
    APPROVAL_REQUEST = "approval_request"
    REPLY_NOTICE = "reply_notice"
    POLICY_MATERIAL = "policy_material"
    PLAN_DRAFT = "plan_draft"
    LEGAL_REVIEW = "legal_review"
    INSPECTION_REPORT = "inspection_report"
    RESPONSE_PLAN = "response_plan"
    INVESTIGATION_REPORT = "investigation_report"
    PRESS_RELEASE = "press_release"
    ADMINISTRATIVE_RULE = "administrative_rule"
    OTHER = "other"


@dataclass(frozen=True)
class DocumentFormDefinition:
    """세부조항과 같은 처방 — 이름만 던지면 모델은 형식을 구분하지 못한다."""

    label: str
    definition: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]


DOCUMENT_FORM_DEFINITIONS: Mapping[DocumentForm, DocumentFormDefinition] = MappingProxyType(
    {
        DocumentForm.MEETING_MINUTES: DocumentFormDefinition(
            label="회의록",
            definition="회의 진행과 참석자의 발언·의결을 시간 순으로 기록한 문서",
            includes=(
                "회차, 개최일시, 장소, 참석자 명단",
                "안건별 논의 내용과 의결 사항",
            ),
            excludes=(
                "회의 결론만 정리해 상급자에게 보고하면 report",
                "심의 결과를 상대에게 알리면 reply_notice",
            ),
        ),
        DocumentForm.OFFICIAL_LETTER: DocumentFormDefinition(
            label="공문",
            definition="수신처를 특정해 사안을 알리거나 협조를 요청하는 일반 시행문",
            includes=(
                "수신·경유가 명시된 협조 요청, 자료 제출 요구",
                "제목-본문-붙임의 표준 시행문 구성",
            ),
            excludes=(
                "받은 문서에 대한 답변이면 reply_notice",
                "불특정 다수에게 참여를 구하면 bid_material 또는 policy_material",
            ),
        ),
        DocumentForm.REPORT: DocumentFormDefinition(
            label="보고서",
            definition="조사·연구·업무 수행 결과를 정리해 보고하는 일반 보고 문서",
            includes=(
                "조사 범위와 방법, 결과, 결론·건의",
                "연구보고서, 결과보고, 현황·통계 보고",
            ),
            excludes=(
                "감사·검사 수행이면 audit_material",
                "시설·시스템 점검이면 inspection_report",
                "범죄 수사면 investigation_report",
                "앞으로 할 일을 정하면 plan_draft",
            ),
        ),
        DocumentForm.AUDIT_MATERIAL: DocumentFormDefinition(
            label="감사자료",
            definition="감사·검사의 계획, 수행, 지적사항과 처분을 담은 문서",
            includes=(
                "감사 대상과 기간, 표본 선정 기준",
                "지적사항, 조치 요구, 처분 의견",
            ),
            excludes=(
                "설비·보안 상태 점검이면 inspection_report",
                "감사와 무관한 일반 업무 결과는 report",
            ),
        ),
        DocumentForm.PERSONNEL_MATERIAL: DocumentFormDefinition(
            label="인사자료",
            definition="채용·평정·승진·징계 등 인사 행위와 그 근거를 담은 문서",
            includes=(
                "발령 사항, 평정 결과, 징계 사유와 처분",
                "채용 전형 단계와 합격자 결정",
            ),
            excludes=(
                "발령 사실만 상대에게 통보하면 reply_notice",
                "인사 제도 자체를 설명하면 policy_material",
            ),
        ),
        DocumentForm.BID_MATERIAL: DocumentFormDefinition(
            label="입찰자료",
            definition="입찰·계약 절차에서 공고·평가·계약을 다루는 문서",
            includes=(
                "입찰공고와 재공고, 사전규격공개, 공모",
                "참가자격, 평가 기준과 배점, 예정가격, 낙찰자 결정",
            ),
            excludes=(
                "집행할 예산의 지출 결재면 approval_request",
                "계약 관련 회신이면 reply_notice",
            ),
        ),
        DocumentForm.APPROVAL_REQUEST: DocumentFormDefinition(
            label="승인·품의",
            definition="특정 행위나 지출을 하기 위해 결재권자의 승인을 구하는 문서",
            includes=(
                "품의 사유, 소요 금액과 예산 과목, 지급 상대방",
                "승인 요청 사항과 결재 의견란",
            ),
            excludes=(
                "사업 전체 방향을 설계하면 plan_draft",
                "승인 결과를 상대에게 알리면 reply_notice",
            ),
        ),
        DocumentForm.REPLY_NOTICE: DocumentFormDefinition(
            label="회신·통보",
            definition="받은 질의·요청·신청에 대한 답변이나 결과를 알리는 문서",
            includes=(
                "회신 대상 문서번호와 접수일 인용",
                "질의 요지와 그에 대한 답변, 결정 결과 통지",
            ),
            excludes=(
                "먼저 요청을 보내는 쪽이면 official_letter",
                "질의응답을 모아 안내하면 policy_material",
            ),
        ),
        DocumentForm.POLICY_MATERIAL: DocumentFormDefinition(
            label="정책자료",
            definition="제도·정책의 내용과 운영 방법을 설명해 안내하는 자료",
            includes=(
                "제도 취지, 적용 대상, 신청 절차와 서식 안내",
                "지침·매뉴얼, 질의회시 모음, 정책 설명자료",
            ),
            excludes=(
                "조문 형식으로 효력을 갖는 규범이면 administrative_rule",
                "언론 배포가 목적이면 press_release",
            ),
        ),
        DocumentForm.PLAN_DRAFT: DocumentFormDefinition(
            label="계획안",
            definition="앞으로 수행할 사업·조치의 목표와 추진 방법을 설계한 문서",
            includes=(
                "추진 배경과 목표, 대안 비교, 일정, 소요 예산",
                "단계별 추진 과제와 담당 부서",
            ),
            excludes=(
                "사고·위험에 대한 대응 절차면 response_plan",
                "이미 수행한 결과 정리는 report",
            ),
        ),
        DocumentForm.LEGAL_REVIEW: DocumentFormDefinition(
            label="법률검토서",
            definition="법령 해석과 법적 쟁점을 검토해 의견을 제시하는 문서",
            includes=(
                "적용 법령 조문과 해석, 쟁점별 검토 의견",
                "법률상 비밀·비공개 근거 조항의 적용 판단",
            ),
            excludes=(
                "질의에 대한 답변 형식이면 reply_notice",
                "제도 안내가 목적이면 policy_material",
            ),
        ),
        DocumentForm.INSPECTION_REPORT: DocumentFormDefinition(
            label="점검보고서",
            definition="시설·시스템·보안 상태를 점검한 결과와 취약점을 담은 문서",
            includes=(
                "점검 항목과 기준, 발견된 취약점과 위험도",
                "보안 진단 결과, 조치 필요 사항과 기한",
            ),
            excludes=(
                "회계·업무 적정성 감사면 audit_material",
                "발견한 위험에 대한 대응 절차 설계면 response_plan",
            ),
        ),
        DocumentForm.RESPONSE_PLAN: DocumentFormDefinition(
            label="대응계획서",
            definition="사고·재난·위험 상황에 대한 대응 절차와 역할을 정한 문서",
            includes=(
                "상황 단계별 조치 절차, 비상 연락 체계",
                "보호 대상과 대피·통제 방안",
            ),
            excludes=(
                "일반 사업 추진 설계면 plan_draft",
                "이미 발생한 사고의 경위 정리는 report",
            ),
        ),
        DocumentForm.INVESTIGATION_REPORT: DocumentFormDefinition(
            label="수사보고서",
            definition="범죄 수사·조사의 진행 상황과 확인 사실을 기록한 문서",
            includes=(
                "사건번호, 조사 대상자와 진술 요지",
                "확보 증거, 추가 확인 필요 사항, 향후 수사 계획",
            ),
            excludes=(
                "행정 감사면 audit_material",
                "시설 점검이면 inspection_report",
            ),
        ),
        DocumentForm.PRESS_RELEASE: DocumentFormDefinition(
            label="보도자료",
            definition="언론 배포를 목적으로 정책·성과를 알리는 문서",
            includes=(
                "배포 일시, 담당 부서와 연락처, 요약 리드 문단",
                "인용문과 사진·붙임 안내",
            ),
            excludes=(
                "제도 운영 방법 안내면 policy_material",
            ),
        ),
        DocumentForm.ADMINISTRATIVE_RULE: DocumentFormDefinition(
            label="행정규칙",
            definition="고시·훈령·예규처럼 조문 형식으로 효력을 갖는 규범 문서",
            includes=(
                "제1조·제2조 같은 조문 구성과 시행일",
                "제정·개정 사유, 신구 조문 대비",
            ),
            excludes=(
                "규범이 아니라 운영 방법 안내면 policy_material",
                "개별 사안 처분은 official_letter",
            ),
        ),
        DocumentForm.OTHER: DocumentFormDefinition(
            label="기타",
            definition="위 어느 형식에도 해당하지 않을 때만 고른다",
            includes=("형식을 특정할 단서가 본문에 전혀 없는 경우",),
            excludes=(
                "주제가 낯설다는 이유로 고르지 않는다 — 묻는 것은 무엇에 "
                "관한 내용인가가 아니라 어떤 서식인가다",
            ),
        ),
    }
)

#: 수집 라벨 26개를 채점 축인 문서 형식으로 접는 표.
#: 수집 라벨 자체는 provenance 메타데이터로 계속 보존한다.
DOCUMENT_FORM_BY_TYPE: Mapping[SemanticDocumentType, DocumentForm] = MappingProxyType(
    {
        SemanticDocumentType.BID_NOTICE: DocumentForm.BID_MATERIAL,
        SemanticDocumentType.BID_RENOTICE: DocumentForm.BID_MATERIAL,
        SemanticDocumentType.PRE_SPEC_NOTICE: DocumentForm.BID_MATERIAL,
        SemanticDocumentType.PUBLIC_OFFERING: DocumentForm.BID_MATERIAL,
        SemanticDocumentType.NOTICE: DocumentForm.OTHER,
        SemanticDocumentType.RESEARCH_REPORT: DocumentForm.REPORT,
        SemanticDocumentType.REPORT: DocumentForm.REPORT,
        SemanticDocumentType.STATUS_REPORT: DocumentForm.REPORT,
        SemanticDocumentType.AUDIT_RESULT: DocumentForm.AUDIT_MATERIAL,
        SemanticDocumentType.NOTIFICATION: DocumentForm.ADMINISTRATIVE_RULE,
        SemanticDocumentType.DIRECTIVE: DocumentForm.ADMINISTRATIVE_RULE,
        SemanticDocumentType.REGULATION: DocumentForm.ADMINISTRATIVE_RULE,
        SemanticDocumentType.PLAN: DocumentForm.PLAN_DRAFT,
        SemanticDocumentType.APPROVAL: DocumentForm.APPROVAL_REQUEST,
        SemanticDocumentType.BUDGET_EXECUTION: DocumentForm.APPROVAL_REQUEST,
        SemanticDocumentType.BUDGET_MATERIAL: DocumentForm.REPORT,
        SemanticDocumentType.MEETING_MINUTES: DocumentForm.MEETING_MINUTES,
        SemanticDocumentType.DIRECTOR_ACTIVITY: DocumentForm.MEETING_MINUTES,
        SemanticDocumentType.GUIDE: DocumentForm.POLICY_MATERIAL,
        SemanticDocumentType.INTERPRETATION_COMPILATION: DocumentForm.POLICY_MATERIAL,
        SemanticDocumentType.POLICY_MATERIAL: DocumentForm.POLICY_MATERIAL,
        SemanticDocumentType.OFFICIAL_DOCUMENT: DocumentForm.OFFICIAL_LETTER,
        SemanticDocumentType.REPLY_NOTIFICATION: DocumentForm.REPLY_NOTICE,
        SemanticDocumentType.PERSONNEL: DocumentForm.PERSONNEL_MATERIAL,
        SemanticDocumentType.BUSINESS_TRIP: DocumentForm.APPROVAL_REQUEST,
        SemanticDocumentType.PRESS_RELEASE: DocumentForm.PRESS_RELEASE,
        SemanticDocumentType.OTHER: DocumentForm.OTHER,
    }
)

#: 본문 서술만으로는 갈리기 어려워 명시적 우선순위가 필요한 형식 쌍.
DOCUMENT_FORM_BOUNDARY_RULES: tuple[str, ...] = (
    "문서 제목이나 특정 단어가 아니라 문서가 직접 수행하는 핵심 행정행위와 "
    "주된 목적을 기준으로 하나의 문서 형식을 선택한다. 일부 문단이나 붙임의 "
    "형식만으로 전체 문서 형식을 바꾸지 않는다.",
    "사람·부서·기관의 업무 수행이 법령과 절차에 맞았는지 판단하고 시정·처분을 "
    "요구하면 audit_material, 시설·장비·시스템의 상태·성능·취약성·안전성을 "
    "기술적으로 확인하면 inspection_report, 범죄 혐의와 관련된 사실관계·진술·"
    "증거·수사 진행 상황을 확인하면 investigation_report를 선택한다.",
    "이미 발생했거나 발생 가능성이 구체적인 사고·재난·위험에 대한 탐지·보고·"
    "통제·대피·복구 절차와 역할을 정하면 response_plan, 정상적인 행정·사업의 "
    "목표·범위·일정·예산·과업과 담당 부서를 설계하면 plan_draft를 선택한다.",
    "사업의 목표·범위·추진 방식·일정·예산 배분 등 전체 방향 자체를 결정받으려면 "
    "plan_draft, 이미 정해진 업무 방향 안에서 특정 지출·계약·출장·구매·행위의 "
    "실행 승인을 요청하면 approval_request를 선택한다. 결재란의 존재만으로 "
    "approval_request를 선택하지 않는다.",
    "기존 질의·신청·민원·요청·심사에 대한 답변이나 처리 결과를 알리면 "
    "reply_notice, 선행 요청에 대한 답변이 아니라 기관이 먼저 협조·제출·조치를 "
    "요청하거나 새로운 사안을 전달하면 official_letter를 선택한다.",
    "문서 자체가 적용 대상, 의무, 권한, 절차 또는 기준을 새로 정하는 규범이면 "
    "administrative_rule, 이미 존재하는 법령·규정·제도의 취지와 운영 방법·"
    "신청 절차를 설명하거나 안내하면 policy_material을 선택한다. 제목이나 "
    "조문 형식만으로 판정하지 않는다.",
    "채용·발령·평정·승진·징계 등의 인사 판단을 수행하거나 그 근거·과정·결정을 "
    "기록하면 personnel_material, 이미 결정된 인사 결과를 당사자나 관련 기관에 "
    "단순히 통지하면 reply_notice를 선택한다. 효력을 발생시키는 인사발령문은 "
    "personnel_material로 판정한다.",
    "위 전문 형식 중 어느 것에도 해당하지 않고 조사·연구·업무 수행 결과를 "
    "정리해 보고하는 문서일 때만 report를 선택한다.",
)

if set(DOCUMENT_FORM_BY_TYPE) != set(SemanticDocumentType):
    raise RuntimeError("every collected document type needs a document form")
if set(DOCUMENT_FORM_DEFINITIONS) != set(DocumentForm):
    raise RuntimeError("every document form requires a definition")


def render_document_form_guidance() -> str:
    """classifier와 validator가 같은 문서 형식 의미를 보도록 렌더링한다."""

    lines = ["[문서 형식]"]
    for form in DocumentForm:
        definition = DOCUMENT_FORM_DEFINITIONS[form]
        lines.append(f"- {form.value} ({definition.label}): {definition.definition}")
        lines.append(f"  포함: {' / '.join(definition.includes)}")
        lines.append(f"  제외: {' / '.join(definition.excludes)}")
    lines.append("")
    lines.append("[문서 형식 경계 규칙]")
    lines.extend(f"- {rule}" for rule in DOCUMENT_FORM_BOUNDARY_RULES)
    return "\n".join(lines)
