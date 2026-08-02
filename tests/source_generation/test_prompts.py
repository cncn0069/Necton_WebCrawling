from __future__ import annotations

import re
from dataclasses import replace

import pytest

from rd2.source_generation import prompts as prompts_module
from rd2.source_generation.contracts import (
    ConsistencyAssessment,
    GeneratedDocumentIR,
    MaskFillResponse,
    SensitiveMonitorDecision,
    SourceAssessment,
    SourceEvidenceLevel,
)
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SUBCLAUSE_GENERATION_RULES,
    SubclauseKey,
    SUBCLAUSES_BY_CLAUSE,
    render_target_clause_section,
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
        "mask_restoration",
        "validator",
        "sensitive_validator",
    )
    assert bundle.definition("classifier").response_model is SourceAssessment
    assert bundle.definition("generator").response_model is GeneratedDocumentIR
    # 마스킹 복원만 문서가 아니라 값을 반환한다 — 이 route의 요점이 계약에
    # 드러나야 한다.
    assert (
        bundle.definition("mask_restoration").response_model is MaskFillResponse
    )
    assert bundle.definition("validator").response_model is ConsistencyAssessment
    assert (
        bundle.definition("sensitive_validator").response_model
        is SensitiveMonitorDecision
    )


def test_sensitive_monitor_contract_contains_only_binary_decision_and_record():
    definition = build_prompt_bundle().definition("sensitive_validator")
    schema = definition.response_model.model_json_schema()
    properties = schema["properties"]

    assert set(properties) == {"classification", "rationale"}
    assert set(properties["classification"]["enum"]) == {"S", "O"}
    for forbidden in (
        "block_id",
        "글자 그대로의 인용문",
        "document_form",
        "clause_no",
        "subclause_key",
        "assertions",
        "hard_case_review",
    ):
        assert forbidden not in properties
    assert "정확한 block_id" in definition.system_prompt
    assert "별도 필드로 찾거나 반환하지 않는다" in definition.system_prompt


