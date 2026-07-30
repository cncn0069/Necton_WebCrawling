from __future__ import annotations

from dataclasses import replace

import pytest

from rd2.source_generation import prompts as prompts_module
from rd2.source_generation.contracts import (
    ConsistencyAssessment,
    GeneratedDocumentIR,
    SensitiveConsistencyAssessment,
    SourceAssessment,
    SourceEvidenceLevel,
)
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SUBCLAUSES_BY_CLAUSE,
)
from rd2.source_generation.prompts import (
    CLASSIFIER_SYSTEM_PROMPT,
    CSO_LABEL_GUIDANCE,
    GENERATOR_SYSTEM_PROMPT,
    SELECTABLE_EVIDENCE_LEVELS,
    SOURCE_EVIDENCE_LEVEL_DEFINITIONS,
    VALIDATOR_SYSTEM_PROMPT,
    build_prompt_bundle,
    render_classifier_user_prompt,
    render_generator_user_prompt,
    render_validator_user_prompt,
)


def test_bundle_has_role_specific_definitions_and_schemas():
    bundle = build_prompt_bundle()

    assert tuple(item.name for item in bundle.definitions) == (
        "relevance",
        "classifier",
        "generator",
        "sensitive_generator",
        "validator",
        "sensitive_validator",
    )
    assert bundle.definition("classifier").response_model is SourceAssessment
    assert bundle.definition("generator").response_model is GeneratedDocumentIR
    assert bundle.definition("validator").response_model is ConsistencyAssessment
    assert (
        bundle.definition("sensitive_validator").response_model
        is SensitiveConsistencyAssessment
    )


def test_each_prompt_has_an_independent_content_hash():
    bundle = build_prompt_bundle()
    classifier = bundle.definition("classifier")
    changed = bundle.with_definition(
        replace(
            classifier,
            system_prompt=classifier.system_prompt + "\n새 규칙",
        )
    )

    assert changed.definition("classifier").sha256 != classifier.sha256
    assert changed.definition("generator").sha256 == bundle.definition(
        "generator"
    ).sha256
    assert changed.sha256 != bundle.sha256


def test_classifier_reads_the_clauses_right_after_its_role():
    """역할 문장이 "제5호부터 제8호까지의 조항에 걸려"라고 말하므로 그 조항이
    무엇을 다루는지가 바로 뒤에 와야 한다. 문서형식 목록(5,200자)을 먼저 두면
    그만큼 밀린다.

    조항별 요건은 별도 절이 아니라 3단계 절차의 2번에 인라인으로 있다 —
    ``SENSITIVE_TAXONOMY_GUIDANCE``가 세부유형마다 같은 내용을 더 자세히
    반복하므로 조항 단위 요약 절은 없앴다.
    """

    prompt = build_prompt_bundle().definition("classifier").system_prompt

    role = prompt.index("제5호부터 제8호까지의 조항에 걸려")
    pointer = prompt.index("무엇이 민감 문서가 되는지의 근거")
    article = prompt.index("정보공개법 제9조 제1항")
    scope = prompt.index("이 프롬프트에서는 제9조 제5호부터 제8호까지만 판정한다")
    clauses = prompt.index("제5호는\n   감사·검사·입찰계약·기술개발·인사관리")
    forms = prompt.index("[문서 형식]")
    assert role < pointer < article < scope < clauses < forms


def test_classifier_is_not_asked_to_judge_the_source_label():
    """실측 회귀(v30 10건): S/O를 물었더니 전부 공개 문서인데 2건을 S로 냈고,
    S는 ``source_aligned`` 분기로 빠져 compatible_subclauses와 요청 목표를 함께
    무시한다. 그래서 묻지 않고 O로 고정한다.
    """

    prompt = build_prompt_bundle().definition("classifier").system_prompt

    assert "classification은 O이고 clause_no와" in prompt
    assert "primary_subclause에는 **가장 가까운 것 하나**를 담는다" in prompt
    # S를 고르도록 유도하는 문구가 남아 있으면 오탐이 되살아난다.
    assert "S라면 해당하는 제9조 호와 세부조항을" not in prompt
    assert "classification=S이면 direct_legal_evidence를 고른다" not in prompt


