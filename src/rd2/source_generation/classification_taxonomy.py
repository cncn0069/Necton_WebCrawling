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

TAXONOMY_VERSION = "source-generation-taxonomy-v3"


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
            # 이 목록은 제5호의 다른 네 세부유형을 **빠짐없이** 가리켜야 한다.
            # ``decision_review``의 정의가 "특정 전문업무에 속하지 않는"이라
            # 잔여 범주인데, 목록에서 빠진 전문업무는 그 조건이 작동하지 않아
            # 이쪽으로 흘러든다.
            #
            # 실측(PRISM 9건): 연구개발이 빠져 있어 정책연구 평가·활용 보고서가
            # decision_review 4 / technology_development 3 / audit_inspection 2로
            # 흩어졌다. 제목이 거의 같은 `정책연구 활용결과 보고서` 4건이 세
            # 갈래로 갔다.
            #
            # **다만 이 줄을 넣고 다시 재보니 분포가 안 바뀌었다**(decision_review
            # 4 유지, technology_development 3 -> 2, 제7호·bid_contract로 새로
            # 샌 것 2). 제외 규칙은 "이미 decision_review를 고르려는 중"일 때만
            # 작동하는데, 판별기는 taxonomy를 훑다가 audit_inspection의 `평가·
            # 점검`에 먼저 걸리면 여기까지 오지 않는다. 같은 문서가 두 실행에서
            # `technology_development` -> `bid_contract`로 바뀌기도 했다 —
            # 판별기 자체가 흔들린다.
            #
            # 규칙 자체는 맞아서 남긴다(잔여 범주라면 다른 넷을 빠짐없이 가리켜야
            # 한다). 세부유형 흔들림은 프롬프트 한 줄로 잡히는 문제가 아니고,
            # 출처 성격이 뚜렷한 코퍼스는 목표를 고정하는 편이 확실하다.
            excludes=(
                "핵심 업무가 감사면 audit_inspection",
                "핵심 업무가 입찰이면 bid_contract",
                "핵심 업무가 인사면 personnel_management",
                "핵심 업무가 연구개발·연구용역 심사·평가면 technology_development",
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
                "식별 가능한 직원 또는 지원자에게 개인 연락처·주소·급여·건강정보, "
                "개인별 근무평정·징계처분 등 인사정보가 직접 연결되어 공개 시 "
                "사생활의 비밀 또는 자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "직원의 주민등록번호·개인 연락처·주소·계좌·급여·건강정보",
                "지원자의 개인 연락처·주소·이력과 개인별 시험 점수",
                "식별 가능한 직원의 개인별 근무평정 결과와 징계처분 내역",
            ),
            excludes=(
                "절차의 공정성이 핵심이고 개인 식별이 부수적이면 "
                "personnel_management",
                "이름·부서·직위·업무 연락처만 있는 직무상 공개 정보",
                "구체적인 위반 혐의·진술·조사 내용이 핵심이면 subject_pii",
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
                "조사·점검·심의 대상이 된 개인의 신원에 구체적인 위반 혐의·"
                "진술·조사 내용이 직접 연결되어 공개 시 사생활의 비밀 또는 "
                "자유를 침해할 우려가 있는 정보"
            ),
            includes=(
                "조사대상자의 성명과 구체적인 진술 내용",
                "식별 가능한 개인의 위반 혐의 사실과 조사 경위·확보 증거",
            ),
            excludes=(
                "조사·감사의 절차와 기준이 핵심이면 audit_inspection",
                "개인별 근무평정 또는 확정된 징계처분만 있으면 personnel_pii",
                "이름·부서·직위만 있고 혐의·진술·조사 내용이 없는 경우",
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
    "식별 가능한 직원의 개인별 근무평정·징계처분이면 personnel_pii, 조사대상자의 "
    "구체적인 위반 혐의·진술·조사 내용이면 subject_pii를 선택한다. 직원이라는 "
    "이유만으로 subject_pii 적용을 배제하지 않는다.",
    "사람의 이름만 있거나 직원의 이름·부서·직위·업무 연락처만 있으면 제6호 "
    "S 근거가 아니다. 식별 가능한 사람과 보호되는 개인속성이 직접 연결되어야 한다.",
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


#: 생성기 페르소나의 **업무 맥락** 축. 목표 세부유형이 곧 그 사람이 맡은 일이다.
#: 직무 축은 ``DOCUMENT_FORM_PERSONA``(``DocumentForm`` 정의 뒤에 있다)다 —
#: 둘을 조합해 ``prompts.render_generator_role_prompt``가 한 문장으로 만든다.
SUBCLAUSE_PERSONA_CONTEXT: Mapping[SubclauseKey, str] = MappingProxyType(
    {
        SubclauseKey.AUDIT_INSPECTION: "감사·검사 업무",
        SubclauseKey.BID_CONTRACT: "입찰·계약 업무",
        SubclauseKey.TECHNOLOGY_DEVELOPMENT: "연구개발 과제 심사 업무",
        SubclauseKey.DECISION_REVIEW: "정책 확정 전 내부 검토 업무",
        SubclauseKey.PERSONNEL_MANAGEMENT: "채용·승진 등 인사 절차 업무",
        SubclauseKey.PETITIONER_PII: "민원·신고 접수와 처리 업무",
        SubclauseKey.SUBJECT_PII: "조사·점검·분쟁 처리 업무",
        SubclauseKey.PERSONNEL_PII: "채용·급여·복무 등 직원 인사 업무",
        SubclauseKey.WELFARE_PII: "복지·급여 수급 자격 심사 업무",
        SubclauseKey.BUSINESS_STRATEGY: "기업 경영정보를 다루는 업무",
        SubclauseKey.MA_TERMS: "인수·합병 검토 업무",
        SubclauseKey.SECURITY_DIAGNOSIS: "정보보안 취약점 점검 업무",
        SubclauseKey.TECHNOLOGY_PATENT: "기술·특허 관리 업무",
        SubclauseKey.UNIT_COST: "원가·납품단가 산정 업무",
        SubclauseKey.CORNERING: "비축물자 수급 조절 업무",
        SubclauseKey.REAL_ESTATE_SPECULATION: "개발 계획과 보상 기준을 다루는 업무",
    }
)

@dataclass(frozen=True)
class SubclauseGenerationRule:
    """목표 세부유형 하나의 생성 지시.

    ``instruction``만 있던 때는 그 안에서 "사업검토서, 심의조서, 자문위원회
    회의내용" 같은 실제 문서명을 나열했는데, **그 문서가 무엇을 담는지는 어디에도
    없었다.** 이 파일이 이미 여러 번 배운 실패다 — ``evidence_level``도 문서형식도
    "이름만 던지면 모델은 enum 이름의 낱말로 짐작한다". 문서명을 그 안에 실제로
    담기는 항목과 함께 준다.
    """

    instruction: str
    #: ``"문서명: 그 안에 실제로 담기는 항목"`` 형태. 실제 지자체·기관의
    #: 비공개 세부기준이 이 세부유형으로 드는 문서에서 가져온다.
    document_patterns: tuple[str, ...] = ()


#: 목표 세부유형 하나에만 적용되는 생성 규칙.
#:
#: 이전에는 제5~8호 16개 규칙이 ``prompts.SENSITIVE_CLAUSE_GENERATION_GUIDANCE``
#: 한 문자열에 통째로 박혀 있었고, 생성기는 목표가 이미 잠겼는데도 16개를 전부
#: 읽었다. 실측(2026-07-31 seoul_opengov 10건)에서 ``decision_review`` 규칙이
#: "'아직 최종 확정되지 않은 내부 검토 단계' 같은 문장을 근거로 삼지 않는다"고
#: 이름까지 붙여 금지했는데도 생성물이 정확히 그 문장으로 끝나 독립 채점자가
#: O로 판정했다 — 금지 규칙이 관련 없는 15개 사이에 묻힌 상태였다.
#: ``document_form``을 잠긴 형식 하나로 필터링한 것과 같은 처방이다.
SUBCLAUSE_GENERATION_RULES: Mapping[SubclauseKey, SubclauseGenerationRule] = (
    MappingProxyType(
    {
        SubclauseKey.AUDIT_INSPECTION: SubclauseGenerationRule(
            # "실지감사 착수 전"만 있던 때는 감사 **결과** 보고서가 원문으로
            # 들어오면 쓸 자리가 없었다. 실측(alio-2021040202182097): 13쪽짜리
            # 연간감사 결과 보고서를 받고 기관명만 남긴 채 「2023년도 감사 계획
            # 검토 자료」를 새로 지어냈다.
            #
            # 그 원문 안에도 제5호 소재가 있었다 — `일상감사 대상 범위를
            # 200만원에서 100만원으로 강화`, `원가계산 및 예정가격 산정의
            # 적정성` 같은 점검 기준과 임계값이다. 착수 시점이 아니라 **확정
            # 여부**가 제5호를 세우므로 그렇게 넓힌다.
            instruction=(
                "실지감사·검사 착수 전이거나 아직 확정되지 않은 단계의 자료를 "
                "쓴다. 감사 대상 선정 기준, 표본 추출 기준, 중점 점검 항목, "
                "적용 임계값처럼 미리 알려지면 점검 대상이 대비할 수 있는 "
                "내용을 담고, 그것이 감사·검사의 공정한 수행을 어떻게 "
                "무력화하는지 문맥에서 드러나게 한다."
            ),
            document_patterns=(
                "감사·조사·단속 계획: 감사대상 선정 사유, 표본 추출 기준, "
                "중점 점검 항목, 확인하려는 혐의점, 현장 방문 예정일",
                "확인서·문답서: 확인하려는 사항과 담당자 진술 요지",
                "확정 전 지적사항 검토안: 지적 내용, 관련 법령 조문, 검토 중인 "
                "처분 의견",
            ),
        ),
        SubclauseKey.BID_CONTRACT: SubclauseGenerationRule(
            instruction=(
                "낙찰자 결정 전 단계의 자료를 쓴다. 공개되면 예정가격을 "
                "예측하거나 특정 입찰자를 식별할 수 있다는 점이 드러나게 한다."
            ),
            document_patterns=(
                "예정가격 조서: 품목별 단가와 산정 근거, 적용 요율, 낙찰 하한율",
                "평가위원회 심의안건·심의의결서: 위원 명단과 소속, 항목별 "
                "배점표, 업체별 채점 결과",
                "협상 경과 기록: 회차별 제시 조건과 조정 내용, 남은 쟁점",
            ),
        ),
        SubclauseKey.TECHNOLOGY_DEVELOPMENT: SubclauseGenerationRule(
            instruction=(
                "심사·연구가 끝나기 전 단계의 자료를 쓴다. 공개 시 심사나 "
                "연구개발의 공정한 수행에 생길 지장이 드러나게 한다."
            ),
            document_patterns=(
                "과제 선정 평가표: 심사위원별 항목 점수와 평가 의견, 순위",
                "연구용역 중간보고: 확정 전 중간 결과와 남은 과제, 변경 검토 사항",
                "개발 로드맵: 미공개 단계별 일정과 목표, 투입 계획",
            ),
        ),
        # 실제 지자체 비공개 기준(서울시·공주시)이 이 유형으로 드는 문서다.
        # 공문(수신처에 협조를 요청하는 문서)은 거의 없다 — 실측에서
        # official_letter × decision_review 4건이 3회 실행 내내 전부 O로
        # 판정된 것과 맞아떨어진다.
        SubclauseKey.DECISION_REVIEW: SubclauseGenerationRule(
            instruction=(
                "확정 전 검토 내용 자체를 쓴다. 검토된 대안을 안별로 나열하고, "
                "어느 부서·위원이 어떤 이유로 찬성·반대·보류했는지를 발언 "
                "수준의 구체적 사실로 적는다. 진행 중이라는 상태는 결재란 "
                "빈칸이 드러내므로, 문장은 상태 설명이 아니라 오간 검토 내용을 "
                "담는다."
            ),
            document_patterns=(
                "사업검토서(사업확정 전): 추진 배경, 비교한 대안과 각각의 "
                "장단점·소요예산, 담당부서 검토 의견",
                "예산편성요구 심의조서: 부서별 요구액과 사정액, 감액 사유, "
                "심의 의견",
                "자문·심의위원회 회의내용: 안건별 위원 발언과 찬반 이유, "
                "의결·보류 결과",
                "착수·중간보고 회의 결과: 진행 현황, 새로 드러난 쟁점, 다음 "
                "단계로 정한 사항",
                "유관기관 비공식 협의 내용: 기관별로 갈린 입장과 아직 조율되지 "
                "않은 쟁점",
            ),
        ),
        SubclauseKey.PERSONNEL_MANAGEMENT: SubclauseGenerationRule(
            instruction=(
                "임용·승진 절차가 끝나기 전 단계의 자료를 쓴다. 공개 시 인사의 "
                "공정성이 저해되거나 외부 개입이 가능해진다는 점이 드러나게 한다."
            ),
            document_patterns=(
                "시험 출제·채점 계획: 문제 은행 관리 방식, 출제·시험위원 위촉, "
                "채점 기준과 배점, 합격자 결정 방식",
                "승진심사위원회 회의록: 심사 대상자 서열 자료, 위원별 평가와 발언",
                "소청심사위원회 회의록: 청구 요지, 위원 의견, 의결 결과",
                "인사 이동 검토안: 전보·교류 대상자와 검토 사유, 확정 전 배치안",
            ),
        ),
        # 제6호 4종은 "누구에게 적용되는지"만 말하고 **무엇을 쓰라는 말이
        # 없었다.** 실측(2026-07-31)에서 personnel_pii·petitioner_pii·
        # subject_pii 목표 생성물에 사람이 아예 등장하지 않고 부서 간 협조
        # 문서로 나온 것과 맞아떨어진다 — 적용 대상만 알려주고 값을 요구하지
        # 않았으니 값이 안 나온 것이다. 쓸 값을 규칙 안에 명시한다.
        SubclauseKey.PERSONNEL_PII: SubclauseGenerationRule(
            instruction=(
                "채용·인사·급여·복무 업무에 실제로 등장하는 지원자 또는 "
                "직원에게 적용한다. 그 사람의 성명과 개인정보를 같은 행·같은 "
                "문장에서 직접 연결한다."
            ),
            document_patterns=(
                "인사기록·인적사항 카드: 성명, 생년월일, 주민등록번호, 자택 "
                "주소, 개인 연락처, 가족관계",
                "급여·수당 지급대장: 성명별 지급액과 공제 내역, 급여 계좌번호",
                "채용 지원자 명부: 성명, 연락처, 학력·경력, 개인별 시험 점수",
                "복무·건강 관리 자료: 병가 사유와 진단 내용, 장애 여부",
                "근무평정·징계 자료: 성명, 개인별 평정등급·평가의견, 징계처분",
            ),
        ),
        SubclauseKey.PETITIONER_PII: SubclauseGenerationRule(
            instruction=(
                "민원·신고 업무에 실제로 등장하는 민원인 또는 신고자에게 "
                "적용한다. 그 사람의 신원과 신고 내용을 직접 연결한다."
            ),
            document_patterns=(
                "민원 접수대장: 접수번호별 민원인 성명, 연락처, 주소",
                "민원 처리 내역: 신고 경위에 담긴 개인 사정과 피해 내용",
                "신고자 보호 관련 기록: 신고자 인적사항과 신고 대상의 관계",
            ),
        ),
        SubclauseKey.SUBJECT_PII: SubclauseGenerationRule(
            instruction=(
                "조사·점검·심의·분쟁 업무에 실제로 등장하는 조사대상자, "
                "진술인 또는 분쟁 당사자에게 적용한다. 그 사람의 신원과 구체적인 "
                "혐의·진술·조사 내용을 직접 연결한다."
            ),
            document_patterns=(
                "진술조서·확인서: 진술인 성명과 생년월일, 진술 요지",
                "조사대상자 명부: 성명, 소속, 위반 혐의 사실과 적용 조문",
                "심의 대상자 자료: 신상 정보와 심의에 부쳐진 사유",
            ),
        ),
        SubclauseKey.WELFARE_PII: SubclauseGenerationRule(
            instruction=(
                "복지·급여·지원 자격 업무에 실제로 등장하는 신청인, 수급자 "
                "또는 가구원에게 적용한다. 그 사람의 신원과 생활 사정을 직접 "
                "연결한다."
            ),
            document_patterns=(
                "수급자격 조사표: 신청인 성명, 소득·재산 내역, 부양의무자 관계",
                "가구원 명부: 가구 구성과 각 가구원의 건강 상태·취업 여부",
                "지원 신청서 처리 기록: 지원 사유에 기재된 개인 사정과 결정 결과",
            ),
        ),
        SubclauseKey.BUSINESS_STRATEGY: SubclauseGenerationRule(
            instruction=(
                "특정 법인·단체의 미공개 경영정보를 쓴다. 공개 시 그 법인의 "
                "정당한 이익이 현저히 침해되는 이유가 드러나게 한다."
            ),
            document_patterns=(
                "사업 확장·철수 계획: 대상 지역·품목, 투입 규모, 실행 일정",
                "재무 전망 자료: 연도별 매출·손익 추정치와 산출 가정",
                "거래처 관리 전략: 거래처별 등급과 단가 정책, 이탈 대응 방안",
            ),
        ),
        SubclauseKey.MA_TERMS: SubclauseGenerationRule(
            instruction=(
                "타결 전 인수·합병 검토 내용을 쓴다. 공개 시 협상 지위가 "
                "훼손되는 이유가 드러나게 한다."
            ),
            document_patterns=(
                "인수가격 산정 자료: 평가 방법별 산정액과 적용 할인율",
                "실사 보고서: 발견된 우발채무와 미공개 소송 현황",
                "협상 경과: 상대방과 회차별 제시 조건, 합의되지 않은 쟁점",
            ),
        ),
        SubclauseKey.SECURITY_DIAGNOSIS: SubclauseGenerationRule(
            instruction=(
                "조치되기 전 보안 취약점을 쓴다. 실제 기관·제품·주소·계정·"
                "자격증명·공격 절차는 재현하지 않고 비운영 가상 값만 쓴다."
            ),
            document_patterns=(
                "모의해킹 결과 보고서: 발견된 취약 지점과 위험 등급, 재현 경로 요약",
                "취약점 조치 현황표: 항목별 미조치 사유와 조치 기한",
                "보안장비 구성·네트워크 경로: 구간별 장비 배치와 통제 지점",
            ),
        ),
        SubclauseKey.TECHNOLOGY_PATENT: SubclauseGenerationRule(
            instruction=(
                "공개되지 않은 기술 내용을 쓴다. 공개 시 그 기술의 재산적 "
                "가치가 사라지는 이유가 드러나게 한다."
            ),
            document_patterns=(
                "공정·설계 자료: 단계별 조건 값과 사용 재료 배합",
                "출원 전 발명 신고서: 발명의 요지와 선행기술 대비 차별점",
                "기술이전 대상 노하우: 이전 범위와 대가 산정 근거",
            ),
        ),
        SubclauseKey.UNIT_COST: SubclauseGenerationRule(
            instruction=(
                "사업자의 원가 구조 자체를 쓴다. 표나 key-value 항목으로 "
                "정리하고, 공개 시 그 사업자의 협상력이 훼손되는 이유가 "
                "드러나게 한다."
            ),
            document_patterns=(
                "원가계산서: 품목별 재료비·노무비·경비와 이윤율",
                "납품단가 산출내역서: 단가 구성과 적용 할인",
                "하도급 대금 지급 구조: 공정별 지급 비율과 대금 흐름",
            ),
        ),
        SubclauseKey.CORNERING: SubclauseGenerationRule(
            instruction=(
                "공표 전 물자 수급 조절 내용을 쓴다. 이 내용이 미리 알려지면 "
                "선점·매점매석이 가능해진다는 점이 드러나게 한다."
            ),
            document_patterns=(
                "비축물자 방출 계획: 품목별 방출 시기와 물량, 방출 기준가",
                "수급 조절 계획: 수급 전망과 개입 시점, 조달 예정 물량",
            ),
        ),
        SubclauseKey.REAL_ESTATE_SPECULATION: SubclauseGenerationRule(
            instruction=(
                "고시·공고 전 단계의 자료를 쓴다. 이 내용이 미리 알려지면 해당 "
                "토지를 선매수할 수 있다는 점이 문맥에서 드러나게 한다."
            ),
            document_patterns=(
                "개발계획 검토안(고시 전): 후보지 목록과 구역 지정 검토 의견",
                "보상 기준 검토자료: 감정 기준과 필지별 예상 보상액",
                "매입 예정지 조서: 필지별 매입 우선순위와 협의 일정",
            ),
        ),
    }
    )
)

#: 세부유형 하나로 좁혀도 남아야 하는, 그 호 전체에 걸리는 규칙.
#:
#: 제6호의 개인정보 연결 규칙처럼 어느 세부유형을 고르든 똑같이 적용되는
#: 내용이다. 원문에서 네 세부유형을 나열하던 문장은 목표가 이미 하나로 잠긴
#: 이상 의미가 없어 "목표 세부유형"으로 일반화했다.
CLAUSE_GENERATION_SHARED_RULES: Mapping[ClauseNumber, tuple[str, ...]] = (
    MappingProxyType(
        {
            ClauseNumber.CLAUSE_5: (),
            ClauseNumber.CLAUSE_6: (
                "[SENSITIVE SEED]의 사람 역할이 원문 업무와 다르면 그 역할을 "
                "그대로 이식하지 않는다. 원문에 자연스럽게 존재하는 사람 역할과 "
                "마스킹된 필드의 의미에 맞는 새 가상 값을 생성한다.",
                "목표 세부유형에 맞는 식별 가능한 가상 주체와 구체적인 개인정보 "
                "또는 개인 사정을 같은 문장, key-value 항목 또는 표 행에서 직접 "
                "연결한다.",
                "개인정보를 넣을 적절한 열이 없으면 `개인 연락처`, `주소`, `계좌`, "
                "`개인 사정` 등 해당 값의 의미가 분명한 새 열을 추가하고, 같은 "
                "행의 식별 가능한 주체와 연결한다. 전화번호를 `제외사유`처럼 "
                "의미가 다른 기존 열에 넣지 않는다.",
                "직원의 직무상 이름·부서·직위·업무 연락처만으로 끝내지 않고, "
                "목표에 필요하면 개인 연락처·주소·계좌·급여·건강정보 또는 "
                "개인별 근무평정·징계처분 등 제6호 보호 대상을 새 가상 값으로 "
                "작성한다.",
            ),
            ClauseNumber.CLAUSE_7: (
                "해당 정보가 특정 법인·단체·개인의 경영·영업상 비밀이고 공개 시 "
                "정당한 이익을 현저히 해칠 구체적인 이유가 문맥에서 확인되게 한다.",
            ),
            ClauseNumber.CLAUSE_8: (
                "이미 공표·고시된 정보가 아니라 공개 전 정보여야 하며, 공개 시 "
                "특정인에게 생길 이익 또는 불이익의 경로가 문맥에서 확인되게 한다.",
            ),
        }
    )
)

_GENERATABLE_CLAUSES = (
    ClauseNumber.CLAUSE_5,
    ClauseNumber.CLAUSE_6,
    ClauseNumber.CLAUSE_7,
    ClauseNumber.CLAUSE_8,
)
_GENERATABLE_SUBCLAUSES = {
    subclause
    for clause in _GENERATABLE_CLAUSES
    for subclause in SUBCLAUSES_BY_CLAUSE[clause]
}
if set(SUBCLAUSE_GENERATION_RULES) != _GENERATABLE_SUBCLAUSES:
    raise RuntimeError(
        "every clause 5-8 subclause needs exactly one generation rule"
    )
if set(SUBCLAUSE_PERSONA_CONTEXT) != _GENERATABLE_SUBCLAUSES:
    raise RuntimeError(
        "every clause 5-8 subclause needs a persona work context"
    )
if set(CLAUSE_GENERATION_SHARED_RULES) != set(_GENERATABLE_CLAUSES):
    raise RuntimeError("every generatable clause needs a shared-rule entry")


def render_target_clause_section(subclause_key: SubclauseKey) -> str:
    """생성기가 맨 앞에서 읽는 **목표 조항 한 절**.

    판별기·검증기가 받는 taxonomy는 "이게 어느 세부유형인지 고르라"는 판별용
    이지만, 생성기는 고를 것이 없다 — 목표는 이미 잠겼고 할 일은 원문을 그
    조항에 **걸리도록 변형**하는 것이다.

    ``includes``도 ``excludes``도 여기서는 쓰지 않는다. 둘 다 **판별용**이다.

    ``includes``는 "이런 게 있으면 이 세부유형이다"라는 증거 목록인데,
    생성기에게 필요한 "무엇을 써라"는 ``SUBCLAUSE_GENERATION_RULES``가 이미
    더 자세히 담고 있어 16개 중 12개가 거의 같은 문장을 두 번 냈다
    (2026-07-31 확인). 나머지 4개(제6호 PII)는 규칙 쪽이 적용 대상만 말하고
    값을 빠뜨리고 있어서 ``includes``의 내용을 규칙 안으로 옮겼다.

    ``excludes``는 "핵심 업무가 감사면 audit_inspection"처럼 **다른 세부유형을
    고르라**는 문장이다. 목표가 이미 잠긴 생성기는 다시 고를 수 없고, 게다가
    그 문장이 인용하는 다른 세부유형 이름은 이 프롬프트 안에 설명이 없어
    가리키는 곳 없는 참조로 남는다 — 문서형식 절에서 ``제외``를 뺀 것과 같은
    이유다(``render_document_form_guidance``).
    """

    clause = clause_of_subclause(subclause_key)
    definition = SUBCLAUSE_DEFINITIONS[subclause_key]
    rule = SUBCLAUSE_GENERATION_RULES[subclause_key]
    lines = [
        f"[목표 조항: 정보공개법 제9조 제{clause.value}호 — "
        f"{definition.label}({subclause_key.value})]",
        f"판정 기준: {definition.definition}",
        "",
        "이 문서에 만들어 넣을 것:",
        f"- {rule.instruction}",
    ]
    lines.extend(f"- {shared}" for shared in CLAUSE_GENERATION_SHARED_RULES[clause])
    if rule.document_patterns:
        lines.append("")
        lines.append(
            "실무에서 이 내용을 담는 자료와, 그 안에 실제로 들어가는 항목:"
        )
        lines.extend(f"- {pattern}" for pattern in rule.document_patterns)
    return "\n".join(lines)


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
            # 경계 규칙과 같은 필터를 ``제외`` 항목에도 적용한다. 목록에서 뺀
            # 세부유형을 인용하는 항목은 가리키는 곳 없는 참조로 남는다 —
            # 실측(2026-08-01): 제5~8호만 준 검증기 프롬프트에 제3호
            # ``security_defense``가 ``security_diagnosis``의 제외 항목을 통해
            # 그대로 남아 있었다. 경계 규칙은 필터링되는데 이 줄만 빠져 있었다.
            visible_excludes = [
                item
                for item in definition.excludes
                if not any(key.value in item for key in excluded_subclauses)
            ]
            if visible_excludes:
                lines.append(f"  제외: {' / '.join(visible_excludes)}")
    lines.append("")
    lines.append("[세부조항 경계 규칙]")
    lines.extend(
        f"- {rule}"
        for rule in SUBCLAUSE_BOUNDARY_RULES
        if not any(key.value in rule for key in excluded_subclauses)
    )
    return "\n".join(lines)


def render_clause_6_validator_guidance() -> str:
    """민감정보 검증기에 제6호 네 세부유형과 그 경계만 제공한다.

    별도 문구를 복제하지 않고 ``SUBCLAUSE_DEFINITIONS``·
    ``SUBCLAUSE_BOUNDARY_RULES``를 렌더링하는 공용 함수에 필터만 적용한다.
    따라서 일반 분류기와 민감정보 검증기가 같은 정의를 보며, 제5·7·8호의
    관련 없는 후보는 민감정보 판정을 방해하지 않는다.
    """

    return render_taxonomy_guidance((ClauseNumber.CLAUSE_6,))


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
    """세부조항과 같은 처방 — 이름만 던지면 모델은 형식을 구분하지 못한다.

    ``includes``/``excludes``는 **판별용**이다 — "이게 이 형식인지 아닌지"를
    가리는 경계다. 생성기는 이를 필수 체크리스트로 사용하지 않는다.

    생성용 명세도 둘로 나눈다. ``required_elements``는 이 형식이면 변형과
    무관하게 항상 있어야 하는 요소이고, ``variant_patterns``는 원문 업무와
    목표 조항에 맞는 변형만 골라 쓰는 선택지다. ``generation_detail``은 선택된
    요소를 어떤 장·block·문장 관습으로 배열할지를 설명한다.
    """

    label: str
    definition: str
    includes: tuple[str, ...]
    excludes: tuple[str, ...]
    generation_detail: str
    required_elements: tuple[str, ...]
    variant_patterns: tuple[str, ...]


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
            generation_detail=(
                "안건마다 발언자와 발언 요지를 구분해 적고, 표결이 있으면 찬성·"
                "반대·기권 수를 명시한다. '검토 후 처리하기로 함' 같은 결론"
                "문장으로 요약하지 말고 실제 오간 발언을 재구성한다."
            ),
            required_elements=(
                "회의 회차·개최일시·장소와 참석자",
                "안건별 논의 내용과 의결 또는 후속 조치",
            ),
            variant_patterns=(
                "발언 중심 회의록: 발언자별 발언 요지와 견해 차이",
                "표결 회의록: 안건별 찬성·반대·기권 수와 의결 결과",
                "서면 심의 기록: 위원별 검토 의견과 서면 의결 결과",
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
            generation_detail=(
                "본문을 '~ 관련입니다'로 시작해 전달 목적과 사안을 밝힌다. "
                "협조·제출을 요구하는 변형에서만 요청 사항을 번호로 나열하고 "
                "회신 기한을 구체적인 날짜로 명시한다."
            ),
            required_elements=(
                "특정 수신처와 전달 목적",
                "제목-본문의 표준 시행문 구조",
            ),
            variant_patterns=(
                "협조 요청 공문: 요청 사항·담당 주체·회신 기한",
                "자료 제출 요구 공문: 제출 자료·방법·기한",
                "사안 통지 공문: 통지 내용·적용 시점·후속 조치",
                "붙임 동반 공문: 본문에서 붙임 자료명과 수량을 참조",
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
            generation_detail=(
                "Ⅰ. 개요 Ⅱ. 추진 경과·현황 Ⅲ. 결과 Ⅳ. 결론·건의 순의 장 "
                "구성을 쓰고, 비교 가능한 수치는 문장이 아니라 표나 "
                "key_value로 제시한다."
            ),
            required_elements=(
                "보고 대상 업무의 범위와 수행·조사 방법",
                "확인된 결과와 그에 근거한 결론 또는 건의",
            ),
            variant_patterns=(
                "연구·조사보고서: 방법·분석 결과·결론",
                "업무 결과보고: 추진 경과·성과·잔여 과제",
                "현황·통계보고: 기준 시점·집계표·변동 원인",
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
            generation_detail=(
                "감사대상·감사기간을 표제부에 두고 현재 감사 단계를 밝힌다. "
                "계획 단계면 점검 항목과 표본 기준을, 결과 단계면 '지적번호-"
                "지적내용-관련법령-조치요구사항' 열을 가진 표를 쓴다."
            ),
            required_elements=(
                "감사 대상·범위·기간과 수행 단계",
                "감사 기준에 따른 확인 항목 또는 지적사항",
            ),
            variant_patterns=(
                "감사 계획: 표본 선정 기준·중점 점검 항목·일정",
                "감사 수행 기록: 확인 사항·문답·잠정 판단",
                "감사 결과: 지적사항·관련 법령·조치 요구·처분 의견",
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
            generation_detail=(
                "인사 행위의 대상자별 행을 가진 표를 기본 구조로 쓰되, 선택한 "
                "인사 변형에 필요한 열만 둔다. 개인별 판단 근거는 같은 행 또는 "
                "인접한 key_value·문단에서 대상자와 분명하게 연결한다."
            ),
            required_elements=(
                "대상자와 채용·발령·평정·승진·징계 중 해당 인사 행위",
                "행위의 기준·근거와 결정 또는 검토 상태",
            ),
            variant_patterns=(
                "채용 자료: 전형 단계·평가기준·지원자별 결과",
                "발령·전보 자료: 현 직위·발령 내용·발령일",
                "평정·승진 자료: 대상자별 평가·서열·심사 의견",
                "징계 자료: 징계 사유·근거 규정·처분 또는 심의 의견",
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
            generation_detail=(
                "공고·평가·협상·계약 중 현재 단계를 하나 정해 표제부와 "
                "본문을 구성한다. 선택한 단계에 필요한 조건·가격·평가 정보만 "
                "표 또는 목록으로 제시한다."
            ),
            required_elements=(
                "입찰·계약 대상과 현재 절차 단계",
                "해당 단계의 일정 및 참가·평가·계약 조건",
            ),
            variant_patterns=(
                "입찰공고·재공고: 참가자격·일정·제출방법",
                "사전규격공개·공모: 요구 규격·의견 제출 절차",
                "평가 자료: 평가 항목·배점·위원 또는 업체별 결과",
                "협상·계약 자료: 예정가격·회차별 조건·낙찰자 결정",
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
            generation_detail=(
                "품의 사유와 승인받을 사항을 먼저 적고 기안-검토-결재 열을 "
                "가진 table을 둔다. 지출 변형일 때만 소요예산·예산과목·"
                "지급상대방을 key_value로 제시한다."
            ),
            required_elements=(
                "승인을 구하는 구체적인 행위와 품의 사유",
                "승인 요청 사항과 기안·검토·결재 상태",
            ),
            variant_patterns=(
                "지출 품의: 소요금액·예산과목·지급상대방",
                "계약·구매 품의: 대상·선정 사유·계약 조건",
                "출장·행사·업무 승인: 목적·기간·참여자·승인 범위",
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
            generation_detail=(
                "'귀하께서 신청(질의)하신 ~에 대해 다음과 같이 회신합니다'처럼 "
                "원 요청을 인용하는 문장으로 시작한다. 처분·신청 결과 변형이면 "
                "승인·불승인·보류 등 결정 결과를, 질의 회신이면 질문별 답변을 "
                "명확히 적는다."
            ),
            required_elements=(
                "회신 대상이 된 선행 질의·요청·신청의 식별 정보와 요지",
                "그 선행 사안에 대한 답변 또는 처리 결과",
            ),
            variant_patterns=(
                "질의 회신: 질문별 답변과 근거",
                "신청·심사 결과 통보: 승인·불승인·보류 결과와 사유",
                "처리 결과 통지: 조치 내용·완료일·추가 절차",
                "인사 결과 통지: 확정된 발령·처분과 효력 발생일",
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
            generation_detail=(
                "제도 개요-적용대상-신청절차-문의처 순으로 절을 나누고, "
                "절차는 단계별 번호 목록으로 쓴다."
            ),
            required_elements=(
                "제도·정책의 취지와 적용 대상",
                "운영 방법·이용 절차와 문의 또는 담당 부서",
            ),
            variant_patterns=(
                "정책 설명자료: 추진 배경·핵심 내용·기대 효과",
                "지침·매뉴얼: 단계별 처리 절차·담당 역할·서식",
                "질의회시·FAQ: 질문별 답변과 적용 사례",
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
            generation_detail=(
                "추진배경-목표-추진전략-세부과제(표)-소요예산-추진일정(표) "
                "순으로 구성하되, 예산이 없는 조치라면 소요예산을 강제하지 "
                "않는다. 확정 전 문서면 제목에 '(안)'을 붙인다."
            ),
            required_elements=(
                "추진 배경·목표와 앞으로 수행할 세부 과제",
                "과제별 담당 부서와 추진 일정",
            ),
            variant_patterns=(
                "사업계획: 추진전략·세부사업·예산·성과지표",
                "업무추진계획: 과제·담당·일정·보고체계",
                "개선계획: 현황·문제점·개선과제·이행점검",
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
            generation_detail=(
                "쟁점마다 '쟁점-관련 법령·조문-검토의견-결론' 구조를 "
                "반복한다. 결론은 단정적 문장으로 쓰되 근거 조문을 함께 "
                "인용한다."
            ),
            required_elements=(
                "검토할 법적 쟁점과 사실관계",
                "적용 법령·조문, 해석·검토 의견과 결론",
            ),
            variant_patterns=(
                "법률 의견서: 쟁점별 법리·판단·권고",
                "법령 해석 검토: 문언·체계·입법 취지에 따른 해석",
                "비공개 근거 검토: 대상 정보·적용 조항·공개이익 형량",
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
            generation_detail=(
                "점검일시·점검대상·점검자를 표제부에 두고, 발견 사항은 "
                "'점검항목-발견내용-위험도-조치기한' 열을 가진 표로 "
                "정리한다."
            ),
            required_elements=(
                "점검 일시·대상·수행자와 점검 기준",
                "항목별 발견 내용·판정과 필요한 조치",
            ),
            variant_patterns=(
                "시설·안전 점검: 설비별 상태·위험등급·보수 기한",
                "시스템·보안 점검: 취약 항목·위험도·조치 상태",
                "성능·품질 점검: 시험 기준·측정값·적합 여부",
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
            generation_detail=(
                "상황단계(관심-주의-경계-심각 등)별로 조치사항을 표로 "
                "나누고, 단계별 담당 부서와 연락체계를 key_value로 붙인다."
            ),
            required_elements=(
                "대상 사고·재난·위험 시나리오와 발동 기준",
                "상황 단계별 조치·담당 역할·보고 및 연락체계",
            ),
            variant_patterns=(
                "재난·안전 대응: 경보·대피·통제·복구 절차",
                "정보보안 사고 대응: 탐지·격리·보고·복구 절차",
                "업무연속성 계획: 핵심업무·대체수단·복구 우선순위",
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
            generation_detail=(
                "사건번호·조사대상자·소속을 표제부에 두고, 진술 요지는 "
                "'일시-장소-진술인-진술내용' 구조로, 확보 증거는 목록으로 "
                "작성한다."
            ),
            required_elements=(
                "사건·조사의 식별 정보와 조사 대상 또는 범위",
                "확인 사실·진술·증거와 향후 조사 계획",
            ),
            variant_patterns=(
                "진술·신문 기록: 일시·장소·진술인·구체 진술",
                "증거 확인 보고: 확보 경위·증거 내용·혐의와의 관련성",
                "수사 진행 보고: 현재까지 확인 사실·미확인 쟁점·다음 조치",
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
            generation_detail=(
                "배포일시와 담당자 연락처를 머리에 두고, 첫 문단(리드)에 "
                "핵심 내용을 요약한 뒤 상세 설명이 이어지는 역피라미드 "
                "구조로 쓴다."
            ),
            required_elements=(
                "언론 배포 일시·담당 부서·업무 연락처",
                "핵심 사실을 요약한 리드 문단과 이를 뒷받침하는 상세 내용",
            ),
            variant_patterns=(
                "정책 발표: 시행 배경·핵심 내용·적용 시점",
                "사업·성과 발표: 대표 수치·성과·향후 일정",
                "행사 안내: 일시·장소·참여 방법·취재 안내",
                "인용·사진 동반 자료: 관계자 인용문·사진 또는 붙임 설명",
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
            generation_detail=(
                "제1조(목적)부터 적용대상·권한·의무·절차를 조문 번호로 "
                "구성한다. 개정 변형일 때만 신구 조문 대비표를 둔다."
            ),
            required_elements=(
                "규범의 명칭·근거와 제1조부터 이어지는 조문 구조",
                "적용 대상·의무·권한·절차 및 시행일 또는 부칙",
            ),
            variant_patterns=(
                "제정 규칙: 목적·정의·적용범위·본문 조항·부칙",
                "개정 규칙: 개정 이유·개정 조문·신구 조문 대비",
                "폐지 규칙: 폐지 근거·경과조치·시행일",
            ),
        ),
        DocumentForm.OTHER: DocumentFormDefinition(
            label="기타",
            definition=(
                "문서형식이 정의된 16개 전문 형식 중 어느 것에도 해당하지 않는 "
                "목록 밖 형식이거나, 자료만으로 형식을 식별할 수 없는 경우"
            ),
            includes=(
                "신청서·접수대장·확인서 등 목록 밖의 구체적 형식",
                "자료가 불완전하여 형식 자체를 식별할 수 없는 경우",
            ),
            excludes=(
                "주제가 낯설다는 이유로 고르지 않는다 — 묻는 것은 무엇에 "
                "관한 내용인가가 아니라 정의된 16개 형식 중 하나인가다",
                "정의된 16개 형식의 핵심 행정행위와 주된 목적에 해당하는 경우",
            ),
            generation_detail=(
                "목록 밖 형식이 식별되면 그 형식의 기본 골격을 유지한다. 형식 "
                "자체를 식별할 수 없으면 '형식 불명'으로 기록하고 최소한의 "
                "표제부 key_value와 본문 문단만 구성한다."
            ),
            required_elements=(
                "[SOURCE CONTEXT]에 목록 밖의 구체적 형식명이 있으면 그 형식명과 "
                "원문 골격을 문서에 드러내고, 식별할 수 없으면 제목 또는 첫 "
                "문단에 '형식 불명' 기록",
                "식별된 목록 밖 형식의 핵심 골격 또는 형식 불명 자료의 최소 본문",
            ),
            variant_patterns=(
                "목록 밖 형식: 신청서·접수대장·확인서 등 식별된 골격 유지",
                "형식 불명: 최소 표제부와 본문 문단으로만 구성",
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
    "정의된 16개 전문 형식의 핵심 행정행위와 주된 목적 중 어느 것에도 해당하지 "
    "않으면 other를 선택한다. 목록 밖 형식이 식별되면 other_document_form에 "
    "'신청서'·'접수대장'처럼 구체적으로 쓰고, 자료만으로 형식을 식별할 수 "
    "없을 때는 '형식 불명'이라고 쓴다.",
)

if set(DOCUMENT_FORM_BY_TYPE) != set(SemanticDocumentType):
    raise RuntimeError("every collected document type needs a document form")
if set(DOCUMENT_FORM_DEFINITIONS) != set(DocumentForm):
    raise RuntimeError("every document form requires a definition")

#: 이 문서형식을 실제로 작성하는 사람. 생성기 페르소나의 **직무** 축이다.
#:
#: "당신은 생성기다"라고 시작하면 모델은 LLM으로서 글을 쓴다 — 서식은 흉내
#: 내지만 그 직무가 실제로 쓰는 문장 관행(감사반의 "~할 것을 요구함", 회의
#: 간사의 발언 인용)은 나오지 않는다. 그 문서를 매일 쓰는 사람으로 세우면
#: 문체와 관행이 함께 따라온다.
#:
#: 업무 맥락 축은 ``SUBCLAUSE_PERSONA_CONTEXT``다.
#: 직함만 담는다 — 무슨 일을 하는 사람인지는 ``SUBCLAUSE_PERSONA_CONTEXT``와
#: 문서형식 이름이 이미 말한다. 여기에 "~를 기록하는" 같은 수식절을 넣으면
#: 업무 맥락 수식절과 겹쳐 "감사 업무를 맡고 있는 회의를 기록하는 간사"처럼
#: 읽힌다.
DOCUMENT_FORM_PERSONA: Mapping[DocumentForm, str] = MappingProxyType(
    {
        DocumentForm.MEETING_MINUTES: "회의 간사",
        DocumentForm.OFFICIAL_LETTER: "기안 주무관",
        DocumentForm.REPORT: "보고 담당 주무관",
        DocumentForm.AUDIT_MATERIAL: "감사담당관",
        DocumentForm.PERSONNEL_MATERIAL: "인사담당자",
        DocumentForm.BID_MATERIAL: "계약담당공무원",
        DocumentForm.APPROVAL_REQUEST: "기안자",
        DocumentForm.REPLY_NOTICE: "민원 처리 담당자",
        DocumentForm.POLICY_MATERIAL: "제도 담당 주무관",
        DocumentForm.PLAN_DRAFT: "기획 담당자",
        DocumentForm.LEGAL_REVIEW: "법무담당관",
        DocumentForm.INSPECTION_REPORT: "점검반원",
        DocumentForm.RESPONSE_PLAN: "상황 대응 담당자",
        DocumentForm.INVESTIGATION_REPORT: "조사관",
        DocumentForm.PRESS_RELEASE: "공보담당자",
        DocumentForm.ADMINISTRATIVE_RULE: "법제 담당자",
        DocumentForm.OTHER: "담당 주무관",
    }
)

if set(DOCUMENT_FORM_PERSONA) != set(DocumentForm):
    raise RuntimeError("every document form needs a writer persona")


def render_document_form_guidance(
    forms: tuple[DocumentForm, ...] | None = None,
) -> str:
    """문서 형식 정의를 렌더링한다.

    ``forms``로 보여줄 형식을 고른다 — ``render_taxonomy_guidance``가
    ``clause_numbers``로 세부조항을 고르는 것과 같은 패턴이다. relevance·
    classifier·validator는 형식을 스스로 판별해야 하므로 ``forms=None``(전체
    17개 + 경계 규칙)을 그대로 받는다.

    ``forms``는 판별 후보를 제한해야 하는 호출자를 위한 필터다. 이 함수가
    보여주는 ``includes``/``excludes``는 끝까지 판별용이며, 생성기는
    ``required_elements``/``variant_patterns``를 사용하는 생성 전용 렌더러를
    사용한다. 후보가 제한되면 다른 후보를 가리키는 제외·경계 문구는 뺀다.
    """

    selected_forms = forms or tuple(DocumentForm)
    lines = ["[문서 형식]"]
    for form in selected_forms:
        definition = DOCUMENT_FORM_DEFINITIONS[form]
        lines.append(f"- {form.value} ({definition.label}): {definition.definition}")
        lines.append(f"  포함: {' / '.join(definition.includes)}")
        if forms is None:
            lines.append(f"  제외: {' / '.join(definition.excludes)}")
    if forms is None:
        lines.append("")
        lines.append("[문서 형식 경계 규칙]")
        lines.extend(f"- {rule}" for rule in DOCUMENT_FORM_BOUNDARY_RULES)
    return "\n".join(lines)


def render_generation_detail_guidance(document_form: DocumentForm) -> str:
    """잠긴 형식 하나의 생성 전용 상세 안내를 렌더링한다.

    ``render_document_form_guidance``와 달리 판별용 includes/excludes는 전혀
    사용하지 않는다. 변형과 무관한 필수요소, 원문·목표에 맞춰 선택하는 변형,
    장 구성·block·문장 관습만 보여준다.
    """

    definition = DOCUMENT_FORM_DEFINITIONS[document_form]
    label = definition.label
    lines = [
        "[이 문서형식의 생성 상세]",
        f"- {document_form.value} ({label}): {definition.generation_detail}",
        "",
        "항상 필요한 요소:",
    ]
    lines.extend(f"- {item}" for item in definition.required_elements)
    lines.extend(("", "원문 업무와 목표 조항에 맞는 변형만 선택한다:"))
    lines.extend(f"- {item}" for item in definition.variant_patterns)
    return "\n".join(lines)
