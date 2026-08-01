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
    DRAFT_BLANK_HEADER_KEYS,
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
        # ``other``는 시행문 폴백이 아니라 정의된 16개 형식 **밖**의 형식이다.
        # 빈 tuple은 "원문에서 식별된 형식의 표제부를 동적으로 사용"한다는
        # sentinel이며, 아래 렌더러와 검사기가 별도로 처리한다.
        DocumentForm.OTHER: (),
    }
)

#: 표제부로 인정하는 최소 항목 수. 항목 수가 이보다 적은 형식은 전부 요구한다.
MIN_HEADER_KEYS = 2


def header_keys_for(document_form: DocumentForm | None) -> tuple[str, ...]:
    """해당 문서형식의 표제부 항목. 형식을 모르면 시행문 기본값."""

    if document_form is None:
        return HEADER_KEYS
    return HEADER_KEYS_BY_FORM[document_form]


def render_header_key_guidance(
    document_form: DocumentForm | None = None,
) -> str:
    """문서형식별 표제부를 프롬프트 섹션으로 렌더링한다.

    ``document_form``을 주면 그 형식 하나의 표제부만 보여준다 —
    ``header_keys_for``를 그대로 재사용한다. generator는 이미 잠긴 형식
    하나만 알면 되므로 17개 형식을 전부 나열할 이유가 없다. ``None``이면
    기존처럼 전체 형식을 나열한다.
    """

    lines = ["[문서형식별 표제부]"]
    forms = (document_form,) if document_form is not None else tuple(DocumentForm)
    for form in forms:
        label = DOCUMENT_FORM_DEFINITIONS[form].label
        if form is DocumentForm.OTHER:
            keys = "원문에서 식별되는 목록 밖 형식의 표제부 항목"
        else:
            keys = " / ".join(header_keys_for(form))
        lines.append(f"- {form.value} ({label}): {keys}")
    lines.append("")
    lines.append(
        f"위 항목 중 원문에 실제로 있는 것을 그대로 쓰고 최소 {MIN_HEADER_KEYS}개 "
        "항목을 둔다. 해당 형식의 표제부에 문서번호·시행일자가 있고 초안이면 "
        "그 항목은 남기되 값을 비운다. "
        "원문 표제부에 목록에 없는 항목이 더 있으면 함께 유지한다."
    )
    return "\n".join(lines)


def render_generator_form_section(document_form: DocumentForm) -> str:
    """생성기가 받는 **문서형식 절 하나**. 정의·표제부·본문 구성이 한 덩어리다.

    이전에는 같은 문서형식 얘기가 세 절로 흩어져 있었다 — 판별용 정의
    (``render_document_form_guidance``), 생성 상세
    (``render_generation_detail_guidance``), 표제부
    (``render_header_key_guidance``). 세 함수에서 왔다는 것이 유일한 이유였고,
    생성기 입장에서는 전부 "지금 쓸 이 형식 하나"에 대한 지시라 나눌 근거가
    없었다. 게다가 판별용 ``포함:``은 "이런 게 있으면 이 형식이다"라는 **증거**
    목록인데 생성기에게는 "이런 걸 넣어라"로 읽혀야 해서, 같은 데이터를 그대로
    붙이면 뜻이 어긋난다.

    데이터 출처는 그대로 둔다 — ``HEADER_KEYS_BY_FORM``은 ``check_document_form``
    이 검사에 쓰는 바로 그 표이고, 정의·생성 상세·필수요소·변형 목록은
    ``DOCUMENT_FORM_DEFINITIONS``다. 판별용 ``includes``는 사용하지 않는다.
    """

    definition = DOCUMENT_FORM_DEFINITIONS[document_form]
    keys = header_keys_for(document_form)
    if document_form is DocumentForm.OTHER:
        header_lines = (
            "표제부 항목: 원문에서 식별되는 목록 밖 형식의 항목",
            f"- 첫 block은 그 형식의 실제 표제부를 담은 key_value로 시작하고 최소 "
            f"{MIN_HEADER_KEYS}개 항목을 둔다. 신청서·접수대장·확인서에 공문 전용 "
            "문서번호·수신·시행일자를 새로 강제하지 않는다.",
            "- 형식 자체를 식별할 수 없으면 자료명·작성일처럼 중립적인 항목으로 "
            "최소 표제부를 구성한다.",
        )
    else:
        header_lines = (
            f"표제부 항목: {' / '.join(keys)}",
            f"- 첫 block은 위 항목을 담은 key_value로 시작하고, 최소 {MIN_HEADER_KEYS}개 "
            "항목을 둔다. 위 표제부에 문서번호·시행일자가 있고 초안이면 그 항목은 "
            "남기되 값을 비운다. 원문 표제부에 다른 항목이 더 있으면 함께 유지한다.",
            '- 문서번호를 쓰는 형식이면 "부서명-일련번호" 형식으로 적는다.',
        )
    lines = [
        f"[이 문서의 형식: {document_form.value} ({definition.label})]",
        definition.definition,
        "",
        *header_lines,
        "",
        f"본문 구성: {definition.generation_detail}",
        "",
        "이 형식이면 항상 필요한 요소:",
    ]
    lines.extend(f"- {item}" for item in definition.required_elements)
    lines.extend(
        (
            "",
            "원문 업무와 목표 조항에 맞는 변형을 아래에서 하나만 선택한다. "
            "서로 다른 변형을 한 문서에 섞지 않는다:",
        )
    )
    lines.extend(f"- {item}" for item in definition.variant_patterns)
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


