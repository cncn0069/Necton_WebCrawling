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
    MINIMAL_GENERATOR_CHECKLIST,
    MINIMAL_GENERATOR_OUTPUT_ORDER,
    SUBCLAUSE_EXAMPLES,
    find_mask_slots,
    render_minimal_generator_system_prompt,
    render_minimal_generator_user_prompt,
)

_GENERATABLE = tuple(SUBCLAUSE_GENERATION_RULES)

_SOURCE = "\n\n".join(f"[BLOCK p1:b{index}]\n줄 {index}" for index in range(7))


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_only_the_target_clause_rule_is_rendered(subclause: SubclauseKey):
    """고를 수 없는 규칙은 고를 이유만 준다.

    현행 ``SENSITIVE_CLAUSE_GENERATION_GUIDANCE``는 제5~8호 규칙을 전부 주고
    "해당 규칙 하나만 적용한다"고 덧붙이는데, 실측에서 제6호 목표 문서에 제5호
    감사 문구가 섞여 나왔다. 최소판은 분별기가 정한 목표 하나만 싣는다.

    v9에서 호 단위 위험 극대화 절을 없앴으므로(같은 말을 세 번 하고 있었다 —
    정의·위험 극대화·instruction) 이제 세부유형 단위로 같은 것을 본다.
    """

    prompt = render_minimal_generator_system_prompt(subclause)

    assert SUBCLAUSE_GENERATION_RULES[subclause].instruction in prompt
    for other in _GENERATABLE:
        if other is subclause:
            continue
        assert SUBCLAUSE_GENERATION_RULES[other].instruction not in prompt, other.value


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_every_example_reaches_the_prompt(subclause: SubclauseKey):
    """짝이 어긋나면 ``zip``이 조용히 짧은 쪽에 맞춰 예시가 사라진다."""

    prompt = render_minimal_generator_system_prompt(subclause)

    for example in SUBCLAUSE_EXAMPLES[subclause]:
        assert example in prompt


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_coverage_rule_is_always_rendered(subclause: SubclauseKey):
    """원문을 몇 block 남길지는 목표 조항과 무관하다.

    실측(radio-001): 이 규칙이 없을 때 원문 126 block 중 32 block만 남았고,
    사라진 것이 산발적 문장이 아니라 절 단위였다. 조항별로 켜고 끄면 어느
    조항에서 원문이 증발하는지가 조항 성능처럼 보이게 되므로 항상 넣는다.

    v7에서 이 규칙은 별도 절이 아니라 ``[출력 순서]``의 ``document.blocks``
    필드 설명으로 옮겼다 — 그래서 절이 통째로 있는지가 아니라 **규칙 네 개가
    렌더링되는지**를 본다. 자리가 바뀌어도 지시가 남아 있으면 통과해야 하고,
    조용히 사라지면 실패해야 한다.
    """

    prompt = render_minimal_generator_system_prompt(subclause)

    assert MINIMAL_GENERATOR_OUTPUT_ORDER in prompt
    assert "글자 그대로 옮긴다" in prompt  # 기본 동작
    assert "요약해 합치지 않고" in prompt  # 요약 금지
    assert "표는 행과 열을 그대로" in prompt  # 표 보존
    assert "절·항목 제목은" in prompt  # 절 보존


def test_user_prompt_states_the_block_count_on_both_shapes():
    """모델에게 "세어 보라"고만 하면 세지 않는다. 셀 수를 미리 준다.

    판별 결과를 붙이는 경로와 원문만 넘기는 경로(``pipeline``의 최소 route)가
    갈리는데, 개수를 한쪽에만 주면 그 route만 조용히 짧아진다.
    """

    bare = render_minimal_generator_user_prompt(_SOURCE)
    with_assessment = render_minimal_generator_user_prompt(
        _SOURCE,
        layout_analysis="5쪽 개조식",
        available_slots="- [값] 출연진",
    )

    assert "block 7개" in bare
    assert "block 7개" in with_assessment


