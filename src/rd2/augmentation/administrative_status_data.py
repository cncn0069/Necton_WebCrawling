""""행정처리" 비공개 사유 카테고리 데이터.

정보공개법 제9조 1~8호(법정 비공개 사유)와는 완전히 별개다 — 문서가 아직
확정되지 않아 절차상 공개되지 않은 상태(결재 진행중, 공개시점 미도래 등)를
다룬다. `clause_data.py`의 `ClauseDefinition`과 형태는 비슷하지만, 법정
조항 번호(`clause_no`)를 쓰지 않고 별도 타입으로 분리했다 — "clause"라는
이름/필드를 재사용하면 5~8호와 섞여서 혼동을 일으킨 적이 있다(2026-07-16,
AUGMENTATION_STATUS.md "행정처리" 절 참고).

12개 후보 유형 중 텍스트로 표현 불가능한 것들은 제외했다: "시스템 등록 오류"
(순수 시스템 상태, 문서 텍스트로 표현할 대상 자체가 없음), "첨부파일 미등록"
(본문이 아니라 메타데이터 문제), "감사·조사 진행중"(이미 정보공개법 5호와
동일 사유), "결재자 인장(도장)"(이미지 합성 문제, 단 "결재 진행중" 문구
자체는 포함).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdministrativeStatusDefinition:
    category: str  # 영문 짧은 코드 — selection의 "category" 필드에 그대로 들어감
    title: str
    description: str


ADMINISTRATIVE_STATUSES: dict[str, AdministrativeStatusDefinition] = {
    "pending_disclosure_date": AdministrativeStatusDefinition(
        category="pending_disclosure_date",
        title="공개시점 미도래",
        description="공개 예정일이 아직 도래하지 않아 그 전까지는 비공개로 취급되는 상태",
    ),
    "draft": AdministrativeStatusDefinition(
        category="draft",
        title="문서 초안",
        description="아직 최종 확정되지 않은 초안 상태로, 내용이 변경될 수 있어 정식 공개 대상이 아님",
    ),
    "internal_review": AdministrativeStatusDefinition(
        category="internal_review",
        title="내부 검토중(부서 협의중)",
        description="관계 부서 간 협의나 법률 검토가 진행 중이라 내용이 확정되지 않은 상태",
    ),
    "pending_review_committee": AdministrativeStatusDefinition(
        category="pending_review_committee",
        title="공개여부 심사 진행중",
        description="정보공개심의회 등에서 공개·부분공개·비공개 여부를 심사 중이라 결과가 나오기 전까지 비공개인 상태",
    ),
    "interagency_coordination": AdministrativeStatusDefinition(
        category="interagency_coordination",
        title="타 기관 협의 필요",
        description="공동 작성 문서이거나 다른 기관의 의견을 받아야 공개 여부를 최종 결정할 수 있는 상태",
    ),
    "deidentification_in_progress": AdministrativeStatusDefinition(
        category="deidentification_in_progress",
        title="개인정보 비식별 작업 진행중",
        description="원칙적으로 공개 대상이지만 주민번호·연락처·주소 등을 지우는 비식별화 작업이 끝나기 전까지 공개를 보류하는 상태",
    ),
    "pending_disposal": AdministrativeStatusDefinition(
        category="pending_disposal",
        title="문서 폐기·정리 대상",
        description="폐기 예정이거나 중복·오등록이 확인돼 공개 대상에서 제외 검토 중인 상태",
    ),
    "complaint_in_progress": AdministrativeStatusDefinition(
        category="complaint_in_progress",
        title="민원 처리 진행중",
        description="민원 조사나 사실관계 확인이 진행 중이라 결과가 확정될 때까지 공개하지 않는 상태",
    ),
}
