from __future__ import annotations

from dataclasses import replace

from rd2.source_generation.classification_taxonomy import TAXONOMY_VERSION
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
        "bid_contract: 입찰계약",
        "decision_review: 의사결정·내부검토",
        "personnel_pii: 인사·채용 개인정보",
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