def test_sensitive_monitor_keeps_inspector_role_and_explains_binary_labels():
    prompt = build_prompt_bundle().definition("sensitive_validator").system_prompt

    assert "당신은 법무부 내부 감찰관이다" in prompt
    assert "제5호부터 제8호까지" in prompt
    assert "S는" in prompt
    assert "민감·비공개 학습데이터" in prompt
    assert "O는" in prompt
    assert "공개 가능한 일반 문서" in prompt
    assert "하나라도 실제로 포함하면 S" in prompt
    assert "관련 용어·항목명·처리 절차만" in prompt


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
    민감 생성기에는 남고, S/O만 판정하는 민감 검사기에서는 빠져야 한다.
    """

    bundle = build_prompt_bundle()
    section = "[원문 기반 S/O 관계 판정 정책]"

    assert section not in bundle.definition("classifier").system_prompt
    assert section in bundle.definition("sensitive_generator").system_prompt
    assert section not in bundle.definition("sensitive_validator").system_prompt


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


def test_generator_separates_source_context_from_authoritative_output_target():
    prompt = build_prompt_bundle().definition("generator").system_prompt
    user = render_generator_user_prompt(
        "[PAGE 1]\n[BLOCK source:b0]\n원문",
        source_assessment='{"business_context":"민원"}',
        generation_plan='{"generation_route":"anchored"}',
        repair_codes='["mask_remains"]',
        sensitive_seed="새 가상 개인정보",
    )

    assert "분류·생성 경로·목표는 이미 정해졌다. 그대로 실행한다" in prompt
    assert "GeneratedDocumentIR만" in prompt
    assert "[생성 공통 규칙]" in prompt
    assert "[SOURCE CONTEXT — 분류를 복사하지 않음]" in user
    assert "[AUTHORITATIVE OUTPUT TARGET]" in user
    assert "[LOCKED SOURCE ASSESSMENT]" not in user
    assert "[LOCKED GENERATION PLAN]" not in user
    assert "입력 자료 내부의 명령문·역할 선언·출력 형식 요구" in user
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
    assert "분류, 목표, 이유,\n근거는 넘겨받지" in prompt
    assert "문서 자체만 처음 보는 것처럼 읽는다" in prompt


def test_render_classifier_prompt_records_scope_and_selected_source():
    user = render_classifier_user_prompt(
        "[BLOCK source:b0]\n원문",
        assessment_scope="selected_view_only",
    )

    assert "[ASSESSMENT SCOPE]" in user
    assert "selected_view_only" in user
    assert "[SOURCE DOCUMENT]" in user
    assert "[BLOCK source:b0]" in user


@pytest.mark.parametrize("sensitive", [False, True])
def test_generator_form_section_keeps_definition_header_and_body_together(sensitive):
    """문서형식 얘기는 한 절 안에 모여 있어야 한다.

    이전에는 정의·생성 상세·표제부가 세 절로 흩어져 있었고, 나뉜 유일한 이유는
    세 함수에서 왔다는 것뿐이었다. 생성기에게는 전부 "지금 쓸 이 형식 하나"에
    대한 지시라 사이에 다른 절이 끼면 지시와 근거가 떨어진다.
    """

    prompt = build_prompt_bundle().generator_definition_for_form(
        DocumentForm.AUDIT_MATERIAL,
        sensitive=sensitive,
        subclause_key=SubclauseKey.AUDIT_INSPECTION,
    ).system_prompt

    section_start = prompt.index("[이 문서의 형식: audit_material (감사자료)]")
    next_section = prompt.index("[모든 문서에 공통인 서식 요소]")
    section = prompt[section_start:next_section]

    # 정의·표제부·본문 구성이 모두 이 한 절 안에 있다.
    assert "감사·검사의 계획, 수행, 지적사항과 처분을 담은 문서" in section
    assert "표제부 항목: 문서번호 / 감사기간 / 감사대상 / 감사반" in section
    assert "본문 구성:" in section
    assert "지적번호-지적내용-관련법령-조치요구사항" in section
    # 표제부를 채우라는 지시가 항목 목록과 같은 절에 있다.
    assert "첫 block은 위 항목을 담은 key_value로 시작하고" in section


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


def test_validator_scope_is_narrowed_to_clauses_five_to_eight():
    """**이전 결정을 뒤집은 자리다.**

    전에는 "검증기는 목표를 모른 채 채점하는 것이 존재 이유라, 선택지를 미리
    좁히면 생성물이 실제로 C로 읽히는 경우를 탐지할 수 없다"는 이유로 제1~8호를
    모두 줬고, 대신 "제1~4호는 C, 제5~8호는 S다"라는 대응 규칙을 붙였다.

    그 규칙으로도 새는 것이 실측(2026-08-01, 4회 실행)에서 확인됐다 —
    ``classification=C`` + ``clause_no=5``, ``clause_no=4`` + 제5호 세부유형처럼
    계약 위반을 내 **실행마다 2~3건이 통째로 버려졌다.** 탐지력을 얻으려다
    측정 자체를 잃고 있었다.

    이 파이프라인은 제5~8호만 생성한다(``SourceAssessment``가 C를 계약에서
    거부하고 생성 목표도 제5~8호뿐이다). C로 읽히는 생성물을 탐지할 능력은
    포기하고, 대신 채점이 끝까지 도달하는 것을 택했다. 되돌리려면 이 테스트를
    지우는 것이 아니라 위 실측 실패율을 다시 재야 한다.
    """

    prompt = build_prompt_bundle().definition("validator").system_prompt

    # 금지는 **스키마를 이름으로 부르며** 한 줄로 남긴다. 계약의
    # ``CsoClassification``에는 C가 그대로 있어 모델이 스키마에서 그 값을 보고,
    # 프롬프트가 침묵하면 enum 이름의 낱말로 짐작한다 — ``direct_legal_evidence``를
    # 정의에서 빼면서 금지 한 줄만 남긴 것과 같은 처방이다
    # (``SOURCE_EVIDENCE_LEVEL_RULES``). 일반 검증기는 금지 문구가 필요하지만,
    # 민감 검사기는 live 전용 S/O Literal이라 C를 낼 수 없다.
    assert "스키마에 C가 남아 있어도 고르지 않는다" in prompt
    sensitive_definition = build_prompt_bundle().definition("sensitive_validator")
    sensitive_schema = sensitive_definition.response_model.model_json_schema()
    assert set(sensitive_schema["properties"]["classification"]["enum"]) == {"S", "O"}
    assert "classification은 S 또는 O만 쓴다" in prompt
    assert "제5호 (S)" in prompt
    assert "제1호 (C)" not in prompt


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

    두 role 모두 제5~8호만 다룬다. 판별기는 계약이 C를 거부하고, 검증기도
    이 파이프라인이 만드는 문서만 채점하므로 C를 낼 일이 없다.
    """

    bundle = build_prompt_bundle()
    classifier = bundle.definition("classifier").system_prompt
    validator = bundle.definition("validator").system_prompt

    for prompt in (classifier, validator):
        assert CSO_LABEL_GUIDANCE in prompt

    assert "- C:" not in CSO_LABEL_GUIDANCE
    assert "legal_secret" not in validator
    assert "legal_secret" not in classifier