def test_classifier_defines_every_evidence_level_it_may_choose():
    """실측 회귀: 정의 없이 이름만 주자 모델이 법령명이 많은 고시를 보고
    ``direct_legal_evidence``를 고르면서 O로 판정해 계획 단계가 막혔다.
    """

    prompt = build_prompt_bundle().definition("classifier").system_prompt

    for level in SELECTABLE_EVIDENCE_LEVELS:
        assert level.value in prompt
        assert SOURCE_EVIDENCE_LEVEL_DEFINITIONS[level] in prompt

    # 정의는 그 값을 쓰는 필드 설명보다 앞에 온다.
    assert prompt.index("[근거 수준(evidence_level) 판정 정의]") < prompt.index(
        "source_suitability에는 원문이 실제로 제공하는 근거 수준을 기록한다"
    )


def test_classifier_is_not_offered_the_route_it_cannot_reach():
    """``direct_legal_evidence``는 O 원문에서 ``ROUTE_INVALID``로 끝난다. 정의는
    빼되, enum이라 structured output 스키마에는 남으므로 금지는 남겨야 한다.
    """

    prompt = build_prompt_bundle().definition("classifier").system_prompt
    unreachable = SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE

    assert unreachable not in SELECTABLE_EVIDENCE_LEVELS
    assert SOURCE_EVIDENCE_LEVEL_DEFINITIONS[unreachable] not in prompt
    assert f"- {unreachable.value}:" not in prompt
    assert "direct_legal_evidence가 남아 있어도 고르지 않는다" in prompt


def test_classifier_does_not_carry_the_source_label_policy():
    """S/O 관계 판정 정책은 S/O를 가릴 때만 쓴다 — 판별기는 더 이상 가리지 않는다.
    민감 생성기·민감 채점자에는 남아 있어야 한다.
    """

    bundle = build_prompt_bundle()
    section = "[원문 기반 S/O 관계 판정 정책]"

    assert section not in bundle.definition("classifier").system_prompt
    assert section in bundle.definition("sensitive_generator").system_prompt
    assert section in bundle.definition("sensitive_validator").system_prompt


def test_evidence_level_guidance_cannot_silently_drop_a_level(monkeypatch):
    """enum에 값이 늘고 정의가 빠지면 렌더링이 실패해야 한다.

    정의 없는 수준이 조용히 프롬프트에서 빠지면 모델은 다시 이름만 보고
    짐작하게 된다 — 이 회귀를 막는 것이 이 테스트의 목적이다.
    """

    partial = {
        level: text
        for level, text in SOURCE_EVIDENCE_LEVEL_DEFINITIONS.items()
        if level != SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE
    }
    monkeypatch.setattr(
        prompts_module, "SOURCE_EVIDENCE_LEVEL_DEFINITIONS", partial
    )

    with pytest.raises(KeyError):
        prompts_module.render_source_evidence_level_guidance()


def test_classifier_describes_facts_but_does_not_generate_or_choose_route():
    prompt = build_prompt_bundle().definition("classifier").system_prompt

    for field in (
        "source_suitability",
        "business_context",
        "subject_roles",
        "available_slots",
        "compatible_subclauses",
    ):
        assert field in prompt
    # 역할 경계는 이제 문장이 아니라 **받는 절의 구성**으로만 지켜진다 —
    # 판별기에게는 생성 규칙도 표제부도 주지 않는다.
    assert "[세부유형별 생성 규칙]" not in prompt
    assert "[문서형식별 표제부]" not in prompt


def test_generator_receives_locked_inputs_and_returns_only_ir():
    prompt = build_prompt_bundle().definition("generator").system_prompt
    user = render_generator_user_prompt(
        "[PAGE 1]\n[BLOCK source:b0]\n원문",
        source_assessment='{"business_context":"민원"}',
        generation_plan='{"generation_route":"anchored"}',
        repair_codes='["mask_remains"]',
        sensitive_seed="새 가상 개인정보",
    )

    assert "분류, 생성 경로와 목표를" in prompt
    assert "다시 판정하거나 바꾸지 않는다" in prompt
    assert "GeneratedDocumentIR만" in prompt
    assert "[세부유형별 생성 규칙]" in prompt
    assert "[LOCKED SOURCE ASSESSMENT]" in user
    assert "[LOCKED GENERATION PLAN]" in user
    assert "[REPAIR CODES]" in user
    assert "[FULL SOURCE DOCUMENT]" in user
    assert "새 가상 개인정보" in user


