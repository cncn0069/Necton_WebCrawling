"""생성기 프롬프트 최소판.

지키는 성질은 둘이다 — **한 호의 규칙만 들어간다**, 그리고 **예시를 준 대가로
따라오는 실패를 반환 직전에 잡는다.**
"""

from __future__ import annotations

import pytest

from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSE_GENERATION_RULES,
    ClauseNumber,
    SubclauseKey,
    clause_of_subclause,
)
from rd2.source_generation.minimal_prompt import (
    CLAUSE_ESCALATION_RULES,
    MINIMAL_GENERATOR_CHECKLIST,
    SUBCLAUSE_EXAMPLES,
    render_minimal_generator_system_prompt,
)

_GENERATABLE = tuple(SUBCLAUSE_GENERATION_RULES)


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_only_the_target_clause_rule_is_rendered(subclause: SubclauseKey):
    """고를 수 없는 규칙은 고를 이유만 준다.

    현행 ``SENSITIVE_CLAUSE_GENERATION_GUIDANCE``는 제5~8호 규칙을 전부 주고
    "해당 규칙 하나만 적용한다"고 덧붙이는데, 실측에서 제6호 목표 문서에 제5호
    감사 문구가 섞여 나왔다. 최소판은 분별기가 정한 호 하나만 싣는다.
    """

    prompt = render_minimal_generator_system_prompt(subclause)
    own = clause_of_subclause(subclause)

    assert CLAUSE_ESCALATION_RULES[own] in prompt
    for clause, rule in CLAUSE_ESCALATION_RULES.items():
        if clause is not own:
            assert rule not in prompt, clause.value


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_checklist_is_always_last(subclause: SubclauseKey):
    """반환 직전 점검이라 맨 뒤여야 한다."""

    prompt = render_minimal_generator_system_prompt(subclause)

    assert prompt.endswith(MINIMAL_GENERATOR_CHECKLIST)


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_every_example_reaches_the_prompt(subclause: SubclauseKey):
    """짝이 어긋나면 ``zip``이 조용히 짧은 쪽에 맞춰 예시가 사라진다."""

    prompt = render_minimal_generator_system_prompt(subclause)

    for example in SUBCLAUSE_EXAMPLES[subclause]:
        assert example in prompt


def test_every_supported_clause_has_an_escalation_rule():
    assert set(CLAUSE_ESCALATION_RULES) == {
        ClauseNumber.CLAUSE_5,
        ClauseNumber.CLAUSE_6,
        ClauseNumber.CLAUSE_7,
        ClauseNumber.CLAUSE_8,
    }


def test_prompt_stays_far_below_the_current_generator():
    """최소판이라는 이름이 값과 어긋나지 않게 상한을 둔다.

    현행 생성기 프롬프트는 형식별 3,512~3,977자이고 여기에 판별 결과 JSON·계획
    JSON·seed·repair code가 user prompt로 더 붙는다. 최소판은 system prompt와
    원문뿐이다.
    """

    lengths = [
        len(render_minimal_generator_system_prompt(key)) for key in _GENERATABLE
    ]

    assert max(lengths) < 2_000, max(lengths)