def test_neither_judge_can_reach_clauses_one_to_four():
    """실측(2026-08-01): 검증기가 제1~4호로 새 실행마다 2~3건이 통째로 버려졌다.

    ``classification=C`` + ``clause_no=5``, ``clause_no=4`` + 제5호 세부유형처럼
    계약 위반을 냈다. 고를 수 없는 선택지를 목록에 두면 그리로 샌다 — 판별기에
    제5~8호만 주던 것과 같은 처방을 검증기에도 적용했다.

    ``제외`` 항목까지 함께 걸러야 한다. 경계 규칙만 필터링하던 때
    ``security_diagnosis``의 제외가 제3호 ``security_defense``를 인용해
    남아 있었다.
    """

    bundle = build_prompt_bundle()
    hidden = [
        subclause.value
        for clause in (
            ClauseNumber.CLAUSE_1,
            ClauseNumber.CLAUSE_2,
            ClauseNumber.CLAUSE_3,
            ClauseNumber.CLAUSE_4,
        )
        for subclause in SUBCLAUSES_BY_CLAUSE[clause]
    ]
    for role in ("classifier", "validator"):
        prompt = bundle.definition(role).system_prompt
        for name in hidden:
            assert name not in prompt, (role, name)

    validator = bundle.definition("validator").system_prompt
    assert "classification은 S 또는 O만 쓴다" in validator
    assert not re.search(r"제[1-4]호", validator)


def test_taxonomy_repeats_the_clause_number_next_to_every_subclause():
    """실측 회귀: validator가 ``classification=S`` + ``subclause_key=business_strategy``
    (제7호 소속)는 맞혔지만 ``clause_no=5``를 내 계약 검증에서 응답 전체가 버려졌다.
    절 머리 `제N호 (S)`는 한 번만 나오고 목록이 길어 개별 항목을 읽을 때 잊히기
    쉽다 — 항목마다 소속 호를 반복해 그 실수를 막는다.
    """

    validator_prompt = build_prompt_bundle().definition("validator").system_prompt
    classifier_prompt = build_prompt_bundle().definition("classifier").system_prompt

    for clause_no in (
        ClauseNumber.CLAUSE_5,
        ClauseNumber.CLAUSE_6,
        ClauseNumber.CLAUSE_7,
        ClauseNumber.CLAUSE_8,
    ):
        for subclause in SUBCLAUSES_BY_CLAUSE[clause_no]:
            marker = f"- {subclause.value} [제{clause_no.value}호]"
            assert marker in validator_prompt
            assert marker in classifier_prompt


def test_validator_rejects_field_names_as_evidence():
    """실측(2026-08-01) 오탐: 생성물이 프롬프트의 문서 패턴 목록을 그대로 베껴
    "예정가격 조서: 품목별 단가와 산정 근거 ... 제출해 주시기 바랍니다"라는
    자료 요청 공문을 냈는데, 검증기가 바로 그 문장들을 근거로 S를 줬다.
    문서 안에 실제 금액·점수·이름은 하나도 없었다.

    제6호 전용 검증기에는 같은 규칙이 이미 있었지만 일반 검증기에는 없었다.
    """

    bundle = build_prompt_bundle()
    validator = bundle.definition("validator").system_prompt
    assert "항목명은 근거가 아니다" in validator
    assert "제출해 주시기 바랍니다" in validator
    assert "글자 그대로 있을 때만 evidence로 인정" in validator

    # 제6호 전용 검사기는 정확한 위치·인용을 통과 조건으로 삼지 않는다.
    sensitive = bundle.definition("sensitive_validator").system_prompt
    assert "정확한 block_id" in sensitive
    assert "글자 그대로의 인용문" in sensitive
    assert "찾거나 반환하지 않는다" in sensitive