def test_validator_is_blind_to_source_plan_and_generation_reason():
    user = render_validator_user_prompt('{"title":"생성본","blocks":[]}')
    prompt = build_prompt_bundle().definition("validator").system_prompt

    assert "GeneratedDocumentIR만" in user
    assert "source_assessment" not in user
    assert "generation_plan" not in user
    assert "repair_codes" not in user
    assert "생성기의 분류, 목표, 이유" in prompt


def test_render_classifier_prompt_records_scope_and_selected_source():
    user = render_classifier_user_prompt(
        "[BLOCK source:b0]\n원문",
        assessment_scope="selected_view_only",
    )

    assert "[ASSESSMENT SCOPE]" in user
    assert "selected_view_only" in user
    assert "[SOURCE DOCUMENT]" in user
    assert "[BLOCK source:b0]" in user


@pytest.mark.parametrize("role", ["generator", "sensitive_generator"])
def test_generator_reads_form_definitions_next_to_the_rule_that_needs_them(role):
    """"감사자료는 감사자료의 서식으로"라고 지시한 자리에서 감사자료가 무엇인지
    바로 읽어야 한다 — 정의가 다른 절 뒤로 밀리면 지시와 근거가 떨어진다.
    """

    prompt = build_prompt_bundle().definition(role).system_prompt

    rule = prompt.index("원문의 문서형식을 그대로 사용한다")
    definitions = prompt.index("[문서 형식]")
    headers = prompt.index("[문서형식별 표제부]")
    header_rule = prompt.index("첫 block은 원문 문서형식의 표제부를")
    assert rule < definitions < headers < header_rule


@pytest.mark.parametrize("role", ["generator", "sensitive_generator"])
def test_generator_carries_each_form_section_once(role):
    """회귀: 두 절이 GENERATOR_SYSTEM_PROMPT 안으로 들어왔으므로 bundle에서
    다시 붙이면 같은 표가 두 번 나온다.
    """

    prompt = build_prompt_bundle().definition(role).system_prompt

    # 본문에서 "위 [문서형식별 표제부]를 따른다"로 되짚는 참조는 세지 않는다.
    headings = [line for line in prompt.splitlines() if line.startswith("[")]
    assert headings.count("[문서 형식]") == 1
    assert headings.count("[문서형식별 표제부]") == 1


def test_classifier_reads_form_definitions_next_to_the_rule_that_needs_them():
    """"문서 유형을 판정하고"라고 지시한 자리에서 어떤 유형이 있는지 바로 읽어야
    한다 — 생성기와 같은 배치다.
    """

    prompt = build_prompt_bundle().definition("classifier").system_prompt

    # 문구가 아니라 절의 위치로 검사한다 — 프롬프트 문장은 자주 다시 쓰인다.
    pointer = prompt.index("문서 유형은 아래 [문서 형식] 목록에서 하나를 고른다")
    definitions = prompt.index("[문서 형식]\n")
    taxonomy = prompt.index("[정보공개법 제9조 분류 taxonomy]")
    assert pointer < definitions < taxonomy

    # bundle에서 다시 붙이면 같은 목록이 두 번 나온다.
    headings = [line for line in prompt.splitlines() if line.startswith("[")]
    assert headings.count("[문서 형식]") == 1