def test_slot_list_does_not_read_as_a_keep_list():
    """[바꿀 수 있는 자리]가 유일한 원문 포인터면 "나머지는 버려도 된다"로 읽힌다."""

    prompt = render_minimal_generator_user_prompt(
        _SOURCE,
        layout_analysis="5쪽 개조식",
        available_slots="- [값] 출연진",
    )

    assert "이 목록에 없는 block도 **전부 출력에 담는다.**" in prompt


@pytest.mark.parametrize("subclause", _GENERATABLE, ids=lambda k: k.value)
def test_masking_is_an_exception_inside_the_coverage_rule(subclause: SubclauseKey):
    """마스킹 규칙은 `그대로 옮겨 적기`와 **같은 자리에** 있어야 한다.

    최소판에는 이 규칙이 아예 없었다(현행 프롬프트는
    ``SENSITIVE_CLAUSE_COMMON_RULES``에 갖고 있다). 그 자리를 보존 규칙의
    `그대로 옮겨 적기`가 덮어써서, 부분공개 원문의 ``****``가 채울 자리가 아니라
    옮겨 적을 글자가 됐다. 그래서 다른 절에 규칙을 하나 더 두는 것으로는 안 된다
    — 앞뒤로 모순되는 두 지시가 되고, 그때 모델이 뒤엣것만 남긴다는 것은 제5호
    실측에서 이미 봤다.

    v7에서 두 규칙이 함께 ``document.blocks`` 필드 설명으로 옮겨갔다. 검사하는
    것은 여전히 같다 — **둘이 한 자리에 있는가**.
    """

    prompt = render_minimal_generator_system_prompt(subclause)

    assert "글자 그대로 옮긴다" in MINIMAL_GENERATOR_OUTPUT_ORDER
    assert "값이 들어갈 자리" in MINIMAL_GENERATOR_OUTPUT_ORDER
    assert "마스킹 문자는 남기지 않는다" in prompt
    # 반환 직전 점검에도 걸어 둔다. 지시만 있고 점검이 없으면 무엇이 안 지켜졌는지
    # 생성물에서만 드러나는데, 마스킹은 채점기가 잡지 못한다.
    assert "마스킹이 출력에 그대로 남아 있지 않은가" in prompt


def test_mask_slots_are_listed_before_the_source_goes_out():
    """마스킹 자리는 **코드가** 뽑아 목록으로 준다.

    프롬프트 문장만 두면 모델이 원문을 옮겨 적는 동안 ``○○``를 알아보기를
    기대해야 한다. 마스킹은 정규식으로 확실히 찾히므로 판별기에 물을 필요도,
    호출을 늘릴 필요도 없다 — 원문을 보내기 전에 자리 목록으로 만들어 두면
    `[BLOCK …]` 개수와 같은 성질이 된다: 모델이 발견할 것이 없고 빠뜨림이
    셀 수 있는 값이 된다.
    """

    masked = "\n\n".join(
        (
            "[BLOCK p1:b0]\n2017년 형사부 구성안",
            "[BLOCK p1:b1]\n1 22 오○○ 남 1968 대구",
            "[BLOCK p1:b2]\n예정가격 산정은 별지와 같다",
            "[BLOCK p1:b3]\n담당자 김** 연락처 02-***-****",
        )
    )

    slots = find_mask_slots(masked)
    prompt = render_minimal_generator_user_prompt(masked)

    assert [block_id for block_id, _ in slots] == ["p1:b1", "p1:b3"]
    assert "[마스킹 자리 — 2곳. 전부 값으로 채운다]" in prompt
    assert "- p1:b1 — " in prompt
    # 마스킹이 없는 원문에는 절 자체가 붙지 않는다. 없는 자리를 채우라고 하면
    # 모델이 자리를 만든다.
    assert "[마스킹 자리" not in render_minimal_generator_user_prompt(_SOURCE)


def test_mask_slots_reach_both_user_prompt_shapes():
    """판별 결과를 붙이는 경로에도 부분공개 원문이 들어온다.

    한쪽에만 넣으면 route에 따라 마스킹이 남는다 — ``pipeline``의 최소 경로는
    원문만 넘기고 배치 스크립트는 판별 결과를 함께 넘긴다.
    """

    masked = "[BLOCK p1:b0]\n민원인 이○○ 010-****-1234"

    with_assessment = render_minimal_generator_user_prompt(
        masked,
        layout_analysis="1쪽 개조식",
        available_slots="- [값] 민원인",
    )

    assert "[마스킹 자리 — 1곳. 전부 값으로 채운다]" in with_assessment
    assert "[마스킹 자리 — 1곳. 전부 값으로 채운다]" in render_minimal_generator_user_prompt(
        masked
    )