def test_prompt_constants_keep_roles_separate():
    assert "근거\n판별기이다" in CLASSIFIER_SYSTEM_PROMPT
    # 생성기는 "생성기"가 아니라 그 문서를 실제로 쓰는 사람으로 세운다.
    assert "당신은 대한민국 공공기관의" in GENERATOR_SYSTEM_PROMPT
    assert "한 건을 작성한다" in GENERATOR_SYSTEM_PROMPT


def test_relevance_classifier_and_general_validator_still_see_every_document_form():
    """critical gap (2026-07-31 plan-eng-review): generator만 잠긴
    document_form 하나로 필터링해야 한다. relevance·classifier·validator는
    형식을 스스로 판별해야 하므로 필터 파라미터가 추가된 뒤에도 여전히 17개
    전부를 받아야 한다 — 테스트 없이 조용히 깨지면 판별 정확도만 떨어지고
    에러가 나지 않는다(리뷰에서 지적된 조용한 실패 경로).
    """

    bundle = build_prompt_bundle()
    relevance = bundle.definition("relevance").system_prompt
    classifier = bundle.definition("classifier").system_prompt
    validator = bundle.definition("validator").system_prompt
    sensitive_validator = bundle.definition("sensitive_validator").system_prompt

    for form in DocumentForm:
        assert f"- {form.value} (" in relevance, form
        assert f"- {form.value} (" in classifier, form
        assert f"- {form.value} (" in validator, form
    assert "[문서 형식 경계 규칙]" in relevance
    assert "[문서 형식 경계 규칙]" in classifier
    assert "[문서 형식 경계 규칙]" in validator
    assert "[문서 형식 경계 규칙]" not in sensitive_validator
    assert "document_form" not in sensitive_validator


def test_generator_definition_for_form_is_filtered_to_one_form():
    """generator에게 주는 프롬프트는 잠긴 document_form 하나만 담고, 나머지
    16개 형식 이름은 판별용 제외(excludes) 문구를 통해서도 새어 들어오지
    않아야 한다(D1 발견: excludes 문구가 다른 형식 이름을 그대로 인용한다).
    """

    bundle = build_prompt_bundle()
    audit = bundle.generator_definition_for_form(
        DocumentForm.AUDIT_MATERIAL, sensitive=False
    )
    meeting = bundle.generator_definition_for_form(
        DocumentForm.MEETING_MINUTES, sensitive=False
    )

    assert "audit_material" in audit.system_prompt
    assert "감사대상·감사기간을 표제부에" in audit.system_prompt
    for form in DocumentForm:
        if form is DocumentForm.AUDIT_MATERIAL:
            continue
        assert f"- {form.value} (" not in audit.system_prompt, form
    assert "[문서 형식 경계 규칙]" not in audit.system_prompt
    # 문서형식 절에는 판별용 제외(excludes)가 없어야 한다 — 그 문구가 다른
    # 형식 이름을 그대로 인용하기 때문이다. "제외:"가 프롬프트 전체에서
    # 안 보인다고 단언하지는 않는다(taxonomy 절은 정당하게 담고 있다).
    form_section = audit.system_prompt.split(
        "[이 문서의 형식: audit_material", 1
    )[1].split("[모든 문서에 공통인 서식 요소]", 1)[0]
    assert "제외:" not in form_section
    assert audit.sha256 != meeting.sha256

    sensitive_audit = bundle.generator_definition_for_form(
        DocumentForm.AUDIT_MATERIAL, sensitive=True
    )
    assert sensitive_audit.sha256 != audit.sha256
    assert sensitive_audit.name == "sensitive_generator"
    assert audit.name == "generator"