def test_validator_is_told_which_clauses_map_to_which_label():
    """실측 회귀(v30 스모크·v32 각 1~2건): 검증기가 ``classification=S`` + 제3호를
    내서 계약 파싱에서 응답 전체가 버려졌다. 계약은 조항과 C/S의 대응만 강제하는데
    (``LegalClassification._classification_must_be_coherent``) 어느 호가 C이고 어느
    호가 S인지는 프롬프트에 없었다.

    taxonomy를 제5~8호로 좁히는 방식은 쓰지 않는다 — 검증기는 목표를 모른 채
    채점하는 것이 존재 이유라, 선택지를 미리 좁히면 생성물이 실제로 C로 읽히는
    경우를 탐지할 수 없다.
    """

    bundle = build_prompt_bundle()
    prompt = bundle.definition("validator").system_prompt

    assert "제1~4호는 C, 제5~8호는 S다" in prompt
    # 좁히지 않았음을 함께 고정한다 — C트랙 조항이 계속 보여야 한다.
    assert "제1호 (C)" in prompt


def test_validator_derives_clause_no_from_subclause_instead_of_choosing_independently():
    """실측 회귀(v37 20건): validator가 ``subclause_key=business_strategy``(제7호
    소속)는 맞혔지만 ``clause_no=5``를 독립적으로 내 계약 검증에서 응답 전체가
    버려졌다. 판별기는 애초에 clause_no 필드가 없어(``SourceAssessment``에
    ``primary_subclause``만 있고 조항 번호는 코드가 계산한다) 이 문제가 성립하지
    않지만, 검증기는 ``clause_no``·``subclause_key``를 각자 내야 하므로 순서를
    명시하지 않으면 둘이 어긋날 수 있다.
    """

    prompt = build_prompt_bundle().definition("validator").system_prompt

    assert "clause_no는 별도로 판단하지 않는다" in prompt
    assert "먼저 위 taxonomy에서" in prompt
    assert "그 세부유형이 속한 절 머리" in prompt


def test_classifier_and_validator_share_the_label_definition():
    """``ConsistencyComparison``이 두 role의 판정을 직접 비교하므로 기준이 갈리거나
    한쪽에만 있으면 비교가 무의미해진다 — ``EVIDENCE_QUOTE_GUIDANCE``와 같은 이유로
    공용 절이다.

    조항별 요건은 별도 공용 절로 두지 않는다 — ``TAXONOMY_GUIDANCE``(검증기는
    제1~8호, 판별기는 제5~8호)가 세부유형마다 이미 담고 있어 조항 단위 요약은
    그 정보의 부분집합이었다("정보공개법 c/s/o 겹치는 이유" 논의 참고).
    """

    bundle = build_prompt_bundle()
    classifier = bundle.definition("classifier").system_prompt
    validator = bundle.definition("validator").system_prompt

    for prompt in (classifier, validator):
        assert CSO_LABEL_GUIDANCE in prompt

    # 검증기만 C를 판정하므로 제1~4호 세부유형은 검증기에만 있어야 한다.
    assert "legal_secret [제1호]" in validator
    assert "legal_secret [제1호]" not in classifier


def test_taxonomy_repeats_the_clause_number_next_to_every_subclause():
    """실측 회귀: validator가 ``classification=S`` + ``subclause_key=business_strategy``
    (제7호 소속)는 맞혔지만 ``clause_no=5``를 내 계약 검증에서 응답 전체가 버려졌다.
    절 머리 `제N호 (S)`는 한 번만 나오고 목록이 길어 개별 항목을 읽을 때 잊히기
    쉽다 — 항목마다 소속 호를 반복해 그 실수를 막는다.
    """

    validator_prompt = build_prompt_bundle().definition("validator").system_prompt
    classifier_prompt = build_prompt_bundle().definition("classifier").system_prompt

    for clause_no, subclauses in SUBCLAUSES_BY_CLAUSE.items():
        for subclause in subclauses:
            marker = f"- {subclause.value} [제{clause_no.value}호]"
            assert marker in validator_prompt
            if clause_no in (
                ClauseNumber.CLAUSE_5,
                ClauseNumber.CLAUSE_6,
                ClauseNumber.CLAUSE_7,
                ClauseNumber.CLAUSE_8,
            ):
                assert marker in classifier_prompt


def test_prompt_constants_keep_roles_separate():
    assert "근거\n판별기이다" in CLASSIFIER_SYSTEM_PROMPT
    assert "잠긴 생성 계획" in GENERATOR_SYSTEM_PROMPT
    assert "독립 채점자" in VALIDATOR_SYSTEM_PROMPT
