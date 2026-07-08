"""조항별 시나리오 데이터 (코드 품질 리뷰 #1: 하드코딩 함수가 아니라 데이터로 관리).

새 조항을 추가하거나 시나리오를 늘릴 때는 이 파일에 항목만 추가하면 되고,
generators/generate.py의 생성 함수는 수정할 필요가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rd2.schema.models import CsoClassification


@dataclass(frozen=True)
class ClauseDefinition:
    clause_no: str  # 숫자만 (예: "1") — Document.cso_sub_clause 포맷과 일치
    classification: CsoClassification
    title: str
    description: str
    scenario_prompts: list[str] = field(default_factory=list)  # 조항당 3~5개 변형 (다양성 확보)
    requires_pii: bool = False
    on_hold: bool = False


CLAUSES: dict[str, ClauseDefinition] = {
    "1": ClauseDefinition(
        clause_no="1",
        classification=CsoClassification.C,
        title="법률상 비밀·비공개 규정",
        description="다른 법률에서 비밀 또는 비공개로 규정한 사항",
        scenario_prompts=[
            "국가 사이버안보 대응 매뉴얼 중 침해사고 대응 절차 세부 지침 문서",
            "국가정보원법에 따라 비공개로 지정된 정보수집 활동 보고서 초안",
            "군사기밀보호법 적용 대상 무기체계 시험평가 결과 보고서",
            "형사소송법상 수사 비밀 유지 대상인 내사 진행 상황 보고 문서",
        ],
    ),
    "2": ClauseDefinition(
        clause_no="2",
        classification=CsoClassification.C,
        title="안보·국방·통일·외교 국익저해",
        description="공개될 경우 국가의 안전보장·국방·통일·외교관계 등 국가의 중대한 이익을 현저히 해칠 우려",
        scenario_prompts=[
            "대북 접경지역 군사대비태세 강화 방안 검토 문서",
            "주변국과의 비공개 외교 협상 전략 및 대응 시나리오 문건",
            "국방부 차세대 방위산업 기술 이전 관련 대외비 협의 자료",
        ],
    ),
    "3": ClauseDefinition(
        clause_no="3",
        classification=CsoClassification.C,
        title="국민 생명·신체·재산 보호 지장",
        description="공개될 경우 국민의 생명·신체 및 재산의 보호에 현저한 지장을 초래할 우려",
        scenario_prompts=[
            "원자력발전소 비상 대응 매뉴얼 중 물리적 방호 취약점 분석 문서",
            "댐 붕괴 시 하류지역 대피 시나리오 및 인명피해 예측 보고서",
            "위험물질 저장시설 보안 취약점 점검 결과 문서",
        ],
    ),
    "4": ClauseDefinition(
        clause_no="4",
        classification=CsoClassification.C,
        title="진행중 재판·범죄예방·수사·재판권 침해",
        description="진행 중인 재판 및 범죄의 예방과 수사, 공소의 제기 및 유지, 형의 집행, 교정·보안처분 관련 사항",
        scenario_prompts=[
            "진행 중인 대형 경제범죄 수사 내사 진행 상황 및 압수수색 계획 문서",
            "교정시설 보안 등급 재조정 및 수용자 이송 계획 내부 문건",
            "특정 사건 관련 피고인 신병처리 방침 검토 보고서",
        ],
    ),
    "5": ClauseDefinition(
        clause_no="5",
        classification=CsoClassification.S,
        title="감사·검사·입찰계약·기술개발·인사관리 내부검토",
        description="감사·감독·검사·시험·입찰계약·기술개발·인사관리 및 의사결정 과정 또는 내부검토 과정에 있는 사항",
        scenario_prompts=[
            "내부 감사 착수 전 특정 부서 비위 의혹 사전 검토 보고서",
            "대형 입찰 사업 평가위원 선정 및 배점 기준 내부 검토안",
            "차기 임원 인사 후보군 평가 및 검토 문서",
            "신기술 연구개발 과제 중간평가 내부 검토 자료",
        ],
    ),
    "6": ClauseDefinition(
        clause_no="6",
        classification=CsoClassification.S,
        title="성명·주민등록 등 개인정보",
        description="성명·주민등록번호 등 개인에 관한 사항으로 공개될 경우 개인의 사생활의 비밀 또는 자유를 침해할 우려",
        scenario_prompts=[
            "특정 민원인의 개인정보(성명, 주민등록번호, 주소, 연락처)가 포함된 민원 처리 결과 통보서",
            "채용 지원자 개인정보(성명, 생년월일, 연락처, 계좌번호)가 포함된 인사 평가 자료",
        ],
        requires_pii=True,
    ),
    "7": ClauseDefinition(
        clause_no="7",
        classification=CsoClassification.S,
        title="법인·개인 경영상·영업상 비밀",
        description="법인·단체 또는 개인의 경영상·영업상 비밀에 관한 사항으로 공개될 경우 정당한 이익을 현저히 침해",
        scenario_prompts=[
            "기업의 핵심 기술 특허 명세서 초안",
            "M&A 실사 금액표 및 인수 협상 조건 문서",
            "해킹 탐지 로그 기반 보안 취약점 진단 보고서 (기업 대외비)",
            "협력업체 원가구조 및 납품단가 협상 내부 자료",
        ],
    ),
    "8": ClauseDefinition(
        clause_no="8",
        classification=CsoClassification.S,
        title="부동산 투기·매점매석 이익",
        description="공개될 경우 부동산 투기·매점매석 등으로 특정인에게 이익 또는 불이익을 줄 우려",
        scenario_prompts=[
            "신규 택지개발지구 지정 예정 지역 사전 검토 문서",
            "공공기관 이전 부지 선정 관련 비공개 검토 자료",
        ],
    ),
    "기타": ClauseDefinition(
        clause_no="기타",
        classification=CsoClassification.S,
        title="로그·임시백업",
        description="시스템 로그, 임시 백업 등 — RD-2에서 우선 홀드 상태",
        scenario_prompts=[],
        on_hold=True,
    ),
}