def test_generator_sees_only_the_locked_target_subclause():
    """실측(2026-07-31 seoul_opengov 10건) 후속: 목표 세부유형이 이미 잠겼는데도
    제5~8호 16개 규칙을 전부 주면 정작 지켜야 할 규칙이 묻힌다. decision_review
    규칙이 "'아직 최종 확정되지 않은 내부 검토 단계' 같은 문장을 근거로 삼지
    않는다"고 이름까지 붙여 금지했는데도 생성물이 정확히 그 문장으로 끝나
    독립 채점자가 O로 판정했다.
    """

    bundle = build_prompt_bundle()
    prompt = bundle.generator_definition_for_form(
        DocumentForm.REPORT,
        sensitive=False,
        subclause_key=SubclauseKey.DECISION_REVIEW,
    ).system_prompt

    # 페르소나 문단이 맨 앞에 오고, 그 직후 목표 조항이 온다.
    assert prompt.startswith("당신은 대한민국 공공기관에서")
    assert prompt.index("한 건을 작성한다") < prompt.index(
        "[목표 조항: 정보공개법 제9조 제5호"
    )
    assert prompt.index("[목표 조항: 정보공개법 제9조 제5호") < prompt.index(
        "[이 문서의 형식:"
    )

    # 목표 조항 설명과 그 조항의 생성 규칙은 한 절에 모여 있고, 나머지
    # 15개 세부유형 규칙은 빠진다.
    target_section = prompt.split("[목표 조항: 정보공개법 제9조 제5호", 1)[1].split(
        "[이 문서의 형식:", 1
    )[0]
    assert "판정 기준:" in target_section
    assert "이 문서에 만들어 넣을 것:" in target_section
    # 실제 지자체 비공개 기준이 이 유형으로 드는 문서명이 지시에 들어 있다.
    assert "사업검토서" in target_section
    assert "심의조서" in target_section
    assert "[제6호 목표일 때]" not in prompt
    assert "[제7호 목표일 때]" not in prompt
    assert "[제8호 목표일 때]" not in prompt


def test_named_documents_come_with_what_they_contain():
    """문서명만 나열하면 모델은 그 낱말로 짐작한다.

    이 파일이 이미 여러 번 배운 실패다(``evidence_level``, 문서형식). 실무
    문서명을 들 때는 그 안에 실제로 들어가는 항목을 함께 준다 — 규칙마다
    ``"문서명: 담기는 항목"`` 형태를 강제해 이름만 남는 일을 막는다.
    """

    for key, rule in SUBCLAUSE_GENERATION_RULES.items():
        assert rule.document_patterns, key
        for pattern in rule.document_patterns:
            assert ":" in pattern, (key, pattern)
            name, contains = pattern.split(":", 1)
            assert name.strip(), (key, pattern)
            # 담기는 항목이 문서명보다 짧으면 설명이 아니라 되풀이다.
            assert len(contains.strip()) > len(name.strip()), (key, pattern)

    section = render_target_clause_section(SubclauseKey.DECISION_REVIEW)
    assert "실무에서 이 내용을 담는 자료와, 그 안에 실제로 들어가는 항목:" in section
    assert "사업검토서(사업확정 전): 추진 배경" in section


def test_generator_never_names_a_sibling_subclause():
    """생성기 프롬프트에는 목표 세부유형 하나의 이름만 나온다.

    목표는 이미 잠겨 있어 다시 고를 수 없으므로 "핵심 업무가 감사면
    audit_inspection" 같은 판별용 ``excludes``는 실행할 수 없는 지시이고,
    그 문장이 인용하는 다른 세부유형 이름은 프롬프트 안에 설명이 없어
    가리키는 곳 없는 참조로 남는다. 문서형식 ``제외``를 뺀 것과 같은 이유다.
    """

    bundle = build_prompt_bundle()
    subclauses = [
        subclause
        for clause in (
            ClauseNumber.CLAUSE_5,
            ClauseNumber.CLAUSE_6,
            ClauseNumber.CLAUSE_7,
            ClauseNumber.CLAUSE_8,
        )
        for subclause in SUBCLAUSES_BY_CLAUSE[clause]
    ]
    for document_form in DocumentForm:
        for target in subclauses:
            prompt = bundle.generator_definition_for_form(
                document_form,
                sensitive=(target in SUBCLAUSES_BY_CLAUSE[ClauseNumber.CLAUSE_6]),
                subclause_key=target,
            ).system_prompt
            for other in subclauses:
                if other is target:
                    continue
                assert other.value not in prompt, (document_form, target, other)

    # 다른 세부유형은 다른 프롬프트이자 다른 fingerprint여야 한다.
    other_prompt = bundle.generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=False,
        subclause_key=SubclauseKey.PETITIONER_PII,
    )
    assert "petitioner_pii" in other_prompt.system_prompt
    assert "decision_review" not in other_prompt.system_prompt
    assert other_prompt.sha256 != bundle.generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=False,
        subclause_key=SubclauseKey.DECISION_REVIEW,
    ).sha256