def test_mask_slot_list_is_capped_but_says_so():
    """마스킹이 수십 곳이면 목록이 원문보다 길어진다.

    실측(형사부 구성안, 158 block): 이름이 전부 ``김○○``이라 마스킹 block이
    44곳이었다. 자르되 **자른 사실을 적는다** — 목록이 곧 상한으로 읽히면
    41번째부터는 채우지 않아도 되는 자리가 된다.
    """

    masked = "\n\n".join(
        f"[BLOCK p1:b{index}]\n위원 {index}: 김○○" for index in range(45)
    )

    prompt = render_minimal_generator_user_prompt(masked)

    assert "[마스킹 자리 — 45곳. 전부 값으로 채운다]" in prompt
    assert "- p1:b39 — " in prompt
    assert "- p1:b40 — " not in prompt
    assert "그 밖에 5곳 더 있다" in prompt


def test_no_clause_level_rule_layer_survives():
    """호 층의 규칙은 다시 들어오지 않는다.

    호 하나에 세부유형이 둘~다섯이라 그 층은 공통분모밖에 말할 수 없고, 그
    공통분모는 이미 세부유형 정의와 instruction이 더 구체적으로 말한다. 제6호
    실측에서 같은 지시가 세 번 나왔다. 값의 모양은 예시가 보여 준다.
    """

    prompt = render_minimal_generator_system_prompt(SubclauseKey.PERSONNEL_PII)

    assert "[위험 극대화" not in prompt
    # 안전 문구만 역할 지정으로 옮겨 살아남는다 — 제7호 규칙에 묻어 있었지만
    # 조항과 무관한 공통 제약이다.
    assert "실행 가능한 공격 절차는 쓰지 않는다" in prompt


def test_prompt_stays_far_below_the_current_generator():
    """최소판이라는 이름이 값과 어긋나지 않게 상한을 둔다.

    현행 생성기 프롬프트는 형식별 3,512~3,977자이고 여기에 판별 결과 JSON·계획
    JSON·seed·repair code가 user prompt로 더 붙는다. 최소판은 system prompt와
    원문뿐이다.

    상한을 2,000 -> 2,600으로 올렸다. 보존 규칙이 들어가면서 최장이
    1,760 -> 2,258이 됐는데, 그 규칙이 막는 것은 문구 실패가 아니라 **기본 동작
    미지정**이다(실측 radio-001: 원문 126 block -> 32 block). 빼면 프롬프트는
    짧아지고 생성물은 원문의 4분의 1이 된다.

    2,600 -> 2,900. 제목 규칙과 마스킹 예외가 들어가 최장이 2,791이 됐다. 둘 다
    같은 종류다 — `그대로 옮겨 적기`가 만든 부작용을 같은 자리에서 되돌린다.
    마스킹 쪽은 뺄 수 없다: 현행 프롬프트에는 있는 규칙이고, 없으면 부분공개
    원문의 ``****``가 값이 아니라 옮겨 적을 글자가 된다.

    2,900 -> 3,000. v7에서 절을 없애고 규칙을 출력 필드 설명으로 내리면서
    ``[출력 순서]``가 항상 실리게 됐다(v6까지는 ground를 지정했을 때만 붙었다).
    그 절이 이제 보존 규칙을 담으므로 조건부로 둘 수 없다. 최장은 2,984다 —
    절 하나가 사라졌는데도 200자 가까이 는 것은 필드마다 규칙을 붙이면 같은
    말을 나눠 적게 되기 때문이고, 그만큼이 이 재배치의 값이다.
    """

    lengths = [
        len(render_minimal_generator_system_prompt(key)) for key in _GENERATABLE
    ]

    assert max(lengths) < 3_000, max(lengths)
