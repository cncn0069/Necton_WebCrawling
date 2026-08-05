"""Compatibility policy for locked document-form and subclause pairs.

The generator receives two independently selected axes: the source document's
administrative form and the target legal subclause.  Some pairs are natural,
some need an explicit bridge scenario, and some cannot preserve both axes'
core meaning.  Keep that decision deterministic and outside the prose prompt.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Mapping

from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DOCUMENT_FORM_DEFINITIONS,
    DocumentForm,
    SUBCLAUSES_BY_CLAUSE,
    SUBCLAUSE_LABELS,
    SubclauseKey,
    clause_of_subclause,
)


FORM_SUBCLAUSE_COMPATIBILITY_VERSION = "form-subclause-compatibility-v1"


class FormSubclauseCompatibility(str, Enum):
    """How a locked form and target subclause may be composed."""

    NATIVE = "native"
    BRIDGE = "bridge"
    CONFLICT = "conflict"


AI = SubclauseKey.AUDIT_INSPECTION
BC = SubclauseKey.BID_CONTRACT
PM = SubclauseKey.PERSONNEL_MANAGEMENT
DR = SubclauseKey.DECISION_REVIEW
TD = SubclauseKey.TECHNOLOGY_DEVELOPMENT

PPII = SubclauseKey.PERSONNEL_PII
PET = SubclauseKey.PETITIONER_PII
SUBJ = SubclauseKey.SUBJECT_PII
WEL = SubclauseKey.WELFARE_PII

BS = SubclauseKey.BUSINESS_STRATEGY
MA = SubclauseKey.MA_TERMS
SEC = SubclauseKey.SECURITY_DIAGNOSIS
TP = SubclauseKey.TECHNOLOGY_PATENT
UC = SubclauseKey.UNIT_COST
COR = SubclauseKey.CORNERING
RE = SubclauseKey.REAL_ESTATE_SPECULATION


_TARGET_SUBCLAUSES = frozenset(
    subclause
    for clause in (
        ClauseNumber.CLAUSE_5,
        ClauseNumber.CLAUSE_6,
        ClauseNumber.CLAUSE_7,
        ClauseNumber.CLAUSE_8,
    )
    for subclause in SUBCLAUSES_BY_CLAUSE[clause]
)


# Natural pairs need no extra scenario.  Everything in neither this table nor
# the conflict table is a bridge pair and receives explicit bridge guidance.
_NATIVE_BY_FORM: Mapping[DocumentForm, frozenset[SubclauseKey]] = MappingProxyType(
    {
        DocumentForm.MEETING_MINUTES: _TARGET_SUBCLAUSES,
        # decision_review has its measured, integrated execution prompt.
        DocumentForm.OFFICIAL_LETTER: frozenset({DR}),
        DocumentForm.REPORT: frozenset(
            {DR, TD, PPII, PET, WEL, MA, TP, UC, COR, RE}
        ),
        DocumentForm.AUDIT_MATERIAL: frozenset({AI, PPII, PET, SUBJ, WEL}),
        DocumentForm.PERSONNEL_MATERIAL: frozenset({PM, PPII, SUBJ}),
        DocumentForm.BID_MATERIAL: frozenset({BC, UC}),
        DocumentForm.APPROVAL_REQUEST: frozenset(
            {AI, BC, PM, DR, TD, PPII, PET, SUBJ, WEL, MA, SEC, TP, UC, COR, RE}
        ),
        DocumentForm.REPLY_NOTICE: frozenset({PPII, PET, SUBJ, WEL}),
        DocumentForm.POLICY_MATERIAL: frozenset(),
        DocumentForm.PLAN_DRAFT: frozenset(
            {AI, DR, TD, BS, MA, TP, UC, COR, RE}
        ),
        DocumentForm.LEGAL_REVIEW: frozenset(
            {AI, BC, PM, DR, TD, PPII, PET, SUBJ, WEL, BS, MA, TP, UC, COR, RE}
        ),
        DocumentForm.INSPECTION_REPORT: frozenset({SEC}),
        DocumentForm.RESPONSE_PLAN: frozenset({PPII, WEL, SEC, COR}),
        DocumentForm.INVESTIGATION_REPORT: frozenset({PET, SUBJ}),
        DocumentForm.PRESS_RELEASE: frozenset(),
        DocumentForm.ADMINISTRATIVE_RULE: frozenset(),
        DocumentForm.OTHER: frozenset(),
    }
)


_CONFLICT_BY_FORM: Mapping[DocumentForm, frozenset[SubclauseKey]] = (
    MappingProxyType(
        {
            DocumentForm.MEETING_MINUTES: frozenset(),
            DocumentForm.OFFICIAL_LETTER: frozenset(),
            DocumentForm.REPORT: frozenset({SEC}),
            DocumentForm.AUDIT_MATERIAL: frozenset({DR, SEC}),
            DocumentForm.PERSONNEL_MATERIAL: frozenset(
                {BC, DR, TD, BS, MA, SEC, TP, UC, COR, RE}
            ),
            DocumentForm.BID_MATERIAL: frozenset(
                {AI, PM, DR, TD, PPII, PET, SUBJ, WEL, MA, RE}
            ),
            DocumentForm.APPROVAL_REQUEST: frozenset(),
            DocumentForm.REPLY_NOTICE: frozenset(),
            DocumentForm.POLICY_MATERIAL: frozenset(
                {AI, BC, PM, DR, TD, PPII, PET, SUBJ, WEL, MA, SEC}
            ),
            DocumentForm.PLAN_DRAFT: frozenset({SUBJ}),
            DocumentForm.LEGAL_REVIEW: frozenset(),
            DocumentForm.INSPECTION_REPORT: frozenset(
                {AI, BC, PM, DR, BS, MA, UC}
            ),
            DocumentForm.RESPONSE_PLAN: frozenset({AI, BC, PM}),
            DocumentForm.INVESTIGATION_REPORT: frozenset({PPII, WEL}),
            DocumentForm.PRESS_RELEASE: frozenset(
                {PPII, PET, SUBJ, WEL, SEC, TP, UC}
            ),
            DocumentForm.ADMINISTRATIVE_RULE: frozenset(
                {DR, PPII, PET, SUBJ, WEL, BS, MA, SEC, TP, UC}
            ),
            DocumentForm.OTHER: frozenset(),
        }
    )
)


if set(_NATIVE_BY_FORM) != set(DocumentForm):
    raise RuntimeError("every document form needs a native compatibility set")
if set(_CONFLICT_BY_FORM) != set(DocumentForm):
    raise RuntimeError("every document form needs a conflict compatibility set")
for _form in DocumentForm:
    _native = _NATIVE_BY_FORM[_form]
    _conflict = _CONFLICT_BY_FORM[_form]
    if not _native <= _TARGET_SUBCLAUSES or not _conflict <= _TARGET_SUBCLAUSES:
        raise RuntimeError(f"unknown target in compatibility policy for {_form.value}")
    if _native & _conflict:
        raise RuntimeError(f"overlapping compatibility policy for {_form.value}")


def form_subclause_compatibility(
    document_form: DocumentForm,
    subclause_key: SubclauseKey,
) -> FormSubclauseCompatibility:
    """Return the deterministic compatibility level for one locked pair."""

    if subclause_key not in _TARGET_SUBCLAUSES:
        raise KeyError(f"unsupported generated subclause: {subclause_key.value}")
    if subclause_key in _CONFLICT_BY_FORM[document_form]:
        return FormSubclauseCompatibility.CONFLICT
    if subclause_key in _NATIVE_BY_FORM[document_form]:
        return FormSubclauseCompatibility.NATIVE
    return FormSubclauseCompatibility.BRIDGE


_FORM_BRIDGE_GUIDANCE: Mapping[DocumentForm, str] = MappingProxyType(
    {
        DocumentForm.MEETING_MINUTES: (
            "회의의 시간순 진행·발언·의결을 본문 중심에 유지한다."
        ),
        DocumentForm.OFFICIAL_LETTER: (
            "보호 대상의 실제 내용을 먼저 전달하고 협조·회신 요구는 마지막 후속 "
            "조치에만 둔다. 자료명이나 제출 요청만으로 목표를 대신하지 않는다."
        ),
        DocumentForm.REPORT: (
            "일반 업무 결과를 보고하는 목적을 유지하고, 전문 감사·점검·수사 행위로 "
            "본문의 주목적을 바꾸지 않는다."
        ),
        DocumentForm.AUDIT_MATERIAL: (
            "업무 수행의 적정성을 판단하고 시정·처분을 요구하는 감사 목적을 본문에서 "
            "분명히 하며, 목표정보는 감사 근거 또는 지적사항으로 연결한다."
        ),
        DocumentForm.PERSONNEL_MATERIAL: (
            "채용·평정·승진·징계라는 인사 판단을 핵심 행정행위로 유지하고 목표정보를 "
            "그 판단의 직접 근거로 연결한다."
        ),
        DocumentForm.BID_MATERIAL: (
            "공고·평가·계약이라는 공공입찰 행위를 핵심으로 유지하고 목표정보를 "
            "참가조건·평가자료·계약조건에 직접 연결한다."
        ),
        DocumentForm.APPROVAL_REQUEST: (
            "승인받으려는 특정 행위와 목표정보의 관계를 품의 사유와 승인 대상에 "
            "명시한다."
        ),
        DocumentForm.REPLY_NOTICE: (
            "선행 요청을 인용하고 승인·불승인·보류 결과를 명시하되, 목표정보는 그 "
            "결정의 구체적인 근거로 직접 제시한다."
        ),
        DocumentForm.POLICY_MATERIAL: (
            "기존 제도의 운영 안내라는 목적을 유지하면서 목표정보가 일반 설명에 "
            "그치지 않도록 적용 사례 또는 비공개 붙임과 직접 연결한다."
        ),
        DocumentForm.PLAN_DRAFT: (
            "미래 사업의 목표·과업·일정·예산을 중심에 두고 목표정보를 해당 계획의 "
            "입력값이나 미확정 세부안으로 연결한다."
        ),
        DocumentForm.LEGAL_REVIEW: (
            "법적 쟁점·근거 조문·검토의견을 주된 구조로 유지하고 목표정보를 쟁점 "
            "판단에 필요한 구체적 사실로 연결한다."
        ),
        DocumentForm.INSPECTION_REPORT: (
            "시설·장비·시스템의 기술적 상태 확인을 핵심으로 유지하고 목표정보가 그 "
            "점검대상과 어떤 관계인지 명시한다."
        ),
        DocumentForm.RESPONSE_PLAN: (
            "구체적 사고·재난·위험의 단계별 대응과 역할을 중심으로 유지하고 "
            "목표정보를 발동조건 또는 대응 입력값으로 연결한다."
        ),
        DocumentForm.INVESTIGATION_REPORT: (
            "범죄 혐의·진술·증거·수사 진행을 핵심으로 유지하고 목표정보를 사건의 "
            "증거로 연결하되 불필요한 개인정보가 목표를 덮지 않게 한다."
        ),
        DocumentForm.PRESS_RELEASE: (
            "아직 배포되지 않은 초안임을 제목과 배포일시로 드러내고, 공개 전까지만 "
            "보호되는 정보와 지속적으로 비공개인 정보를 구분한다."
        ),
        DocumentForm.ADMINISTRATIVE_RULE: (
            "아직 발령·고시되지 않은 조문 초안으로 작성하고 목표정보를 적용 기준이나 "
            "절차 조문에 연결한다."
        ),
        DocumentForm.OTHER: (
            "정의된 16개 형식 밖의 구체적 형식을 원문에서 식별해 유지하고, 알려진 "
            "형식의 핵심 행정행위를 새로 만들지 않는다."
        ),
    }
)


_CLAUSE_BRIDGE_GUIDANCE: Mapping[ClauseNumber, str] = MappingProxyType(
    {
        ClauseNumber.CLAUSE_5: (
            "절차가 끝나기 전 시점과 실제 기준·의견·점수·계획 내용을 함께 적는다."
        ),
        ClauseNumber.CLAUSE_6: (
            "목표 역할의 식별 가능한 사람과 보호되는 개인속성을 같은 문장·항목·행에 "
            "직접 연결한다. 이름·직위만으로 끝내지 않는다."
        ),
        ClauseNumber.CLAUSE_7: (
            "특정 법인·단체의 실제 영업비밀과 공개 시 침해되는 정당한 이익을 직접 "
            "연결한다."
        ),
        ClauseNumber.CLAUSE_8: (
            "공표·고시 전 시점과 선점·투기 가능성을 만드는 실제 일정·물량·후보지 "
            "정보를 직접 연결한다."
        ),
    }
)


def render_form_subclause_bridge_guidance(
    document_form: DocumentForm,
    subclause_key: SubclauseKey,
) -> str:
    """Render the adapter only for pairs that need a bridge scenario."""

    level = form_subclause_compatibility(document_form, subclause_key)
    if level is not FormSubclauseCompatibility.BRIDGE:
        return ""
    clause = clause_of_subclause(subclause_key)
    return "\n".join(
        (
            f"[조합 연결 규칙: {document_form.value} × {subclause_key.value}]",
            "이 조합은 연결 맥락 없이 두 지시를 병렬로 적용하면 형식 또는 목표가 "
            "이탈한다. 표제부만으로 형식을 주장하지 말고 다음 두 조건을 본문에서 "
            "동시에 구현한다.",
            f"- {_FORM_BRIDGE_GUIDANCE[document_form]}",
            f"- {_CLAUSE_BRIDGE_GUIDANCE[clause]}",
        )
    )


def render_form_conflict_guidance() -> str:
    """판별기에게 형식별 **금지** 세부유형만 보여준다.

    판별기는 문서형식과 세부유형을 한 번의 호출에서 함께 정하므로, 형식이
    잠긴 뒤에 필터를 거는 생성기(``render_form_subclause_bridge_guidance``)와
    달리 목록 전체를 미리 받아야 한다.

    native/bridge는 싣지 않는다. 272셀 중 충돌은 일부이고, 허용 목록을 실으면
    같은 정보를 훨씬 긴 표로 두 번 말하는 셈이 된다. 무엇보다 taxonomy가 이미
    "무엇을 고르는가"를 담고 있어 여기서 더할 것은 "무엇을 고르지 않는가"뿐이다.
    """

    lines = [
        "[문서형식별 선택 금지 세부유형]",
        "아래는 그 문서형식으로는 성립할 수 없는 세부유형이다. 문서형식을 먼저 "
        "고른 뒤, 그 형식의 금지 목록에 있는 것은 primary_subclause로 고르지 "
        "않는다. 목록에 없는 형식은 제한이 없다.",
    ]
    for document_form in DocumentForm:
        conflicts = _CONFLICT_BY_FORM[document_form]
        if not conflicts:
            continue
        names = ", ".join(
            f"{SUBCLAUSE_LABELS[subclause]}({subclause.value})"
            for subclause in sorted(conflicts, key=lambda key: key.value)
        )
        label = DOCUMENT_FORM_DEFINITIONS[document_form].label
        lines.append(f"- {document_form.value} ({label}): {names}")
    return "\n".join(lines)


def compatibility_counts() -> Mapping[FormSubclauseCompatibility, int]:
    """Expose an auditable 272-cell policy summary."""

    counts = {level: 0 for level in FormSubclauseCompatibility}
    for document_form in DocumentForm:
        for subclause_key in _TARGET_SUBCLAUSES:
            counts[form_subclause_compatibility(document_form, subclause_key)] += 1
    return MappingProxyType(counts)


if sum(compatibility_counts().values()) != len(DocumentForm) * len(
    _TARGET_SUBCLAUSES
):
    raise RuntimeError("compatibility policy must classify every form/subclause pair")
