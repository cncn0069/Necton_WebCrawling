"""조항×세부조항×문서유형별 내용 민감 포인트 (docs/content-sensitivity-scenario-matrix-20260723.md 코드화).

Part A: doc_type 16개 각각에 공통으로 등장하는 증강 포인트(명사구) — subclause와
무관하게 그 문서유형이면 자연스럽게 나타나는 화제/필드다.

Part B: 61개 (clause_no, subclause_key, doc_type) leaf마다 Part A 포인트 풀에서
"핵심"(비공개의 대표 이유로 앵커링할 포인트)과 "부수"(같이 나와도 되지만 대표
이유로 쓰면 안 되는 포인트)를 지정한다. `personnel`의 5호/6호 쌍처럼 leaf가
같은 필드를 다른 관점(절차적 공정성 vs 신원)으로 재사용하는 경우가 있다.

제보자(whistleblower) 신원보호는 어느 doc_type의 Part A 공통 포인트에도 넣지
않았다 — 실제 법적 근거가 공익신고자보호법이라 정보공개법상 인용은 1호이고,
5호 감사·6호 개인정보의 공통 포인트로 일반화하면 안 된다(2026-07-23 조사).
1호 legal_secret/meeting_minutes에서만 전용 포인트로 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass

from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_BID_RENOTICE,
    DOC_TYPE_INTERPRETATION_COMPILATION,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_NOTICE,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_PRE_SPEC_NOTICE,
    DOC_TYPE_PRESS_RELEASE,
    DOC_TYPE_PUBLIC_OFFERING,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
)

# Part A — doc_type별 공통 증강 포인트.
DOC_TYPE_COMMON_POINTS: dict[str, tuple[str, ...]] = {
    DOC_TYPE_OFFICIAL_DOCUMENT: ("협조요청 내용", "처분 근거", "첨부자료 유무", "수신·발신 기관", "비공개 처리 표시"),
    DOC_TYPE_POLICY_MATERIAL: ("정책방향", "시행일정", "예산배정 규모", "이해관계자 의견수렴 결과"),
    DOC_TYPE_REPORT: ("조사·점검 경위", "원인분석(미확정)", "피해·실적 규모", "향후계획", "참고자료 첨부"),
    DOC_TYPE_MEETING_MINUTES: ("참석자 의견", "의사결정 과정", "미확정 안건", "표결·보류 결과", "반대의견"),
    DOC_TYPE_PRESS_RELEASE: ("배포 전 초안", "내부 Q&A 대응", "표현 수위 조율", "배포 시점"),
    DOC_TYPE_PLAN: ("추진일정", "예산계획", "대상지·대상자 선정기준", "시행 전 협의사항"),
    DOC_TYPE_AUDIT_RESULT: ("조사대상", "지적사항", "시정요구", "증빙자료 목록", "처분 검토의견"),
    DOC_TYPE_BID_NOTICE: ("예정가격", "입찰조건", "참여제한 사유", "규격사양", "낙찰기준"),
    DOC_TYPE_APPROVAL: ("검토의견", "결재라인 이견", "승인절차", "계정정보·접근권한 부여", "예산집행 승인"),
    DOC_TYPE_BID_RENOTICE: ("재공고 사유", "유찰원인", "조건변경 내역"),
    DOC_TYPE_PUBLIC_OFFERING: ("심사기준", "심사위원 명단", "평가점수", "선정결과 이의"),
    DOC_TYPE_PRE_SPEC_NOTICE: ("규격사양", "업계 의견수렴", "특정업체 유불리"),
    DOC_TYPE_PERSONNEL: ("인사평가 등급", "승진후보자 명단", "징계검토 내역", "채용전형 결과", "연봉·성과급 등급", "발령사항"),
    DOC_TYPE_NOTICE: ("시행일정", "이해관계자 반발 예상", "공표범위"),
    DOC_TYPE_INTERPRETATION_COMPILATION: ("해석사례", "상충되는 유권해석", "소급적용 여부"),
    DOC_TYPE_REPLY_NOTIFICATION: ("민원인 정보", "처리결과 요지", "근거법령", "사실관계 확인내용"),
}


@dataclass(frozen=True)
class LeafContentPoints:
    core: tuple[str, ...]
    secondary: tuple[str, ...] = ()


# Part B — leaf별 핵심/부수 포인트. 키는 (clause_no, subclause_key, doc_type) —
# template_matrix.TEMPLATE_TARGETS/doc_templates.TEMPLATE_VARIANTS와 동일 규칙.
LEAF_CONTENT_POINTS: dict[tuple[str, str, str], LeafContentPoints] = {
    # 1호
    ("1", "legal_secret", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("비공개 처리 표시(타 법령상 비밀 지정 근거)",), secondary=("첨부자료 유무",)
    ),
    ("1", "legal_secret", DOC_TYPE_POLICY_MATERIAL): LeafContentPoints(
        core=("정책방향(법령상 비밀정보 활용)",), secondary=("이해관계자 의견수렴",)
    ),
    ("1", "legal_secret", DOC_TYPE_REPORT): LeafContentPoints(
        core=("참고자료 첨부(법령상 비밀 원자료)",), secondary=("조사·점검 경위",)
    ),
    ("1", "legal_secret", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=(
            "제보자 신원보호(공익신고자보호법 인용 — 1호 전용, Part A 공통목록엔 없음)",
            "징계위원회 회의록·위원명단 비공개(공무원징계령 제20·21조 인용 — 2026-07-23 검색 검증, 1호 전용)",
            "미확정 안건(비밀정보 접근권한 심의)",
        ),
        secondary=("표결·보류 결과",),
    ),
    # 2호
    ("2", "security_defense", DOC_TYPE_POLICY_MATERIAL): LeafContentPoints(
        core=("정책방향(안보정책, 전력배치)",), secondary=("시행일정(미도래)",)
    ),
    ("2", "security_defense", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("협조요청 내용(안보 협조)",), secondary=("수신·발신 기관(보안등급)",)
    ),
    ("2", "security_defense", DOC_TYPE_PRESS_RELEASE): LeafContentPoints(
        core=("표현 수위 조율(안보 사안 표현)",), secondary=("배포 시점",)
    ),
    ("2", "unification_diplomacy", DOC_TYPE_REPORT): LeafContentPoints(
        core=("조사·점검 경위(협상 동향)",), secondary=("향후계획",)
    ),
    ("2", "unification_diplomacy", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("의사결정 과정(협상전략 논의)",), secondary=("참석자 의견",)
    ),
    ("2", "unification_diplomacy", DOC_TYPE_PLAN): LeafContentPoints(
        core=("시행 전 협의사항(비공개 협상 일정)",), secondary=("추진일정",)
    ),
    # 3호
    ("3", "life_body", DOC_TYPE_REPORT): LeafContentPoints(
        core=("원인분석(미확정, 사고원인)",), secondary=("피해·실적 규모",)
    ),
    ("3", "life_body", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("처분 근거(안전조치명령)",), secondary=("협조요청 내용",)
    ),
    ("3", "life_body", DOC_TYPE_POLICY_MATERIAL): LeafContentPoints(
        core=("정책방향(시설 취약점 포함 안전정책)",), secondary=("예산배정 규모",)
    ),
    ("3", "life_body", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("참석자 의견(책임소재 논쟁)",), secondary=("표결·보류 결과",)
    ),
    ("3", "life_body", DOC_TYPE_PRESS_RELEASE): LeafContentPoints(
        core=("배포 전 초안(피해규모 확정 전)",), secondary=("배포 시점",)
    ),
    ("3", "property", DOC_TYPE_REPORT): LeafContentPoints(
        core=("피해·실적 규모(재산피해 산정)",), secondary=("원인분석",)
    ),
    ("3", "property", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("처분 근거(재해보상 처분)",), secondary=("첨부자료 유무(손해사정서)",)
    ),
    ("3", "property", DOC_TYPE_PLAN): LeafContentPoints(
        core=("예산계획(보상 예산 미확정)",), secondary=("대상지·대상자 선정기준",)
    ),
    # 4호
    ("4", "trial_investigation", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("의사결정 과정(수사대책 논의)",), secondary=("미확정 안건",)
    ),
    ("4", "trial_investigation", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("협조요청 내용(수사협조, 사건번호)",), secondary=("비공개 처리 표시",)
    ),
    ("4", "trial_investigation", DOC_TYPE_REPORT): LeafContentPoints(
        core=("조사·점검 경위(수사 진행상황)",), secondary=("원인분석",)
    ),
    ("4", "prosecution", DOC_TYPE_APPROVAL): LeafContentPoints(
        core=("검토의견(공소제기 여부)",), secondary=("승인절차",)
    ),
    ("4", "prosecution", DOC_TYPE_REPLY_NOTIFICATION): LeafContentPoints(
        core=("처리결과 요지(수사결과 요지, 불기소 이유)",), secondary=("근거법령",)
    ),
    ("4", "correction_security", DOC_TYPE_PLAN): LeafContentPoints(
        core=("대상지·대상자 선정기준(특별계호 대상자)",), secondary=("시행 전 협의사항",)
    ),
    ("4", "correction_security", DOC_TYPE_REPORT): LeafContentPoints(
        core=("조사·점검 경위(형집행 실태점검)",), secondary=("향후계획",)
    ),
    # 5호
    ("5", "audit_inspection", DOC_TYPE_AUDIT_RESULT): LeafContentPoints(
        core=("지적사항(확정 전 초안)",), secondary=("증빙자료 목록", "처분 검토의견")
    ),
    ("5", "audit_inspection", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("표결·보류 결과(지적사항 확정 전)",), secondary=("반대의견",)
    ),
    ("5", "audit_inspection", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("처분 근거(감사결과 통보)",), secondary=("첨부자료 유무",)
    ),
    ("5", "audit_inspection", DOC_TYPE_REPORT): LeafContentPoints(
        core=("원인분석(위반사항 초안, 미확정)",), secondary=("조사·점검 경위",)
    ),
    ("5", "bid_contract", DOC_TYPE_BID_NOTICE): LeafContentPoints(
        core=("예정가격(입찰 사전정보)",), secondary=("참여제한 사유",)
    ),
    ("5", "bid_contract", DOC_TYPE_APPROVAL): LeafContentPoints(
        core=("승인절차(계약체결 결재)",), secondary=("검토의견(업체평가)",)
    ),
    ("5", "bid_contract", DOC_TYPE_BID_RENOTICE): LeafContentPoints(
        core=("재공고 사유(특정업체 문제)",), secondary=("유찰원인", "조건변경 내역")
    ),
    ("5", "bid_contract", DOC_TYPE_PUBLIC_OFFERING): LeafContentPoints(
        core=("심사기준", "심사위원 명단(확정 전)"), secondary=("평가점수", "선정결과 이의")
    ),
    ("5", "bid_contract", DOC_TYPE_PRE_SPEC_NOTICE): LeafContentPoints(
        core=("규격사양(경쟁사 대비 민감 항목)",), secondary=("특정업체 유불리",)
    ),
    ("5", "personnel_management", DOC_TYPE_PERSONNEL): LeafContentPoints(
        # 징계검토는 여기서 뺐다 — 공무원징계령 제20·21조가 명시적으로 비공개를
        # 정하므로 5호 일반론이 아니라 1호(legal_secret/meeting_minutes)가 맞다
        # (2026-07-23 검색 검증). 인사위원회 승진심사는 판례(인천지방법원
        # 2007구합4753)상 "발언내용은 공개, 위원 인적사항만 비공개"인 5호
        # 사안이라 core에 남긴다.
        core=("인사평가 등급", "승진후보자 명단"),
        secondary=("채용전형 결과", "연봉·성과급 등급"),
    ),
    ("5", "decision_review", DOC_TYPE_APPROVAL): LeafContentPoints(
        core=("결재라인 이견(예산·정책 이견)", "계정정보·접근권한 부여(이 doc_type의 표준형 — 7호 아님)"),
        secondary=("승인절차",),
    ),
    ("5", "decision_review", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("미확정 안건(의사결정 과정 자체 — 이 doc_type의 정의상 표준형)",),
        secondary=("참석자 의견", "반대의견"),
    ),
    ("5", "decision_review", DOC_TYPE_PRESS_RELEASE): LeafContentPoints(
        core=("내부 Q&A 대응(발표 대응문안 — 이 doc_type의 표준형)",), secondary=("배포 전 초안",)
    ),
    ("5", "decision_review", DOC_TYPE_NOTICE): LeafContentPoints(
        core=("시행일정(공표 시점 미확정)",), secondary=("이해관계자 반발 예상",)
    ),
    ("5", "decision_review", DOC_TYPE_INTERPRETATION_COMPILATION): LeafContentPoints(
        core=("상충되는 유권해석(미확정 조율)",), secondary=("소급적용 여부",)
    ),
    ("5", "technology_development", DOC_TYPE_REPORT): LeafContentPoints(
        core=("향후계획(R&D 진행상황)",), secondary=("참고자료 첨부",)
    ),
    # 6호
    ("6", "petitioner_pii", DOC_TYPE_REPLY_NOTIFICATION): LeafContentPoints(
        core=("민원인 정보(성명·연락처·주소)",), secondary=("사실관계 확인내용",)
    ),
    ("6", "personnel_pii", DOC_TYPE_PERSONNEL): LeafContentPoints(
        core=("같은 인사 필드에 담긴 개인식별정보(성명·주민등록번호, 신원 관점 — personnel_management와 같은 양식)",),
        secondary=("발령사항의 성명",),
    ),
    ("6", "personnel_pii", DOC_TYPE_REPORT): LeafContentPoints(
        core=("조사·점검 경위(인사조사, 개인식별정보 포함)",), secondary=("원인분석",)
    ),
    ("6", "welfare_pii", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("첨부자료 유무(수급자격 심사 증빙, 개인정보 포함)",), secondary=("수신·발신 기관",)
    ),
    ("6", "welfare_pii", DOC_TYPE_APPROVAL): LeafContentPoints(
        core=("승인절차(복지급여 지급 승인, 수급자 신상정보)",), secondary=("검토의견",)
    ),
    ("6", "subject_pii", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("참석자 의견(조사대상자 신원 언급 — 제보자 아님)",), secondary=("의사결정 과정",)
    ),
    # 7호
    ("7", "technology_patent", DOC_TYPE_REPORT): LeafContentPoints(
        core=("참고자료 첨부(특허출원 전 기술내용)",), secondary=("향후계획",)
    ),
    ("7", "technology_patent", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("협조요청 내용(기술이전 협상)",), secondary=("비공개 처리 표시(영업비밀)",)
    ),
    ("7", "ma_terms", DOC_TYPE_APPROVAL): LeafContentPoints(
        core=("검토의견(M&A 조건)",), secondary=("결재라인 이견",)
    ),
    ("7", "ma_terms", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("의사결정 과정(협상전략)",), secondary=("반대의견(상대방 요구조건 이견)",)
    ),
    ("7", "security_diagnosis", DOC_TYPE_REPORT): LeafContentPoints(
        core=("원인분석(보안취약점)",), secondary=("피해·실적 규모(피해 시뮬레이션)",)
    ),
    ("7", "unit_cost", DOC_TYPE_BID_NOTICE): LeafContentPoints(
        core=("규격사양(원가·납품단가 산출근거와 결합)",), secondary=("낙찰기준",)
    ),
    ("7", "business_strategy", DOC_TYPE_POLICY_MATERIAL): LeafContentPoints(
        core=("정책방향(경영전략, 시장점유 목표)",), secondary=("이해관계자 의견수렴",)
    ),
    # 8호
    ("8", "real_estate_speculation", DOC_TYPE_PLAN): LeafContentPoints(
        core=("대상지·대상자 선정기준(부지 미확정)",), secondary=("추진일정",)
    ),
    ("8", "real_estate_speculation", DOC_TYPE_POLICY_MATERIAL): LeafContentPoints(
        core=("시행일정(용도변경 시행 전)",), secondary=("정책방향",)
    ),
    ("8", "real_estate_speculation", DOC_TYPE_REPORT): LeafContentPoints(
        core=("원인분석(부지선정 미확정 타당성조사)",), secondary=("피해·실적 규모(지가변동 예측)",)
    ),
    ("8", "real_estate_speculation", DOC_TYPE_MEETING_MINUTES): LeafContentPoints(
        core=("미확정 안건(용도변경 확정 전)",), secondary=("반대의견",)
    ),
    ("8", "cornering", DOC_TYPE_OFFICIAL_DOCUMENT): LeafContentPoints(
        core=("협조요청 내용(물자수급 관계기관 협조)",), secondary=("처분 근거(가격정책)",)
    ),
    ("8", "cornering", DOC_TYPE_REPORT): LeafContentPoints(
        core=("피해·실적 규모(가격변동 예측)",), secondary=("조사·점검 경위(매점매석 의심)",)
    ),
    ("8", "cornering", DOC_TYPE_APPROVAL): LeafContentPoints(
        core=("예산집행 승인(가격정책 승인)",), secondary=("승인절차",)
    ),
}


def describe_leaf_content(clause_no: str, subclause_key: str | None, doc_type: str) -> str | None:
    """leaf의 핵심/부수 포인트를 템플릿 description 문장으로 변환한다.

    선언이 없으면 None — 호출자는 기존 기본 문구로 폴백한다.
    """
    if not subclause_key:
        return None
    points = LEAF_CONTENT_POINTS.get((str(clause_no).strip(), subclause_key, doc_type))
    if points is None:
        return None
    core = "·".join(points.core)
    if points.secondary:
        secondary = "·".join(points.secondary)
        return f"핵심 비공개 사유로 '{core}'를 다루고, '{secondary}'는 부수적으로만 곁들여라."
    return f"핵심 비공개 사유로 '{core}'를 다뤄라."
