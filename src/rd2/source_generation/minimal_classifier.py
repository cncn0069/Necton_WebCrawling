"""판별기 프롬프트의 **최소판**.

``

이 모듈은 반대로 간다. 3단계 사고 과정과 두 분류 체계만 남기고, 각 항목은
**한 줄 정의**로 끝낸다. 포함·제외·경계 규칙을 주지 않는다.

**분류 체계에서 정한 것 둘.**

1. 보안유형에 ``etc``를 두지 않는다. 판별 결과가 그대로 생성 목표가 되는데
   ``etc``에는 만들 것이 없어 그 문서는 계획 단계에서 버려진다.
2. 제8호(매점매석·부동산 투기)를 넣어 16종이다. 빼면 부동산·물자 수급 문서가
   갈 곳이 없다.

**문서 형식은 묻지 않는다.** 원문에는 수집 단계에서 ``doc_type`` 라벨이 붙었고
그 라벨은 ``DOCUMENT_FORM_BY_TYPE``으로 문서 형식에 대응된다. 아는 값을 다시
맞히게 할 이유가 없다 — 형식 17종 목록(1,000자)이 주어진 형식 한 줄로 줄고
출력 필드도 하나 빠진다(3,651자 -> 약 2,590자). 판별기가 틀릴 자리도 하나
줄어든다. 그래서 ``render_minimal_classifier_system_prompt``는 형식을 **반드시**
받고, 출력 계약은 ``MinimalSourceAssessment`` 하나다.

**개인정보 포함 여부는 묻지 않는다.** 원문은 공개 문서라 개인정보 값이 애초에
없다. 그런데 "포함되어 있으면 제6호를 최우선"이라는 규칙을 주면 이름·직위만 있는
직무상 공개 정보를 보고 제6호를 고르게 된다 — 현행 판별기가 배제 규칙("공무원 외의
개인이 등장하지 않으면 제6호를 고르지 않는다")으로 막고 있는 바로 그 오탐이다.
묻지 않으면 그 규칙도 필요 없다.
"""

from __future__ import annotations

from string import Template

from pydantic import Field, model_validator

from rd2.source_generation.classification_taxonomy import (
    DOCUMENT_FORM_DEFINITIONS,
    SUBCLAUSE_DEFINITIONS,
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    ContractModel,
    NonEmptyText,
    SourceActorRole,
    SourceSlot,
)

#: v3에서 후보 제시 방식을 고쳤다. v2로 돌린 19건이 **전부 제5호**로 갔다 —
#: audit_inspection 12 / decision_review 6 / personnel_management 1, 제6·7·8호는
#: 0건이다. 이유를 문서 근거로만 보기 어렵다: 후보 16종이 호 순서대로 나열돼
#: 1~5번이 전부 제5호이고 1번이 audit_inspection인데, 선택이 정확히 그 앞머리에
#: 몰렸다. 특히 director_activity 4건은 형식상 제5~8호 전부가 열려 있는데도 4건
#: 모두 3번(decision_review)이었다.
#:
#: **후보를 형식별로 좁히지는 않는다.** 같은 doc_type에서도 문서마다 걸리는
#: 세부유형이 다르다 — 감사결과보고서가 감사 기준일 수도, 등장 인물의
#: 개인정보일 수도 있다. ``document_form_compatibility``의 native 표는
#: administrative_rule·policy_material이 빈 집합일 만큼 성글어서 필터로 쓰면
#: 멀쩡한 조합까지 막는다.
#:
#: **호도 싣지 않는다.** 잠깐 호별로 묶어 봤지만 그건 편향 하나를 다른 틀로
#: 바꾸는 것이었다 — 판별기가 내는 값은 세부유형 하나뿐이고 호는 코드가 거기서
#: 유도하므로(``clause_of_subclause``), 조문 번호는 모델이 알 필요도 없고 알면
#: "호를 먼저 고르고 그 안에서 세부유형을 고른다"는 순서만 생긴다. 남기는 것은
#: 세부유형 16종의 한 줄 정의뿐이고, 판단 근거는 원문의 업무와 주체다.
#:
#: 번호도 뗐다. 순위로 읽힐 자리 자체를 없애는 쪽이 "순서에 의미 없다"고 적는
#: 것보다 확실하다.
MINIMAL_CLASSIFIER_VERSION = "source-generation-minimal-classifier-2026-08-04-v3"

