from __future__ import annotations

from dataclasses import replace

from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSE_BOUNDARY_RULES,
    SUBCLAUSE_DEFINITIONS,
    TAXONOMY_VERSION,
    render_taxonomy_guidance,
)
from rd2.source_generation.document_select import SelectionConfig
from rd2.source_generation.prompts import (
    PASS2_SYSTEM_PROMPT,
    build_prompt_bundle,
    render_pass1_user_prompt,
    render_pass2_user_prompt,
    render_relevance_user_prompt,
)


def test_prompt_bundle_hash_is_stable_and_covers_required_components():
    first = build_prompt_bundle()
    second = build_prompt_bundle()

    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64
    payload = first.fingerprint_payload()
    assert payload["taxonomy_version"] == TAXONOMY_VERSION
    assert payload["selection_config"]["page_threshold"] == 85
    assert {prompt["name"] for prompt in payload["prompts"]} == {
        "relevance",
        "pass1",
        "pass2",
    }
    assert all("response_schema" in prompt for prompt in payload["prompts"])


def test_prompt_or_selection_config_change_changes_bundle_hash():
    bundle = build_prompt_bundle()
    pass1 = bundle.definition("pass1")
    changed_prompt = bundle.with_definition(
        replace(pass1, system_prompt=pass1.system_prompt + "\n새 규칙")
    )
    changed_config = build_prompt_bundle(SelectionConfig(front_page_limit=19))

    assert changed_prompt.sha256 != bundle.sha256
    assert changed_config.sha256 != bundle.sha256


def test_prompt_renderers_substitute_payloads_without_cross_pass_labels():
    relevance = render_relevance_user_prompt(
        "[BLOCK p1:b0]\n본문",
        max_selected_blocks=5,
    )
    pass1 = render_pass1_user_prompt(
        "[PAGE 1]\n[BLOCK p1:b0]\n본문",
        generation_plan='{"mode":"counterfactual"}',
    )
    pass2 = render_pass2_user_prompt('{"title":"생성본"}')

    assert "최대 5개" in relevance
    assert "[BLOCK p1:b0]" in relevance
    assert '{"mode":"counterfactual"}' in pass1
    assert "[SOURCE DOCUMENT]" in pass1
    assert "[ASSESSMENT SCOPE]" in pass1
    assert "[SENSITIVE SEED]" in pass1
    assert '{"title":"생성본"}' in pass2
    assert "source classification" not in pass2
    assert "generation target" not in pass2
    assert "생성기의 분류" in PASS2_SYSTEM_PROMPT


def test_prompt_bundle_gives_p1_and_p2_the_same_taxonomy_without_route_leakage():
    bundle = build_prompt_bundle()
    pass1_system = bundle.definition("pass1").system_prompt
    pass2_system = bundle.definition("pass2").system_prompt

    for expected in (
        "제1호 (C)",
        "제8호 (S)",
        "bid_contract (입찰계약):",
        "decision_review (의사결정·내부검토):",
        "personnel_pii (인사·채용 개인정보):",
        # 라벨이 아니라 판정 정의·포함·제외 기준이 양쪽에 동일하게 간다.
        "예정가격 산정 근거",
        "핵심 업무가 입찰이면 bid_contract",
        "절차의 공정성이 핵심이고 개인 식별이 부수적이면",
    ):
        assert expected in pass1_system
        assert expected in pass2_system
    assert "span_seeded" in pass1_system
    assert "span_seeded" not in pass2_system
    assert "[SENSITIVE SEED]는 source span이 아니다" in pass1_system
    assert "별도 sensitive seed만 있으면 반드시 anchored" in pass1_system
    assert "O source + contextual_anchor_only +" in pass1_system
    assert "generation_mode=counterfactual 조합이다" in pass1_system
    assert "C/S source + direct_legal_evidence +" in pass1_system
    assert "administrative_statuses가" in pass1_system
    assert "legal clause를 새로 붙이거나 anchored로" in pass1_system
    assert "GeneratedDocumentIR만" in pass1_system
    assert "목표 classification·clause·subclause를 판단" in pass1_system
    assert "[SENSITIVE SEED]의 상황을 본문의 핵심" in pass1_system
    assert "source의 공개 내용만 요약해서는 안 된다" in pass1_system
    assert "문서가 무엇을 포함하거나 다룬다고 소개하지 말고" in pass1_system
    assert "최소 3개의 구체적 사실" in pass1_system
    assert '"합성", "가상", "예시"라는 표지' in pass1_system
    assert "document_type=other일 때는 other_document_type" in pass2_system
    assert "classification=O이면 clause_no=null" in pass2_system
    assert "classification=C/S이면" in pass2_system