def _key_value_values(document: GeneratedDocumentIR) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for block in document.blocks:
        if block.kind == "key_value":
            for entry in block.entries:
                values.setdefault(entry.key, []).append(entry.value)
    return {key: tuple(items) for key, items in values.items()}


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
    values_by_key = _key_value_values(document)
    present_header = [name for name in expected_header if name in values_by_key]
    if document_form is DocumentForm.OTHER:
        first_block = document.blocks[0]
        has_header = (
            first_block.kind == "key_value"
            and len(first_block.entries) >= MIN_HEADER_KEYS
        )
    else:
        has_header = len(present_header) >= min(
            MIN_HEADER_KEYS, len(expected_header)
        )

    is_draft = AdminStatus.DRAFT in target.administrative_statuses
    if document_form is DocumentForm.OTHER:
        # ``other``의 표제부 키는 원문 형식에서 동적으로 온다. 그 형식이 실제로
        # 문서번호·시행일자를 쓰는 경우에만 공란 규칙을 적용하고, 신청서 등에
        # 존재하지 않는 시행문 키를 새로 요구하지 않는다.
        blankable_header_keys = tuple(
            key for key in DRAFT_BLANK_HEADER_KEYS if key in values_by_key
        )
    else:
        blankable_header_keys = tuple(
            key for key in expected_header if key in DRAFT_BLANK_HEADER_KEYS
        )
    header_value_errors: list[str] = []
    if is_draft:
        for key in blankable_header_keys:
            values = values_by_key.get(key)
            if values is None:
                header_value_errors.append(f"초안 표제부 {key} 항목 없음")
            elif any(value for value in values):
                header_value_errors.append(f"초안 표제부 {key} 값은 공란이어야 함")
    else:
        for key in DRAFT_BLANK_HEADER_KEYS:
            if any(not value for value in values_by_key.get(key, ())):
                header_value_errors.append(f"확정 문서 표제부 {key} 값이 비어 있음")

    has_approval = _has_approval_table(document)
    has_attachment = any(
        block.kind == "attachment_reference" for block in document.blocks
    )

    missing: list[str] = []
    if not has_header:
        if document_form is DocumentForm.OTHER:
            missing.append("목록 밖 형식의 첫 key_value 표제부 항목 2개 미만")
        else:
            absent = [name for name in expected_header if name not in values_by_key]
            missing.append("문서 머리 정보 부족: " + ", ".join(absent))
    missing.extend(header_value_errors)
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
