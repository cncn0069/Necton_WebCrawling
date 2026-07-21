"""조항×문서유형 조합별 문서 템플릿 선언 (2026-07-16 시나리오 부조화 논의).

배경: pdf_render._build_approval_box가 부서명 키워드만으로 "담당-검토-결재"
결재란을 항상 완결 상태로 그려서, 제5호(의사결정·내부검토 **과정에 있는**
사항) 문서인데 결재가 다 끝난 것처럼 보이는 모순이 발견됐다. 요소를
키워드 조건으로 덧붙이는 방식으로는 "이 조합에선 이 요소가 이 상태여야
한다"는 제약을 표현할 수 없어서, clause_data.py와 같은 데이터 선언
방식으로 조합별 구조 요소를 관리한다.

템플릿은 생성 명세이자 검수 체크리스트다 — render_document_pdf는
approval_state를 보고 결재란을 그리고, validate_row는 같은 선언으로
생성된 row의 모순(금지 문구 등)을 잡는다.

실제 문서 구조를 참고한 전용 명세와 전체 목표 매트릭스의 기본 변형을 함께
관리한다. 전용 명세가 없는 조합은 조항·문서유형별 공통 본문 골격을 사용하므로,
실물 원본 대조 여부는 TEMPLATE_SOURCE_MAP.md에서 별도로 추적한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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
from rd2.generators.template_matrix import TEMPLATE_TARGETS

# recipient 필드의 센티널 값 — 렌더러가 row별 합성 민원인 성명("OOO 귀하")으로
# 치환한다. 수신자 성명 자체가 개인정보인 제6호 회신 문서 전용.
RECIPIENT_CIVIL_PETITIONER = "@민원인"


class ApprovalState(str, Enum):
    """결재란 상태 — 템플릿이 없는 조합은 기존 부서명 키워드 동작을 유지한다."""

    NONE = "none"  # 결재란 없음
    PENDING = "pending"  # 담당만 기재, 검토/결재는 공란 (내부검토 진행중)
    COMPLETE = "complete"  # 3단 전부 기재 (기존 동작)


class AdminStatus(str, Enum):
    """행정 상태 축 — 조항(제9조) 축과 별개로 문서에 얹히는 상태 변형.

    제9조 1~8호 어느 문서에도 결합할 수 있는 독립 메타데이터다. 일부 상태는
    결재선·본문에 시각적으로 드러나고, 시스템 등록 오류처럼 시스템 메타데이터로
    관리되는 상태도 있다. 값은 CSV/DB의 document_status 컬럼에 그대로 쓰이며
    파일명에는 절대 반영하지 않는다.
    """

    APPROVAL_PENDING = "결재진행중"
    RELEASE_NOT_DUE = "공개예정일미도래"
    DRAFT = "초안"  # 3: 문서 미완성/초안
    INTERNAL_REVIEW = "내부검토중"
    ATTACHMENT_MISSING = "첨부미등록"  # 5: 붙임 문구는 있는데 첨부 실물 없음
    DISCLOSURE_REVIEW = "공개심사중"
    AGENCY_CONSULT = "타기관협의중"  # 7: 타 기관 의견 회신 대기
    DEIDENTIFY_PENDING = "비식별처리중"  # 8: PII 비식별 처리 전 원본
    SYSTEM_REGISTRATION_ERROR = "시스템등록오류"
    DOCUMENT_DISPOSITION = "문서정리중"
    PETITION_IN_PROGRESS = "민원처리중"  # 11: 민원 사실확인·조사 중
    AUDIT_IN_PROGRESS = "감사진행중"


@dataclass(frozen=True)
class StatusVariantSpec:
    """행정 상태 하나가 문서에 요구하는 요소와 금지 문구 선언.

    applies_to가 None이면 모든 템플릿에 적용 가능, 아니면 해당 template_id
    목록에만 적용된다(다른 조합에 붙이면 validate_row가 위반으로 보고).
    overrides_template_forbidden: 이 상태에서는 예외적으로 허용되는 템플릿
    기본 금지 문구 — 예: T6-1은 "비식별 처리" 문구를 금지하지만(마스킹 전
    원본 시나리오), 상태가 "비식별처리중"이면 그 문구가 오히려 필요하다.
    """

    status: AdminStatus
    applies_to: tuple[str, ...] | None
    forbidden_phrases: tuple[str, ...] = ()
    overrides_template_forbidden: tuple[str, ...] = ()
    description: str = ""


STATUS_VARIANTS: dict[str, StatusVariantSpec] = {
    AdminStatus.APPROVAL_PENDING.value: StatusVariantSpec(
        status=AdminStatus.APPROVAL_PENDING,
        applies_to=None,
        forbidden_phrases=("결재 완료", "최종 승인"),
        description="결재 진행중 — 결재 이력에서 최종 결재가 완료되지 않은 상태.",
    ),
    AdminStatus.RELEASE_NOT_DUE.value: StatusVariantSpec(
        status=AdminStatus.RELEASE_NOT_DUE,
        applies_to=None,
        forbidden_phrases=("공개 완료", "공개 시행"),
        description="공개 예정일 미도래 — 예정일 전에는 대국민 공개가 완료됐다는 표현을 쓰지 않는다.",
    ),
    AdminStatus.DRAFT.value: StatusVariantSpec(
        status=AdminStatus.DRAFT,
        applies_to=None,
        forbidden_phrases=("최종 확정", "시행 완료"),
        description=(
            "초안 — 제목에 (초안) 접두, 결재선 전원 공란, 본문 '끝.' 대신 "
            "[이하 작성 중] 표기. 완결 표현이 있으면 모순."
        ),
    ),
    AdminStatus.INTERNAL_REVIEW.value: StatusVariantSpec(
        status=AdminStatus.INTERNAL_REVIEW,
        applies_to=None,
        forbidden_phrases=("검토 완료", "최종 확정"),
        description="내부 검토중 — 검토 결과나 최종 확정 전의 진행 상태.",
    ),
    AdminStatus.ATTACHMENT_MISSING.value: StatusVariantSpec(
        status=AdminStatus.ATTACHMENT_MISSING,
        applies_to=None,
        forbidden_phrases=(),
        description=(
            "첨부 미등록 — 본문에 '붙임 ... 1부.' 문구를 넣되 첨부 실물은 "
            "없어야 한다. 붙임 문구↔첨부 수 대조는 메타데이터 검증(파이프라인) "
            "몫이라 본문 금지 문구는 없다."
        ),
    ),
    AdminStatus.DISCLOSURE_REVIEW.value: StatusVariantSpec(
        status=AdminStatus.DISCLOSURE_REVIEW,
        applies_to=None,
        forbidden_phrases=("공개 결정 완료", "부분공개 결정 완료", "비공개 결정 완료"),
        description="공개 심사중 — 공개·부분공개·비공개 결론을 아직 확정하지 않은 상태.",
    ),
    AdminStatus.AGENCY_CONSULT.value: StatusVariantSpec(
        status=AdminStatus.AGENCY_CONSULT,
        applies_to=("T5-1", "T5-5"),
        forbidden_phrases=("의견 회신 완료", "협의 완료"),
        description=(
            "타 기관 협의중 — 본문에 협의 대상 기관명과 '의견 조회 중' 문구가 "
            "있어야 하고, 협의가 끝났다는 표현이 있으면 모순."
        ),
    ),
    AdminStatus.DEIDENTIFY_PENDING.value: StatusVariantSpec(
        status=AdminStatus.DEIDENTIFY_PENDING,
        applies_to=("T6-1", "T6-2"),
        forbidden_phrases=("비식별 처리 완료", "마스킹 완료"),
        overrides_template_forbidden=("비식별 처리",),
        description=(
            "비식별 처리중 — PII가 마스킹 안 된 원본 상태에 '비식별 처리 예정' "
            "문구가 함께 있어야 한다(처리 전이라는 근거). 이미 처리 완료됐다는 "
            "표현이 있으면 모순."
        ),
    ),
    AdminStatus.SYSTEM_REGISTRATION_ERROR.value: StatusVariantSpec(
        status=AdminStatus.SYSTEM_REGISTRATION_ERROR,
        applies_to=None,
        description="시스템 등록 오류 — 공개 여부·분류·첨부 등록값을 시스템에서 정정 중인 상태.",
    ),
    AdminStatus.DOCUMENT_DISPOSITION.value: StatusVariantSpec(
        status=AdminStatus.DOCUMENT_DISPOSITION,
        applies_to=None,
        forbidden_phrases=("폐기 완료", "중복 정리 완료"),
        description="문서 정리중 — 폐기 예정·중복·오등록 여부를 확인 중인 상태.",
    ),
    AdminStatus.PETITION_IN_PROGRESS.value: StatusVariantSpec(
        status=AdminStatus.PETITION_IN_PROGRESS,
        applies_to=("T6-2",),
        forbidden_phrases=("최종 회신", "처리 완료"),
        description=(
            "민원 처리중 — 검토 결과 대신 '사실관계 확인 중' 처리 경과가 있어야 "
            "하고, 최종 답변·완결 표현이 있으면 모순."
        ),
    ),
    AdminStatus.AUDIT_IN_PROGRESS.value: StatusVariantSpec(
        status=AdminStatus.AUDIT_IN_PROGRESS,
        applies_to=None,
        forbidden_phrases=("감사 종결", "조사 완료"),
        description="감사·조사 진행중 — 최종 결론 또는 종결 전의 절차 상태.",
    ),
}


def find_status_variant(document_status: str | None) -> StatusVariantSpec | None:
    """row의 document_status 값에 선언된 상태 변형을 찾는다 — 없으면 None."""
    if not document_status:
        return None
    return STATUS_VARIANTS.get(str(document_status).strip())


@dataclass(frozen=True)
class DocTemplateSpec:
    """조항×문서유형 조합 하나에 필요한 구조 요소 선언.

    결재선 필드는 실제 orginl_info 수집 공문(예: "9급 공채 임용예정자
    실무수습 발령", "재해위로금 지급 규정 일부개정 발령 보고")을 육안
    검토해 확인한 관례를 따른다 — 범용 "담당/검토/결재" 라벨이 아니라
    직위명(주무관/사무관/과장...)이 그대로 컬럼이고 그 아래 행에 서명자
    성명이 들어가며, 미결재 직위의 성명 칸은 공란이다. 내부결재 문서는
    "수신" 필드가 "내부결재"이고, 하단에 시행 문서번호와 공개구분
    ("대국민공개"/"비공개")이 붙는다.

    forbidden_phrases: 이 조합의 시나리오와 모순되는 본문 문구 —
    생성 후 validate_row로 걸러낸다 (예: 내부검토중 문서에 "최종 승인").
    """

    template_id: str
    clause_no: str
    doc_type: str
    approval_state: ApprovalState
    subclause_key: str | None = None
    approval_positions: tuple[str, ...] = ("주무관", "사무관", "과장")
    approval_signed_count: int = 0  # 앞에서부터 몇 번째 직위까지 서명됐는지
    recipient: str | None = None  # "수신" 필드 값 (내부결재 문서면 "내부결재")
    disclosure_label: str | None = None  # 하단 공개구분 표기 (예: "비공개(5)")
    # 본문 골격: "standard" | "audit_interim" | "personnel_order" | "bid_review"
    #            | "unit_price" | "meeting_pending" | "personnel_eval" | "civil_reply"
    body_format: str = "standard"
    # 문서 외곽 셸. 정책자료와 회의록은 실제 원본이 표준 시행공문과 다른 독립
    # 서식을 쓰므로 본문 partial뿐 아니라 상단/하단 구성도 분리한다.
    form_format: str = "official_form"
    forbidden_phrases: tuple[str, ...] = ()
    description: str = ""


TEMPLATES: dict[tuple[str, str], DocTemplateSpec] = {
    ("1", DOC_TYPE_OFFICIAL_DOCUMENT): DocTemplateSpec(
        template_id="T1-1", clause_no="1", doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
        approval_state=ApprovalState.COMPLETE, recipient="내부결재",
        disclosure_label="비공개(1)", body_format="legal_confidential",
        description="법률 또는 다른 법률이 위임한 명령에 따라 비밀·비공개인 공문.",
    ),
    ("2", DOC_TYPE_POLICY_MATERIAL): DocTemplateSpec(
        template_id="T2-1", clause_no="2", doc_type=DOC_TYPE_POLICY_MATERIAL,
        approval_state=ApprovalState.COMPLETE, recipient="내부결재",
        disclosure_label="비공개(2)", body_format="national_security",
        description="국가안전보장·국방·통일·외교관계의 중대한 이익을 다루는 정책자료.",
    ),
    ("3", DOC_TYPE_REPORT): DocTemplateSpec(
        template_id="T3-1", clause_no="3", doc_type=DOC_TYPE_REPORT,
        approval_state=ApprovalState.COMPLETE, recipient="내부결재",
        disclosure_label="비공개(3)", body_format="public_safety",
        description="공개 시 국민의 생명·신체·재산 보호에 현저한 지장을 줄 수 있는 보고서.",
    ),
    ("4", DOC_TYPE_MEETING_MINUTES): DocTemplateSpec(
        template_id="T4-1", clause_no="4", doc_type=DOC_TYPE_MEETING_MINUTES,
        approval_state=ApprovalState.PENDING, approval_signed_count=1,
        recipient="내부결재", disclosure_label="비공개(4)",
        body_format="legal_proceeding",
        forbidden_phrases=("수사 완료", "판결 확정", "사건 종결"),
        description="진행 중인 재판·수사·공소유지·교정·보안처분 관련 회의자료.",
    ),
    ("5", DOC_TYPE_APPROVAL): DocTemplateSpec(
        template_id="T5-1",
        clause_no="5",
        doc_type=DOC_TYPE_APPROVAL,
        approval_state=ApprovalState.PENDING,
        approval_positions=("주무관", "사무관", "과장", "국장"),
        approval_signed_count=1,  # 기안자(주무관)만 서명 — 검토·결재 공란
        recipient="내부결재",
        disclosure_label="비공개(5)",
        forbidden_phrases=("결재 완료", "최종 승인", "승인 완료", "심의 확정", "검토 완료"),
        description=(
            "제5호 내부검토 과정의 승인/품의 문서 — 결재선은 기안자만 서명된 "
            "미완료 상태여야 하고, 본문에 검토가 끝났음을 뜻하는 문구가 있으면 안 된다."
        ),
    ),
    ("5", DOC_TYPE_AUDIT_RESULT): DocTemplateSpec(
        template_id="T5-2",
        clause_no="5",
        doc_type=DOC_TYPE_AUDIT_RESULT,
        approval_state=ApprovalState.PENDING,
        approval_positions=("감사담당", "감사팀장", "감사실장"),
        approval_signed_count=1,
        recipient="내부결재",
        disclosure_label="비공개(5)",
        body_format="audit_interim",
        # 지적사항 일람표·처분요구·징계는 실제 alio 감사결과보고서에서 확인한
        # "감사가 끝난 뒤"의 요소 — 진행중(제5호) 문서에 있으면 시나리오 모순이다.
        forbidden_phrases=("처분요구서 시행", "징계 의결", "처분 확정", "감사 종결", "시정 완료"),
        description=(
            "제5호 진행중 감사의 중간보고 — 감사개요(목적·기간·대상)와 진행 경과, "
            "향후 계획만 있어야 하고, 지적사항 확정·처분요구·징계 등 결과 단계 "
            "요소가 있으면 안 된다. 기존 감사결과보고서 서식(개요+지적사항 일람표)은 "
            "완결형이라 재사용할 수 없어 중간보고 골격(body_format=audit_interim)을 쓴다."
        ),
    ),
    ("6", DOC_TYPE_PERSONNEL): DocTemplateSpec(
        template_id="T6-1",
        clause_no="6",
        doc_type=DOC_TYPE_PERSONNEL,
        # 인사발령은 확정 문서 — 실제 원본("5급 공무원 인사발령" 등)도 결재선
        # 전원이 서명돼 있다. 5호(미확정)와 달리 6호의 비공개 근거는 결재
        # 상태가 아니라 본문에 그대로 남은 개인정보(성명 등)다.
        approval_state=ApprovalState.COMPLETE,
        approval_positions=("주무관", "사무관", "과장", "국장"),
        recipient="내부결재",
        disclosure_label="비공개(6)",
        body_format="personnel_order",
        # 성명이 OOO/○○○로 이미 마스킹돼 있으면 "개인정보가 포함되어
        # 비공개"라는 6호 시나리오와 모순된다 — 마스킹 전 원본이어야 한다.
        forbidden_phrases=("OOO", "○○○", "비식별 처리", "마스킹 완료"),
        description=(
            "제6호 개인정보 포함 인사발령 — 발령사항 표(소속·직급·성명·발령사항)에 "
            "성명이 마스킹 없이 그대로 있어야 하고(합성 인명), 결재선은 확정 문서답게 "
            "전원 서명 상태여야 한다."
        ),
    ),
    ("5", DOC_TYPE_BID_NOTICE): DocTemplateSpec(
        template_id="T5-3",
        clause_no="5",
        doc_type=DOC_TYPE_BID_NOTICE,
        approval_state=ApprovalState.PENDING,
        approval_signed_count=1,
        recipient="내부결재",
        disclosure_label="비공개(5)",
        body_format="bid_review",
        # 공고 게시·낙찰자 선정·계약 체결은 입찰 절차가 끝난 뒤의 표현 —
        # "공고 전 내부검토안"(제5호) 문서에 있으면 시나리오 모순이다.
        forbidden_phrases=("낙찰자 선정", "계약 체결 완료", "공고 게시 완료", "적격심사 완료"),
        description=(
            "제5호 입찰공고 전 내부검토안 — 실제 mohw 제안요청서(사전규격공개)에서 "
            "확인한 □/○ 개요체와 배점표(구분·배점비율·평가요소)를 쓰되, 평가위원 "
            "구성과 배점 기준이 (안) 상태여야 하고 낙찰·계약 등 절차 완료 표현이 "
            "있으면 안 된다."
        ),
    ),
    ("5", DOC_TYPE_MEETING_MINUTES): DocTemplateSpec(
        template_id="T5-5",
        clause_no="5",
        doc_type=DOC_TYPE_MEETING_MINUTES,
        approval_state=ApprovalState.PENDING,
        approval_signed_count=1,
        recipient="내부결재",
        disclosure_label="비공개(5)",
        body_format="meeting_pending",
        # 실제 molit 수도권정비실무위원회 회의록에서 확인한 결과 표기 관례가
        # <조건부의결>/<보류> — 5호(의사결정 과정)는 결론이 미확정이어야
        # 하므로 확정 의결 표현이 본문에 있으면 모순이다.
        forbidden_phrases=("원안 의결", "원안의결", "심의 확정", "최종 의결"),
        description=(
            "제5호 심의 진행중 위원회 회의록 — 실제 molit 회의록 관례(회의개요 "
            "ㅇ일시/참석/안건, 안건번호·안건명, 논의내용 ㅇ질문 ☞답변, 논의결과)를 "
            "따르되 논의결과가 <보류>(계속 심의)여야 하고 위원 명단은 비공개다."
        ),
    ),
    ("5", DOC_TYPE_PERSONNEL): DocTemplateSpec(
        template_id="T5-4",
        clause_no="5",
        doc_type=DOC_TYPE_PERSONNEL,
        approval_state=ApprovalState.PENDING,
        approval_signed_count=1,
        recipient="내부결재",
        disclosure_label="비공개(5)",
        body_format="personnel_eval",
        # 발령·임용 확정은 인사 의사결정이 끝난 뒤의 표현 — 평가(과정) 문서에
        # 있으면 모순. 실물 인사평가 문서는 원문공개에 올라오지 않아(확정 전
        # 폐기·비공개) 인사발령 원본의 서식 관례 + 미완료 결재선으로 설계했다.
        forbidden_phrases=("발령 확정", "임용 확정", "승진 확정", "인사위원회 의결 완료"),
        description=(
            "제5호 인사평가(후보군 검토) — 평가 개요·후보군 현황(안) 표·향후 계획 "
            "구조여야 하고, 평가기간이 열려 있어야 하며 발령·임용 확정 표현이 "
            "있으면 안 된다."
        ),
    ),
    ("6", DOC_TYPE_REPLY_NOTIFICATION): DocTemplateSpec(
        template_id="T6-2",
        clause_no="6",
        doc_type=DOC_TYPE_REPLY_NOTIFICATION,
        # 회신은 발송된 확정 문서 — 비공개 근거는 문서 상태가 아니라 수신자
        # 성명·연락처 등 민원인 개인정보다. 수신란 자체가 개인(귀하)이라는
        # 점이 내부결재 문서들과 다른 이 조합의 구조적 특징.
        approval_state=ApprovalState.COMPLETE,
        recipient=RECIPIENT_CIVIL_PETITIONER,
        disclosure_label="비공개(6)",
        body_format="civil_reply",
        forbidden_phrases=("OOO", "○○○", "비식별 처리", "마스킹 완료"),
        description=(
            "제6호 개인정보 포함 민원회신 — 수신란이 민원인 개인(합성 성명+귀하)이고 "
            "민원인 정보(성명·연락처·주소)가 마스킹 없이 있어야 한다. 실물 회신 "
            "표본은 수집 대기 중이라 표준 공문 서식(실물 확인) + 회신 관례로 설계, "
            "표본 확보 시 재검증 예정(2026-07-16)."
        ),
    ),
    ("7", DOC_TYPE_BID_NOTICE): DocTemplateSpec(
        template_id="T7-2",
        clause_no="7",
        doc_type=DOC_TYPE_BID_NOTICE,
        # 제7호의 비공개 근거는 문서의 미확정 상태가 아니라 내용(업체 단가 =
        # 영업상 비밀) 자체 — 결재는 완결이어도 시나리오와 모순이 없다.
        approval_state=ApprovalState.COMPLETE,
        recipient="내부결재",
        disclosure_label="비공개(7)",
        body_format="unit_price",
        forbidden_phrases=(),
        description=(
            "제7호 업체 납품단가 문서 — 품목·규격·수량·단가·금액 단가표에 "
            "합성 업체명과 단가가 그대로 있어야 한다(영업상 비밀이 비공개 근거)."
        ),
    ),
}


_CLAUSE_BODY_FORMAT = {
    "1": "legal_confidential", "2": "national_security", "3": "public_safety",
    "4": "legal_proceeding", "5": "standard", "6": "standard",
    "7": "standard", "8": "standard",
}
_DOC_BODY_FORMAT = {
    DOC_TYPE_AUDIT_RESULT: "audit_interim",
    DOC_TYPE_BID_NOTICE: "bid_review",
    DOC_TYPE_PERSONNEL: "personnel_order",
    DOC_TYPE_OFFICIAL_DOCUMENT: "source_official",
    DOC_TYPE_POLICY_MATERIAL: "source_policy_brief",
    DOC_TYPE_REPORT: "source_report",
    DOC_TYPE_MEETING_MINUTES: "source_meeting_record",
    DOC_TYPE_PLAN: "source_plan",
    DOC_TYPE_APPROVAL: "source_approval",
    DOC_TYPE_REPLY_NOTIFICATION: "source_notification",
    DOC_TYPE_PRESS_RELEASE: "press_release",
    DOC_TYPE_NOTICE: "notice",
    DOC_TYPE_BID_RENOTICE: "bid_renotice",
    DOC_TYPE_PUBLIC_OFFERING: "public_offering",
    DOC_TYPE_INTERPRETATION_COMPILATION: "interpretation_compilation",
    DOC_TYPE_PRE_SPEC_NOTICE: "pre_spec_notice",
}
_DOC_FORM_FORMAT = {
    DOC_TYPE_POLICY_MATERIAL: "policy_brief_form",
    DOC_TYPE_MEETING_MINUTES: "meeting_record_form",
}
def _build_template_variants() -> dict[tuple[str, str, str], DocTemplateSpec]:
    existing = {spec.template_id: spec for spec in TEMPLATES.values()}
    variants: dict[tuple[str, str, str], DocTemplateSpec] = {}
    for target in TEMPLATE_TARGETS:
        spec = existing.get(target.template_id)
        if spec is None:
            state = ApprovalState.PENDING if target.doc_type in {
                DOC_TYPE_APPROVAL, DOC_TYPE_MEETING_MINUTES, DOC_TYPE_POLICY_MATERIAL,
                DOC_TYPE_PRESS_RELEASE, DOC_TYPE_NOTICE, DOC_TYPE_BID_RENOTICE,
                DOC_TYPE_PUBLIC_OFFERING, DOC_TYPE_INTERPRETATION_COMPILATION,
                DOC_TYPE_PRE_SPEC_NOTICE,
            } else ApprovalState.COMPLETE
            spec = DocTemplateSpec(
                template_id=target.template_id, clause_no=target.clause_no,
                doc_type=target.doc_type, approval_state=state,
                subclause_key=target.subclause_key,
                approval_signed_count=1 if state is ApprovalState.PENDING else 0,
                recipient="내부결재", disclosure_label=f"비공개({target.clause_no})",
                body_format=_DOC_BODY_FORMAT.get(target.doc_type, _CLAUSE_BODY_FORMAT[target.clause_no]),
                form_format=_DOC_FORM_FORMAT.get(target.doc_type, "official_form"),
                description=f"{target.subclause_label} 관련 {target.doc_type} 전용 템플릿.",
            )
        else:
            spec = DocTemplateSpec(**{**spec.__dict__, "subclause_key": target.subclause_key})
        variants[(target.clause_no, target.subclause_key, target.doc_type)] = spec
    return variants


TEMPLATE_VARIANTS = _build_template_variants()


def find_template(
    clause_no: str, doc_type: str, subclause_key: str | None = None
) -> DocTemplateSpec | None:
    """세부조항 템플릿을 우선 조회하고, 미지정 시 기존 기본 조합으로 폴백한다."""
    clause = str(clause_no).strip()
    if subclause_key:
        return TEMPLATE_VARIANTS.get((clause, str(subclause_key).strip(), doc_type))
    return TEMPLATES.get((clause, doc_type))


def validate_row(spec: DocTemplateSpec, row: dict) -> list[str]:
    """생성된 row가 템플릿 선언(+행정 상태 변형)과 모순되지 않는지 검사한다.

    렌더링 전에 돌려서 모순 문서가 조용히 만들어지는 것을 막는다 —
    위반 목록이 비어 있으면 통과. 검수자용 체크리스트와 같은 기준을 쓴다.
    row에 document_status가 있으면 해당 상태 변형의 금지 문구도 함께
    검사하고, 상태가 이 조합에 적용 불가능하면 그 자체를 위반으로 본다.
    """
    violations: list[str] = []
    body_text = row.get("body_text") or ""

    effective_forbidden = spec.forbidden_phrases
    variant = find_status_variant(row.get("document_status"))
    if variant is not None:
        if variant.applies_to is not None and spec.template_id not in variant.applies_to:
            violations.append(
                f"[{spec.template_id}] document_status '{variant.status.value}'는 이 조합에 "
                f"적용할 수 없다 (적용 대상: {', '.join(variant.applies_to)})"
            )
        else:
            effective_forbidden = tuple(
                p for p in effective_forbidden if p not in variant.overrides_template_forbidden
            )
            for phrase in variant.forbidden_phrases:
                if phrase in body_text:
                    violations.append(
                        f"[{spec.template_id}/{variant.status.value}] 본문에 행정 상태와 "
                        f"모순되는 문구 발견: '{phrase}' — {variant.description}"
                    )

    for phrase in effective_forbidden:
        if phrase in body_text:
            violations.append(
                f"[{spec.template_id}] 본문에 시나리오와 모순되는 문구 발견: '{phrase}'"
                f" — {spec.description}"
            )
    return violations