def test_official_letter_decision_review_uses_one_integrated_section():
    """실측(2026-07-31): official_letter × decision_review 4건이 3회 실행 내내
    전부 O였다. 공문 관행("요청 사항을 나열하고 회신 기한을 명시")이 목표
    조항("오간 검토 내용을 담는다")과 반대로 당겼다. 뒤에서 우선순위를 설명해도
    두 지시가 모두 남으므로, 이 조합은 처음부터 실행용 통합 절 하나만 받는다.
    """

    prompt = build_prompt_bundle().generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=False,
        subclause_key=SubclauseKey.DECISION_REVIEW,
    ).system_prompt

    integrated_heading = (
        "[작성 대상: official_letter(공문) × 정보공개법 제9조 제5호\n"
        "의사결정·내부검토(decision_review)]"
    )
    assert integrated_heading in prompt
    assert prompt.count("decision_review") == 1

    # 판별용 일반 정의와 그것을 취소하던 교정 절은 모두 빠진다.
    assert "[목표 조항:" not in prompt
    assert "[이 문서의 형식:" not in prompt
    assert "[공문 × 내부검토 작성 방식]" not in prompt
    assert "요청 사항을 번호 매긴 항목으로 나열한다" not in prompt
    assert "수신·경유가 명시된 협조 요청, 자료 제출 요구" not in prompt
    assert "관행과 [목표 조항]이 요구하는 내용이 어긋나면" not in prompt

    # 통합 절 자체에 형식·법적 내용·후속 조치가 모두 있다.
    integrated = prompt.split(integrated_heading, 1)[1].split(
        "[모든 문서에 공통인 서식 요소]", 1
    )[0]
    assert "key_value 표제부" in integrated
    assert "확정 전 대안·쟁점" in integrated
    assert "협의·조치를 요청하는" in integrated
    assert "검토 내용을 먼저 모두 제시" in integrated
    # 금지 예문을 그대로 보여줘 문구를 복제시키지 않는다.
    assert '"검토 의견을 제출해 달라"' not in prompt
    assert '"자료를 보내 달라"' not in prompt


def test_generator_scopes_pii_policy_to_clause_six_targets():
    """제6호용 개인정보 정책이 제5·7·8호 생성 내용을 오염시키지 않는다.

    정상 파이프라인은 clause_no==6일 때만 sensitive=True를 넘기지만, 렌더러를
    직접 부르는 코드나 테스트가 잘못된 조합을 만들 수 있다. 조립 함수에서도
    목표 조항을 확인해 방어한다.
    """

    bundle = build_prompt_bundle()
    decision_review = bundle.generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=True,
        subclause_key=SubclauseKey.DECISION_REVIEW,
    ).system_prompt
    business_strategy = bundle.generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=True,
        subclause_key=SubclauseKey.BUSINESS_STRATEGY,
    ).system_prompt
    petitioner_pii = bundle.generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=True,
        subclause_key=SubclauseKey.PETITIONER_PII,
    ).system_prompt
    unlocked_sensitive = bundle.generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=True,
        subclause_key=None,
    ).system_prompt

    policy_heading = "[원문 기반 S/O 관계 판정 정책]"
    assert policy_heading not in decision_review
    assert policy_heading not in business_strategy
    assert policy_heading in petitioner_pii
    # 세부유형이 아직 없는 민감 경로는 조항을 좁힐 수 없으므로 기존 동작 유지.
    assert policy_heading in unlocked_sensitive


def test_official_letter_decision_review_ends_with_eight_item_checklist():
    """통합 절의 성공 조건을 반환 직전의 검사 가능한 8개 질문으로 다시 모은다."""

    prompt = build_prompt_bundle().generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=False,
        subclause_key=SubclauseKey.DECISION_REVIEW,
    ).system_prompt

    heading = "[반환 전 합격 점검 — 공문 × 내부검토]"
    assert heading in prompt
    checklist = prompt.split(heading, 1)[1]
    assert re.findall(r"(?m)^([1-8])\. ", checklist) == list("12345678")
    assert "하나라도 아니면 문서를 고친 뒤 반환한다" in checklist
    assert prompt.rstrip().endswith("본문에 있는가?")

    # 다른 형식이나 다른 목표에는 이 조합 전용 체크리스트가 붙지 않는다.
    other_form = build_prompt_bundle().generator_definition_for_form(
        DocumentForm.REPORT,
        sensitive=False,
        subclause_key=SubclauseKey.DECISION_REVIEW,
    ).system_prompt
    other_target = build_prompt_bundle().generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=False,
        subclause_key=SubclauseKey.BID_CONTRACT,
    ).system_prompt
    assert heading not in other_form
    assert heading not in other_target


