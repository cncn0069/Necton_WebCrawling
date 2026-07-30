"""생성문이 실제 공문의 형식 요소를 갖췄는지 검사한다.

**왜 필요한가.** 실문서 50건 실행에서 P2는 완료된 37건 중 35건을
``document_type=other``로 판정했다. P1은 같은 문서를 ``bid_notice``,
``policy_material``로 구체적으로 분류했는데도 그렇다. 생성물을 열어 보면
이유가 분명하다 — 본문 300~400자의 줄글뿐이고, 실제 공문이라면 반드시 있는
문서번호·수신·시행일자·결재란·붙임이 **글자로 존재하지 않는다.** 채점자가
"무슨 문서인지 모르겠다"고 답하는 게 정직한 반응인 상태다.

**왜 PDF 렌더러가 아니라 여기인가.** P2는 렌더링 결과가 아니라
``GeneratedDocumentIR``을 채점한다. 형식 요소를 PDF 템플릿이 그려 넣으면
보기에는 공문이 되지만 그 내용은 IR에 없으므로 아무도 채점하지 않는다.
그러면 "결재진행중" 같은 라벨의 유일한 근거가 검증 범위 밖에 생긴다.
RD-2의 산출물은 PDF가 아니라 **라벨이 붙은 학습 데이터**이므로, 라벨의 근거는
채점 가능한 곳에 있어야 한다. 렌더러는 IR block을 공문 레이아웃에 배치하는
순수 렌더링으로 남긴다.

이 모듈은 **판정하고 기록만** 한다. hard failure로 올리는 것은 실측으로
준수율을 확인한 뒤에 결정한다 — 오늘 이미 계약 위반이 주된 실패 원인인
상태에서 게이트를 하나 더 얹으면 성공률만 떨어진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from rd2.administrative_status import AdminStatus
from rd2.source_generation.classification_taxonomy import (
    DOCUMENT_FORM_DEFINITIONS,
    DocumentForm,
)
from rd2.source_generation.contracts import (
    GeneratedDocumentIR,
    GenerationTarget,
)

#: 문서 머리에 들어가는 행정 식별 정보. ``key_value`` block으로 표현한다.
#: 시행문 계열의 표제부이며, 문서형식을 모를 때의 기본값이다.
HEADER_KEYS: tuple[str, ...] = ("문서번호", "수신", "시행일자")

#: 문서형식별 표제부 항목.
#:
#: **왜 형식별인가.** 시행문의 문서번호·수신·시행일자를 모든 형식에 요구하면
#: 회의록·보도자료·수사보고서처럼 실무에서 그 항목을 쓰지 않는 문서가 전부
#: "머리 정보 부족"으로 기록된다. ``GENERATOR_SYSTEM_PROMPT``는 원문 문서형식을
#: 그대로 쓰고 공문으로 바꾸지 말라고 지시하므로, 검사가 시행문 하나만 인정하면
#: 프롬프트와 지표가 반대 방향을 가리킨다 — 준수율을 보고 hard failure로 올릴지
#: 판단하려는 이 지표의 목적 자체가 무너진다.
#:
#: 생성기 프롬프트가 이 표를 그대로 렌더링해 받는다
#: (``render_header_key_guidance``). 프롬프트와 검사가 같은 출처를 쓰게 하는
#: 것이 요점이므로, 여기만 고치고 프롬프트를 따로 손보지 않는다.
#:
#: ``DocumentForm``을 직접 순회하므로 형식이 추가되고 표제부가 빠지면 import
#: 시점에 ``KeyError``로 드러난다.
HEADER_KEYS_BY_FORM: Mapping[DocumentForm, tuple[str, ...]] = MappingProxyType(
    {
        DocumentForm.MEETING_MINUTES: ("회차", "개최일시", "장소", "참석자"),
        DocumentForm.OFFICIAL_LETTER: ("문서번호", "수신", "시행일자"),
        DocumentForm.REPORT: ("문서번호", "수신", "시행일자", "보고일자"),
        DocumentForm.AUDIT_MATERIAL: ("문서번호", "감사기간", "감사대상", "감사반"),
        DocumentForm.PERSONNEL_MATERIAL: ("문서번호", "수신", "시행일자"),
        DocumentForm.BID_MATERIAL: ("공고번호", "공고일자", "입찰방법", "수요기관"),
        DocumentForm.APPROVAL_REQUEST: ("문서번호", "수신", "시행일자"),
        DocumentForm.REPLY_NOTICE: ("문서번호", "수신", "시행일자", "관련"),
        DocumentForm.POLICY_MATERIAL: ("문서번호", "담당부서", "시행일자"),
        DocumentForm.PLAN_DRAFT: ("문서번호", "수신", "시행일자"),
        DocumentForm.LEGAL_REVIEW: ("문서번호", "수신", "시행일자", "검토의뢰"),
        DocumentForm.INSPECTION_REPORT: ("문서번호", "점검일시", "점검대상", "점검자"),
        DocumentForm.RESPONSE_PLAN: ("문서번호", "수신", "시행일자"),
        DocumentForm.INVESTIGATION_REPORT: ("사건번호", "작성일자", "조사대상자", "소속"),
        DocumentForm.PRESS_RELEASE: ("배포일시", "담당부서", "담당자", "연락처"),
        DocumentForm.ADMINISTRATIVE_RULE: ("고시번호", "시행일자", "소관부서"),
        DocumentForm.OTHER: HEADER_KEYS,
    }
)

#: 표제부로 인정하는 최소 항목 수. 항목 수가 이보다 적은 형식은 전부 요구한다.
MIN_HEADER_KEYS = 2


def header_keys_for(document_form: DocumentForm | None) -> tuple[str, ...]:
    """해당 문서형식의 표제부 항목. 형식을 모르면 시행문 기본값."""

    if document_form is None:
        return HEADER_KEYS
    return HEADER_KEYS_BY_FORM[document_form]


def render_header_key_guidance() -> str:
    """문서형식별 표제부를 프롬프트 섹션으로 렌더링한다."""

    lines = ["[문서형식별 표제부]"]
    for form in DocumentForm:
        label = DOCUMENT_FORM_DEFINITIONS[form].label
        keys = " / ".join(HEADER_KEYS_BY_FORM[form])
        lines.append(f"- {form.value} ({label}): {keys}")
    lines.append("")
    lines.append(
        f"위 항목 중 원문에 실제로 있는 것을 그대로 쓰고 최소 {MIN_HEADER_KEYS}개를 "
        "채운다. 원문 표제부에 목록에 없는 항목이 더 있으면 함께 유지한다."
    )
    return "\n".join(lines)

#: 결재란을 나타내는 ``table`` block의 열 이름 후보.
APPROVAL_COLUMNS: tuple[str, ...] = ("기안", "검토", "결재")

#: 형식 요소가 특히 중요한 행정상태 — 그 상태의 근거가 곧 형식 요소다.
STATUS_REQUIRES_APPROVAL_BLOCK: frozenset[AdminStatus] = frozenset(
    {AdminStatus.APPROVAL_PENDING, AdminStatus.DRAFT}
)
STATUS_REQUIRES_ATTACHMENT_BLOCK: frozenset[AdminStatus] = frozenset(
    {AdminStatus.ATTACHMENT_MISSING}
)


@dataclass(frozen=True)
class DocumentFormReport:
    """어떤 형식 요소가 있고 없는지. 판정이 아니라 관찰 기록이다."""

    has_header: bool
    has_approval_block: bool
    has_attachment_block: bool
    missing: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.missing


def _key_value_keys(document: GeneratedDocumentIR) -> set[str]:
    keys: set[str] = set()
    for block in document.blocks:
        if block.kind == "key_value":
            keys.update(entry.key for entry in block.entries)
    return keys


def _has_approval_table(document: GeneratedDocumentIR) -> bool:
    for block in document.blocks:
        if block.kind != "table":
            continue
        joined = " ".join(block.columns)
        if sum(1 for name in APPROVAL_COLUMNS if name in joined) >= 2:
            return True
    return False


def check_document_form(
    document: GeneratedDocumentIR,
    target: GenerationTarget,
    *,
    document_form: DocumentForm | None = None,
) -> DocumentFormReport:
    """생성문에 그 문서형식의 서식 요소가 있는지 본다.

    ``document_form``은 원문의 문서형식이다 — 생성문은 그 형식을 그대로 쓰므로
    기대 표제부도 형식에서 나온다. 넘기지 않으면 시행문 기본값으로 본다.

    머리 정보는 모든 문서에 요구하고, 결재란과 붙임은 그것이 곧 근거가 되는
    행정상태가 지정됐을 때만 요구한다 — 관계없는 문서에 결재란을 강제하면
    그것대로 현실에 없는 형태가 된다.
    """

    expected_header = header_keys_for(document_form)
    keys = _key_value_keys(document)
    present_header = [name for name in expected_header if name in keys]
    has_header = len(present_header) >= min(MIN_HEADER_KEYS, len(expected_header))

    has_approval = _has_approval_table(document)
    has_attachment = any(
        block.kind == "attachment_reference" for block in document.blocks
    )

    missing: list[str] = []
    if not has_header:
        absent = [name for name in expected_header if name not in keys]
        missing.append("문서 머리 정보 부족: " + ", ".join(absent))
    if not has_approval and any(
        status in STATUS_REQUIRES_APPROVAL_BLOCK
        for status in target.administrative_statuses
    ):
        missing.append("결재란 table block 없음")
    if not has_attachment and any(
        status in STATUS_REQUIRES_ATTACHMENT_BLOCK
        for status in target.administrative_statuses
    ):
        missing.append("붙임 attachment_reference block 없음")

    return DocumentFormReport(
        has_header=has_header,
        has_approval_block=has_approval,
        has_attachment_block=has_attachment,
        missing=tuple(missing),
    )