def test_every_subclause_carries_a_definition_beyond_its_label():
    """라벨은 enum 키의 번역일 뿐이라 혼동 쌍을 구분하지 못한다."""

    guidance = render_taxonomy_guidance()

    for key, definition in SUBCLAUSE_DEFINITIONS.items():
        # 정의가 라벨을 되풀이하는 수준이면 모델에게 정보가 없는 것과 같다.
        assert len(definition.definition) > len(definition.label) + 20, key
        assert definition.includes, key
        assert definition.excludes, key
        assert f"- {key.value} ({definition.label}):" in guidance


def test_confusable_subclause_pairs_have_explicit_boundary_rules():
    """정의만으로 갈리지 않는 쌍은 경계 규칙으로 한 번 더 못박는다."""

    rules = "\n".join(SUBCLAUSE_BOUNDARY_RULES)

    for left, right in (
        ("bid_contract", "decision_review"),
        ("audit_inspection", "decision_review"),
        ("personnel_management", "personnel_pii"),
        ("technology_development", "technology_patent"),
        ("security_defense", "security_diagnosis"),
        # 라벨에 '민원'이 겹쳐 가장 헷갈리는데 v1에서 규칙이 없던 쌍.
        ("petitioner_pii", "welfare_pii"),
        ("bid_contract", "unit_cost"),
        ("subject_pii", "audit_inspection"),
    ):
        assert any(
            left in rule and right in rule for rule in SUBCLAUSE_BOUNDARY_RULES
        ), f"no boundary rule distinguishes {left} from {right}"

    # 라벨 낱말 겹침으로 고르지 말라는 지시가 실제로 존재한다.
    assert "'민원'이라는 낱말로 구분하지 않는다" in rules
    # 제1호 과잉 적용 방지 규칙이 P2 전용이 아니라 공유 taxonomy에 있다.
    assert "표현만으로 legal_secret을 선택하지" in rules


def test_relevance_prompt_carries_taxonomy_without_generation_leakage():
    bundle = build_prompt_bundle()
    relevance_system = bundle.definition("relevance").system_prompt

    # 선택기는 P1/P2와 같은 세부조항 의미를 봐야 근거 block을 떨어뜨리지 않는다.
    for expected in (
        "제1호 (C)",
        "제8호 (S)",
        "bid_contract (입찰계약):",
        "unit_cost (원가·납품단가):",
        "security_diagnosis (보안진단·취약점):",
        "[세부조항 경계 규칙]",
        # 선택기에게 가장 필요한 건 "무엇이 근거로 보이는가"다.
        "포함: 예정가격 산정 근거",
        "모의해킹 결과와 미조치 취약점 목록",
    ):
        assert expected in relevance_system

    # 누락된 block의 근거가 되살아나지 않는다는 사실을 명시한다.
    assert "영구히 보이지 않는다" in relevance_system

    # 선택기는 생성 경로·목표를 알 필요가 없고, 알면 선택이 목표에 오염된다.
    for forbidden in (
        "source_aligned",
        "span_seeded",
        "anchored",
        "fully_synthetic",
        "generation_mode",
        "counterfactual",
    ):
        assert forbidden not in relevance_system


def test_prompts_require_semantic_p1_status_context_and_independent_p2_grading():
    bundle = build_prompt_bundle()
    pass1_system = bundle.definition("pass1").system_prompt
    pass2_system = bundle.definition("pass2").system_prompt

    assert "상황과 문맥으로 드러낸다" in pass1_system
    assert "상태명이나 정답용 고정 문구를 억지로 삽입" in pass1_system
    assert "행정상태 taxonomy" in pass2_system
    assert "결재진행중: 담당자 기안 완료" in pass2_system
    assert "상태명이 직접 쓰이지 않았더라도" in pass2_system
    assert "법적 classification이 O여도" in pass2_system
    assert "effective_classification" in pass2_system