#: 이 판별기가 고를 수 있는 세부유형. 제5~8호 전부이고 ``etc``는 없다.
MINIMAL_SUBCLAUSES: tuple[SubclauseKey, ...] = tuple(
    subclause
    for clause in (
        ClauseNumber.CLAUSE_5,
        ClauseNumber.CLAUSE_6,
        ClauseNumber.CLAUSE_7,
        ClauseNumber.CLAUSE_8,
    )
    for subclause in sorted(SUBCLAUSES_BY_CLAUSE[clause], key=lambda key: key.value)
)


class MinimalSourceAssessment(ContractModel):
    """최소 판별기의 유일한 출력.

    초안의 6개 필드에서 ``document_form``을 빼고 셋을 더했다 —
    ``available_slots``, ``business_context``, ``subject_roles``. 셋 다 생성
    단계가 실제로 읽는 값이고, 없으면 생성기가 원문에서 무엇을 이어받을지 알
    방법이 없다. ``confidence``는 초안대로 둔다.

    **문서 형식 필드는 없다.** 원문에는 수집 라벨(``doc_type``)이 붙어 있고
    형식은 거기서 유도된다(``DOCUMENT_FORM_BY_TYPE``). 판별기가 다시 고르게
    하면 틀릴 자리만 하나 는다.

    **이름은 ``SourceAssessment``에 맞춘다.** 초안은 ``selected_security_type``·
    ``selected_doc_format``·``step1_layout_analysis``였다. 같은 것을 두 이름으로
    부르면 어댑터가 필요해지고, 두 경로의 산출물을 나란히 둘 때 사람이 매번
    대응표를 봐야 한다. ``step2``/``step3``만 접두어를 남긴다 — 저쪽에 대응하는
    필드가 없어 겹칠 이름이 애초에 없다.

    ``available_slots``도 ``SourceSlot`` 그대로다. 인용만 받던 것을
    ``EvidenceSpan``(block_id + quote)으로 바꾸면 그 인용이 실제 블록에 있는지
    검증할 수 있다 — 최소 프롬프트도 원문을 ``[BLOCK p1:b9]``로 보여 주므로
    모델이 block_id를 모를 이유가 없다.
    """

    layout_analysis: NonEmptyText
    step2_content_extraction: NonEmptyText
    step3_rule_application: NonEmptyText
    primary_subclause: SubclauseKey
    business_context: NonEmptyText
    subject_roles: tuple[SourceActorRole, ...] = Field(min_length=1)
    available_slots: tuple[SourceSlot, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _target_must_be_generatable(self) -> "MinimalSourceAssessment":
        if self.primary_subclause not in MINIMAL_SUBCLAUSES:
            raise ValueError(f"{self.primary_subclause.value} is outside clauses 5-8")
        if len(self.subject_roles) != len(set(self.subject_roles)):
            raise ValueError("subject roles must be unique")
        slot_keys = [(slot.name, slot.kind) for slot in self.available_slots]
        if len(slot_keys) != len(set(slot_keys)):
            raise ValueError("slots must be unique by name and kind")
        return self


MINIMAL_CLASSIFIER_SYSTEM_TEMPLATE = """\
# Role
너는 문서의 보안성과 행정 체계를 분석하여 분류하는 전문가이다.

# Task
제시된 [분류 체계]와 이미 정해진 [문서 형식]을 바탕으로 [원문]을 3단계 사고
과정(CoT)에 따라 분석하고, 가장 적합한 보안·행정 유형 하나를 결정하라.

# [분류 체계: 보안·행정 유형]
$security_types

# [문서 형식 ]
$document_form_line

# [분류 규칙 및 가이드]
- **형식은 주어졌다**: 위 [문서 형식]은 이미 확정된 값이다. 다시 판단하지 말고
  보안 유형을 좁히는 단서로 쓴다.
- **형식이 유형을 정하지 않는다**: 같은 형식의 문서라도 걸리는 유형은 문서마다
  다르다. 감사 자료가 감사 기준일 수도, 등장 인물의 개인정보일 수도, 수의계약
  상대방의 영업비밀일 수도 있다. 형식 이름과 유형 이름이 비슷하다는 이유로
  짝지어 고르지 않는다.
- **목록 순서에는 의미가 없다**: 위에서부터 읽다가 그럴듯한 것에서 멈추지 말고,
  원문을 먼저 읽고 무슨 일이 오갔는지 정한 뒤 그 업무에 맞는 유형을 고른다.
- **원문은 공개 문서다**: 평가기준·개인정보·영업비밀 같은 값은 원문에 없다. 고르는 것은
  "이 문서가 그렇다"가 아니라 **그 값이 놓일 업무와 주체가 원문에 있는가**이다.
  낱말이 겹친다는 이유로 고르지 않는다.

# [출력 형식 (JSON)]
반드시 아래 순서대로 사고하여 결과를 출력하라.
- layout_analysis: 문서의 구조적 특징(헤더, 항목번호 체계, 표 배치, 서명·결재란 유무) 기술.
  내용이 아니라 눈에 보이는 구조만 적는다. 생성 단계가 이 값을 **유지할 골격**으로 받는다.
- step2_content_extraction: 문서가 다루는 업무와 등장하는 주체 기술
- step3_rule_application: 분류 규칙을 어떻게 적용했는지 기술. **고른 것 하나만
  적지 말고, 다음으로 그럴듯했던 유형 둘을 왜 버렸는지 한 줄씩 적는다** —
  원문의 어느 업무·주체가 없어서 성립하지 않는지를 짚는다.
- primary_subclause: 보안 유형 키워드 하나
- business_context: 원문의 업무를 짧고 구체적으로
- subject_roles: 원문에 실제로 등장하는 사람·법인 역할만
- available_slots: 생성 시 의미를 보존해 **값만 갈아끼울** 수 있는 자리.
  name(자리 이름), kind(문단/표 열/key-value/붙임),
  evidence_span(block_id + quote — 그 자리를 짚는 원문 그대로의 인용).
  block_id는 원문에 `[BLOCK p1:b9]`처럼 표시된 값을 그대로 쓴다.
  quote는 요약하거나 `...`으로 줄이지 말고 그 block에 있는 그대로 복사한다.
- confidence: 0.0~1.0"""

MINIMAL_CLASSIFIER_USER_TEMPLATE = """\
[원문]
$source_document
"""


def render_minimal_classifier_system_prompt(document_form: DocumentForm) -> str:
    """보안유형 16종을 한 줄 정의로만 렌더링한다.

    ``document_form``은 필수다 — 형식은 ``doc_type``에서 유도되는 아는 값이라
    판별기가 고를 일이 없다. 형식 17종 목록 대신 주어진 형식 한 줄만 싣는다.
    """

    # 호는 싣지 않는다. 판별기가 내는 값은 세부유형 하나뿐이고 호는 코드가
    # 거기서 유도한다(``clause_of_subclause``) — 프롬프트에 호를 세우면 "호를
    # 먼저 고르고 그 안에서 세부유형을 고른다"는 틀만 생긴다. 고르는 근거는
    # 원문의 업무와 주체이지 조문 번호가 아니다.
    #
    # 번호도 매기지 않는다. v2는 1~16번을 붙였고 선택이 그 앞머리에 몰렸다
    # (19건 중 1번 12건, 3번 6건). 번호가 없으면 순위로 읽힐 자리가 없다.
    security_types = "\n".join(
        f"- {subclause.value} ({SUBCLAUSE_DEFINITIONS[subclause].label}): "
        f"{SUBCLAUSE_DEFINITIONS[subclause].definition}"
        for subclause in MINIMAL_SUBCLAUSES
    )
    definition = DOCUMENT_FORM_DEFINITIONS[document_form]
    return Template(MINIMAL_CLASSIFIER_SYSTEM_TEMPLATE).substitute(
        security_types=security_types,
        document_form_line=(
            f"이 원문의 형식은 {document_form.value}({definition.label})이다: "
            f"{definition.definition}"
        ),
    )


def render_minimal_classifier_user_prompt(source_document: str) -> str:
    return Template(MINIMAL_CLASSIFIER_USER_TEMPLATE).substitute(
        source_document=source_document,
    )
