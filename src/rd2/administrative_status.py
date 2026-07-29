"""법적 조항과 독립적인 행정상태 및 결정론적 문서 표기 정책."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AdminStatus(str, Enum):
    APPROVAL_PENDING = "결재진행중"
    RELEASE_NOT_DUE = "공개예정일미도래"
    DRAFT = "초안"
    INTERNAL_REVIEW = "내부검토중"
    ATTACHMENT_MISSING = "첨부미등록"
    DISCLOSURE_REVIEW = "공개심사중"
    AGENCY_CONSULT = "타기관협의중"
    DEIDENTIFY_PENDING = "비식별처리중"
    SYSTEM_REGISTRATION_ERROR = "시스템등록오류"
    DOCUMENT_DISPOSITION = "문서정리중"
    PETITION_IN_PROGRESS = "민원처리중"
    AUDIT_IN_PROGRESS = "감사진행중"


@dataclass(frozen=True)
class AdministrativeStatusTextPolicy:
    """P1이 자연문으로 실현할 관찰 가능한 상태 문구와 모순 문구."""

    label: str
    detail: str
    forbidden_phrases: tuple[str, ...] = ()


ADMIN_STATUS_TEXT_POLICIES: dict[AdminStatus, AdministrativeStatusTextPolicy] = {
    AdminStatus.APPROVAL_PENDING: AdministrativeStatusTextPolicy(
        label="결재 진행 중",
        detail="담당자 기안 완료, 검토 진행 중, 최종 결재 미완료",
        forbidden_phrases=("결재 완료", "최종 승인"),
    ),
    AdminStatus.RELEASE_NOT_DUE: AdministrativeStatusTextPolicy(
        label="공개 예정일 미도래",
        detail="공개 예정일 이전으로 현재 대외 공개 전 상태",
        forbidden_phrases=("공개 완료", "공개 시행"),
    ),
    AdminStatus.DRAFT: AdministrativeStatusTextPolicy(
        label="초안",
        detail="현재 초안으로 작성 중이며 내용이 확정되지 않은 상태",
        forbidden_phrases=("최종 확정", "시행 완료"),
    ),
    AdminStatus.INTERNAL_REVIEW: AdministrativeStatusTextPolicy(
        label="내부 검토 중",
        detail="담당 부서 내부 검토가 진행 중이며 검토 결과는 미확정",
        forbidden_phrases=("검토 완료", "최종 확정"),
    ),
    AdminStatus.ATTACHMENT_MISSING: AdministrativeStatusTextPolicy(
        label="첨부 미등록",
        detail="본문에 표시된 붙임 자료는 현재 시스템에 등록되지 않은 상태",
        forbidden_phrases=("첨부 등록 완료",),
    ),
    AdminStatus.DISCLOSURE_REVIEW: AdministrativeStatusTextPolicy(
        label="공개 심사 중",
        detail="정보공개 범위와 비공개 대상 부분에 대한 심사가 진행 중",
        forbidden_phrases=("공개 여부 확정",),
    ),
    AdminStatus.AGENCY_CONSULT: AdministrativeStatusTextPolicy(
        label="타기관 협의 중",
        detail="관계기관 의견 조회와 협의가 진행 중이며 회신 결과는 미확정",
        forbidden_phrases=("관계기관 협의 완료",),
    ),
    AdminStatus.DEIDENTIFY_PENDING: AdministrativeStatusTextPolicy(
        label="비식별 처리 중",
        detail="개인 식별정보의 비식별 처리가 완료되지 않아 외부 제공 전 상태",
        forbidden_phrases=("비식별 처리 완료",),
    ),
    AdminStatus.SYSTEM_REGISTRATION_ERROR: AdministrativeStatusTextPolicy(
        label="시스템 등록 오류",
        detail="전자문서 시스템 등록 오류로 정상 등록 여부를 확인 중",
        forbidden_phrases=("시스템 등록 완료",),
    ),
    AdminStatus.DOCUMENT_DISPOSITION: AdministrativeStatusTextPolicy(
        label="문서 정리 중",
        detail="문서 정비·분류·보존 여부 검토가 진행 중",
        forbidden_phrases=("문서 정리 완료",),
    ),
    AdminStatus.PETITION_IN_PROGRESS: AdministrativeStatusTextPolicy(
        label="민원 처리 중",
        detail="민원 사실관계 확인과 처리 절차가 진행 중이며 결과는 미확정",
        forbidden_phrases=("민원 처리 완료",),
    ),
    AdminStatus.AUDIT_IN_PROGRESS: AdministrativeStatusTextPolicy(
        label="감사 진행 중",
        detail="감사·조사·점검이 진행 중이며 지적사항과 처분 결과는 미확정",
        forbidden_phrases=("감사 완료", "처분 확정"),
    ),
}

if set(ADMIN_STATUS_TEXT_POLICIES) != set(AdminStatus):
    raise RuntimeError("every administrative status requires a text policy")
