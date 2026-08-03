"""판별 결과와 목표 세부조항만으로 ``[SENSITIVE SEED]``를 **기계적으로** 조립한다.

**왜 코드가 만드는가.** 이전에는 seed가 사람이 손으로 쓴 문자열이었다(배치
스크립트의 상수, preview 스크립트의 ``CLAUSE_CASES``). 그래서 두 문제가 있었다.

1. 같은 seed를 여러 원문에 재사용하면 생성물이 같은 숫자를 그대로 뱉는다 —
   실측에서 서로 무관한 두 원문이 모두 ``428,000,000원``을 썼다. 학습 데이터의
   다양성이 seed 개수에 묶인다.
2. seed가 원문과 무관하게 정해지므로 "왜 이 조항인가"에 답할 수 없다.

이 모듈은 **이미 잠긴 입력**만 재료로 쓴다 — 목표 세부조항의 taxonomy 정의와
판별기가 그 원문에서 실제로 관찰한 업무 맥락·역할·슬롯이다. 따라서 원문마다
다른 seed가 나오고, 같은 입력이면 항상 같은 seed가 나온다. LLM 호출은 없다.

**구체적 값은 넣지 않는다.** 금액·점수·번호는 어차피 가상이어야 하고 생성기
프롬프트가 이미 "원문에 없는 새 가상 값으로 작성"하라고 지시한다. seed는
**무엇이 필요한지**만 정하고 값 채우기는 생성기에 맡긴다 — 그래야 같은
세부조항이라도 원문마다 다른 값이 나온다.
"""

from __future__ import annotations

from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSE_DEFINITIONS,
    SUBCLAUSE_LABELS,
)
from rd2.source_generation.contracts import (
    GenerationTarget,
    SourceAssessment,
    SourceSlotKind,
)

#: seed 문구의 버전. 조립 규칙이 바뀌면 올린다 — planner policy hash에 실려
#: 과거 계획을 무효화한다.
SEED_ASSEMBLY_VERSION = "mechanical-seed-assembly-v1"

_SLOT_KIND_LABELS = {
    SourceSlotKind.PARAGRAPH: "문단",
    SourceSlotKind.TABLE_COLUMN: "표 열",
    SourceSlotKind.KEY_VALUE: "key-value 항목",
    SourceSlotKind.ATTACHMENT: "붙임",
}


def build_sensitive_seed(
    assessment: SourceAssessment,
    target: GenerationTarget,
) -> str | None:
    """목표 세부조항에 필요한 내용을 원문 관찰값과 엮어 지시문으로 만든다.

    조항이 없는 목표(행정상태 전용)는 ``None``을 돌려준다 — 심을 법적 내용이
    없으므로 seed도 없다.
    """

    if target.subclause_key is None:
        return None

    definition = SUBCLAUSE_DEFINITIONS[target.subclause_key]
    label = SUBCLAUSE_LABELS[target.subclause_key]

    lines = [
        f"목표 세부유형: {label}({target.subclause_key.value})",
        f"이 세부유형의 판정 기준: {definition.definition}",
        "",
        "아래 내용을 원문에 없는 새 가상 값으로 구체적으로 작성한다. 항목마다 "
        "실제 형식의 수치·일자·식별자를 만들어 넣고, 값 없이 항목명만 나열하지 "
        "않는다.",
    ]
    lines.extend(f"- {item}" for item in definition.includes)

    lines.append("")
    lines.append(f"원문 업무 맥락(이 배경을 유지한다): {assessment.business_context}")

    roles = ", ".join(role.value for role in assessment.subject_roles)
    lines.append(
        f"원문에 실제로 등장하는 역할(여기서 벗어난 사람을 새로 끼워넣지 "
        f"않는다): {roles}"
    )

    if assessment.available_slots:
        lines.append("")
        lines.append("재사용할 원문 자리(의미를 유지하고 값만 새로 채운다):")
        for slot in assessment.available_slots:
            kind = _SLOT_KIND_LABELS.get(slot.kind, slot.kind.value)
            lines.append(f"- [{kind}] {slot.name}")

    lines.append("")
    lines.append(
        "공개 시 이 세부유형의 보호 대상에 생길 구체적인 침해·지장 우려가 "
        "본문에서 확인되게 한다. 다음 경우는 이 세부유형이 아니므로 그렇게 "
        "쓰지 않는다."
    )
    lines.extend(f"- {item}" for item in definition.excludes)

    return "\n".join(lines)