def test_generator_persona_combines_form_role_and_subclause_work():
    """"당신은 생성기다"가 아니라 그 문서를 실제로 쓰는 사람으로 세운다.

    직무는 문서형식(누가 쓰는가), 업무 맥락은 목표 세부유형(무슨 일을 하는가)에서
    온다 — 둘 다 이미 잠긴 값이라 조합이 결정론적이다.
    """

    bundle = build_prompt_bundle()

    audit = bundle.generator_definition_for_form(
        DocumentForm.AUDIT_MATERIAL,
        sensitive=False,
        subclause_key=SubclauseKey.AUDIT_INSPECTION,
    ).system_prompt
    assert audit.startswith(
        "당신은 대한민국 공공기관에서 감사·검사 업무를 맡고 있는 감사담당관이다."
        "\n지금 실제 업무로 감사자료 한 건을 작성한다."
    )

    # 같은 문서형식이라도 업무(목표 세부유형)가 다르면 다른 사람이다.
    personnel = bundle.generator_definition_for_form(
        DocumentForm.AUDIT_MATERIAL,
        sensitive=True,
        subclause_key=SubclauseKey.PERSONNEL_PII,
    ).system_prompt
    assert "직원 인사 업무를 맡고 있는 감사담당관이다" in personnel

    # 같은 업무라도 문서형식이 다르면 다른 직무다. 받침 없는 직함은 "다"로 끝난다.
    minutes = bundle.generator_definition_for_form(
        DocumentForm.MEETING_MINUTES,
        sensitive=False,
        subclause_key=SubclauseKey.AUDIT_INSPECTION,
    ).system_prompt
    assert "감사·검사 업무를 맡고 있는 회의 간사다" in minutes
    assert "회의록 한 건을 작성한다" in minutes

    # 어떤 조합에서도 "생성기"로 자칭하지 않는다.
    for prompt in (audit, personnel, minutes):
        assert "당신은 공개된 원문을 받아" not in prompt
        assert "생성기이다" not in prompt


def test_generator_without_locked_subclause_keeps_all_clause_rules():
    """행정상태 단독 목표처럼 세부유형이 잠기지 않은 경우는 좁힐 수 없다."""

    prompt = build_prompt_bundle().generator_definition_for_form(
        DocumentForm.OFFICIAL_LETTER,
        sensitive=False,
        subclause_key=None,
    ).system_prompt

    assert "[제5호 목표일 때]" in prompt
    assert "[제6호 목표일 때]" in prompt
    assert "[목표 조항: 정보공개법" not in prompt


def test_validator_is_an_inspector_not_a_writer():
    """채점자는 그 문서를 쓰는 사람이 아니라 분류가 맞는지 따지는 사람이다.

    생성기 페르소나(실무자)와 시선이 반대여야 채점이 독립적으로 선다. 다만
    감찰관은 "잘못을 찾는" 쪽으로 기울기 쉽고 이 채점에서 그 편향은 곧 S
    오탐이므로, 방향을 "적혀 있다고 주장된 것과 실제로 있는 것을 구분한다"로
    고정하고 근거가 없으면 O로 가도록 함께 못박는다.
    """

    bundle = build_prompt_bundle()
    validator = bundle.definition("validator").system_prompt
    sensitive = bundle.definition("sensitive_validator").system_prompt
    assert "당신은 법무부 내부 감찰관이다" in validator
    assert "S/O 검사기다" in sensitive
    assert "지금 실제 업무로" not in validator
    assert "지금 실제 업무로" not in sensitive

    assert "적혀 있다고 주장된 것이 아니라 실제로 있는 것만" in validator
    assert "위반을 찾아내는 것이 목적이 아니므로" in validator
