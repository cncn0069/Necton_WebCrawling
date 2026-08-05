"""C트랙 템플릿의 트랙 분리와 전개 다양성 회귀 테스트."""

import pytest

from rd2.source_generation.c_track_templates import (
    C_TRACK_TEMPLATES,
    STAGE_GUIDANCE,
    STAGE_VARIANT_COUNT,
    expand_cases,
    render_cot_case_section,
    render_generation_prompt,
    select_frames_for_agency,
    select_frames_per_document_form,
)
from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    SubclauseKey,
)

_S_TRACK_CLAUSES = (
    ClauseNumber.CLAUSE_5,
    ClauseNumber.CLAUSE_6,
    ClauseNumber.CLAUSE_7,
    ClauseNumber.CLAUSE_8,
)
_S_TRACK_SUBCLAUSES = frozenset(
    subclause
    for clause in _S_TRACK_CLAUSES
    for subclause in SUBCLAUSES_BY_CLAUSE[clause]
)

_TEMPLATES = list(C_TRACK_TEMPLATES.values())
_IDS = [f"{t.subclause_key.value}-{t.agency}" for t in _TEMPLATES]


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_prompt_never_names_an_s_track_subclause(template):
    """C트랙 프롬프트에 5~8호 세부조항 이름이 새면 안 된다.

    ``SubclauseDefinition.excludes``가 트랙을 가로지른다 —
    ``correction_security``는 ``subject_pii``(제6호)를, ``security_defense``는
    ``security_diagnosis``(제7호)를 인용한다. 그대로 실으면 C트랙 생성기가
    설명 없는 S트랙 이름을 읽는다. 반대 방향으로 같은 사고가 이미 있었다:
    제5~8호만 준 검증기 프롬프트에 제3호 ``security_defense``가 제외 항목을
    통해 남아 있었다(2026-08-01 실측).
    """

    leaked: set[str] = set()
    for frame in expand_cases(template, 200, seed=7):
        prompt = render_generation_prompt(template, frame)
        leaked |= {key.value for key in _S_TRACK_SUBCLAUSES if key.value in prompt}
        leaked |= {
            f"제{clause.value}호"
            for clause in _S_TRACK_CLAUSES
            if f"제{clause.value}호" in prompt
        }
    assert not leaked, f"S-track references leaked into C-track prompt: {sorted(leaked)}"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_expansion_has_no_duplicate_coordinates(template):
    count = min(4000, template.case_count)
    frames = list(expand_cases(template, count, seed=7))
    assert len({frame.case_index for frame in frames}) == count


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_every_axis_cycles_early_in_the_batch(template):
    """앞자리 축이 배치 앞부분에서 멈춰 있으면 안 된다.

    보폭이 작으면 mixed-radix의 뒷자리만 돌고 부서·문서형식이 세워진다 —
    stride=11이던 때 12만 조합 중 첫 500건이 전부 같은 부서, 문서형식 2종에서
    나왔다. 순서대로 생성하면 앞뒤 문서가 서로 닮는다.
    """

    head = list(expand_cases(template, 100, seed=7))
    assert len({frame.department for frame in head}) == len(template.departments)
    assert len({frame.document_form for frame in head}) == len(template.document_forms)
    assert len({frame.subject for frame in head}) == len(template.subjects)
    assert len({frame.stage for frame in head}) == len(
        {stage for _, stage in template.case_stage_pairs}
    )
    for slot in template.slots:
        seen = {frame.slot_values[slot.name] for frame in head}
        assert seen == set(slot.values), f"slot {slot.name} did not cycle"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_form_quota_gives_every_form_the_same_count(template):
    """형식별 할당은 형식마다 정확히 그만큼 나와야 한다.

    ``expand_cases``의 앞 N건을 그냥 쓰면 형식 분포가 ``subject_cases`` 구성을
    물려받는다 — 법무부 템플릿은 18개 쌍 중 6개가 ``plan_draft``이고
    ``response_plan``은 1개뿐이라 계획안이 6배로 나온다.
    """

    per_form = 3
    frames = select_frames_per_document_form(template, per_form, seed=7)

    counts: dict[str, int] = {}
    for frame in frames:
        counts[frame.document_form] = counts.get(frame.document_form, 0) + 1
    assert counts == {form: per_form for form in template.document_forms}
    # 할당량을 채우느라 같은 조합을 두 번 쓰지 않는다.
    assert len({frame.case_index for frame in frames}) == len(frames)


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_form_quota_keeps_the_other_axes_moving(template):
    """형식으로 걸러도 나머지 축은 계속 돌아야 한다.

    할당량이 찬 형식을 건너뛸 뿐 순서 자체는 ``expand_cases``의 셔플이므로,
    부서가 한둘로 굳으면 그건 거르기가 순서를 망가뜨렸다는 뜻이다.
    """

    frames = select_frames_per_document_form(template, 5, seed=7)
    assert len({frame.department for frame in frames}) > 1
    for slot in template.slots:
        seen = {frame.slot_values[slot.name] for frame in frames}
        assert len(seen) > 1, f"slot {slot.name} froze under the form quota"


def test_form_quota_returns_short_instead_of_repeating():
    """조합이 모자란 형식은 **모자란 대로** 돌려준다.

    채우려고 두 바퀴를 돌면 같은 프레임이 두 번 나오고, 그건 호출부가 산출물만
    보고 알아챌 수 없다.

    **부족선을 세지 말고 물어본다.** 처음에는 통일부 ``approval_request``의
    조합 수 1,120을 적어 두고 1,200을 요구했는데, v4에서 적대자 축이 붙어
    그 형식이 6,720이 되자 부족이 일어나지 않아 테스트가 깨졌다. 축이 늘 때
    같이 움직여야 하는 값을 테스트가 손으로 들고 있던 것이다.
    """

    template = C_TRACK_TEMPLATES[(SubclauseKey.UNIFICATION_DIPLOMACY, "통일부")]
    thinnest = min(template.form_case_counts.values())
    per_form = thinnest + 1
    frames = select_frames_per_document_form(template, per_form, seed=7)

    counts: dict[str, int] = {}
    for frame in frames:
        counts[frame.document_form] = counts.get(frame.document_form, 0) + 1
    assert min(counts.values()) == thinnest
    assert max(counts.values()) == per_form
    assert len({frame.case_index for frame in frames}) == len(frames)


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_agency_quota_spreads_across_forms(template):
    """기관 기준으로 셀 때도 형식은 그 안에서 고르게 돌아야 한다.

    총량이 형식 수보다 작으면 **서로 다른 형식**이 그만큼 나오고, 넘으면 형식별
    건수가 최대 1건 차이로 갈린다. 앞에서부터 그냥 잘라 쓰면 총량이 작을수록
    ``subject_cases``가 두꺼운 형식(법무부 ``plan_draft``)으로 쏠린다.
    """

    forms = len(template.document_forms)

    thin = select_frames_for_agency(template, forms - 1, seed=7)
    assert len(thin) == forms - 1
    assert len({frame.document_form for frame in thin}) == forms - 1

    thick = select_frames_for_agency(template, forms * 3 + 1, seed=7)
    assert len(thick) == forms * 3 + 1
    counts: dict[str, int] = {}
    for frame in thick:
        counts[frame.document_form] = counts.get(frame.document_form, 0) + 1
    assert max(counts.values()) - min(counts.values()) <= 1
    assert len({frame.case_index for frame in thick}) == len(thick)


def test_agency_quota_is_deterministic_per_seed():
    """같은 seed면 같은 표본이어야 한다. 좌표만 남겨 두고 다시 세울 수 있어야 한다."""

    template = C_TRACK_TEMPLATES[(SubclauseKey.CORRECTION_SECURITY, "법무부")]
    first = select_frames_for_agency(template, 5, seed=3)
    again = select_frames_for_agency(template, 5, seed=3)
    other = select_frames_for_agency(template, 5, seed=4)

    assert [f.case_index for f in first] == [f.case_index for f in again]
    assert [f.case_index for f in first] != [f.case_index for f in other]


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_document_form_actually_changes_the_prompt(template):
    """형식이 바뀌면 프롬프트 내용이 실제로 달라져야 한다.

    사안과 형식이 독립 축이던 때는 ``계획안``·``승인·품의``·``대응계획서``가
    표제부까지 같아 프롬프트에서 label 한 단어만 달랐다 — 형식 축이 6배를
    차지하면서 아무 일도 하지 않았다. ``SubjectCase``가 두 축을 묶은 뒤로는
    같은 사안이라도 형식마다 다른 문서명과 다른 담기는 항목이 나온다.

    ``subject_cases``를 직접 본다. 전에는 ``expand_cases``를 전량 돌려 (사안,
    형식) 쌍을 모았는데, 찾는 것이 18쌍인데 좌표 72만 개를 훑는 셈이라 축이
    하나 늘 때마다 이 테스트만 배로 느려졌다 — v7에서 스위트가 상한 시간에
    걸린 원인이다. 전개 순서는 이 테스트가 보는 것이 아니다.
    """

    by_form: dict[str, set[str]] = {}
    for case in template.subject_cases:
        by_form[f"{case.subject}|{case.document_form.value}"] = {
            case.document_name,
            *case.contents,
        }

    for subject in template.subjects:
        docs = {
            key: value for key, value in by_form.items() if key.startswith(f"{subject}|")
        }
        names = [next(iter(sorted(v))) for v in docs.values()]
        assert len(docs) == len(
            {frozenset(v) for v in docs.values()}
        ), f"{subject}: 형식이 달라도 담기는 항목이 같다 ({names})"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_prompt_carries_only_the_locked_case_contents(template):
    """잠긴 조합과 무관한 다른 문서의 항목이 실리면 안 된다."""

    frame = next(iter(expand_cases(template, 1, seed=7)))
    prompt = render_generation_prompt(template, frame)
    locked = frame.subject_case

    for item in locked.contents:
        assert item in prompt
    for case in template.subject_cases:
        if (case.subject, case.document_form) == (locked.subject, locked.document_form):
            continue
        assert case.document_name not in prompt


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_stage_never_contradicts_the_document(template):
    """품의·계획안에 `시행 결과 보고`가 붙는 모순 조합이 나오면 안 된다.

    실측(2026-08-03): 단계가 독립 슬롯이던 때 ``수용자 이송 승인 품의``에
    ``시행 결과 보고``가 붙었고, 모델이 구체적 지시가 달린 단계 쪽을 따라
    결재란이 있어야 할 품의를 결과보고서로 썼다.
    """

    for frame in expand_cases(template, template.case_count, seed=7):
        assert frame.stage in frame.subject_case.allowed_stages


#: 순서 지시 문형. 이게 guidance에 서면 모델이 그 항목들을 본문 절 이름으로
#: 잘라 쓴다 — 실측은 ``CaseSlot.guidance`` 주석에 있다.
#:
#: ``먼저``만으로는 걸지 않는다. "상대가 먼저 움직인 건이라"는 사실 서술이고
#: 절로 쪼갤 수 없다. 문제가 되는 것은 **쓰는 행위에 붙은** 순서다.
_WRITING_ORDER_PHRASES = (
    "먼저 적",
    "먼저 다시 적",
    "적은 다음",
    "다음에 적",
    "끝낸다",
    "순서로 적",
)


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_trigger_guidance_is_complete_and_gives_no_writing_order(template):
    """촉발계기 guidance는 값마다 있고, 순서 지시를 쓰지 않는다.

    **왜 촉발계기만인가.** 진행단계(``STAGE_GUIDANCE``)와 역할구성은 아직 옛
    형태이고, 그 순서 지시가 문서형식별 골격을 실제로 가르는 유일한 장치라
    지금 없애면 ``SubjectCase``가 형식을 사안과 묶어 얻은 것이 함께 사라진다.
    촉발계기는 골격을 정할 필요가 없는 축이라 새 형태를 먼저 시험할 자리로
    골랐다 — 이 예외는 나머지 둘을 옮길 방법이 나오면 함께 사라진다.

    비어 있으면 안 되는 이유는 축이 조합 수에만 잡히고 일은 안 하기 때문이다.
    라벨만 던지면 모델이 무시한다(실측 2026-07-31).
    """

    trigger = next(slot for slot in template.slots if slot.name == "촉발계기")
    assert set(trigger.guidance) == set(trigger.values), (
        "촉발계기는 값마다 guidance가 있어야 한다"
    )
    for value, hint in trigger.guidance.items():
        for phrase in _WRITING_ORDER_PHRASES:
            assert phrase not in hint, (
                f"{template.agency}/{value} guidance가 순서를 지시한다: {phrase!r}"
            )


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_stage_variants_cycle_and_reach_the_prompt(template):
    """진행단계 변주가 축으로 돌고, 고른 문장이 실제로 프롬프트에 실린다.

    변주는 골격을 갈라 두려고 세운 축이다. 전개가 한 변주에 갇히거나 프롬프트가
    항상 첫 문장을 쓰면 조합 수만 네 배가 되고 문서는 그대로다 — 적대자가
    v3까지 전원 나열되어 축 노릇을 못하던 것과 같은 실패다.
    """

    frames = list(expand_cases(template, STAGE_VARIANT_COUNT * 60, seed=5))
    assert {frame.stage_variant for frame in frames} == set(range(STAGE_VARIANT_COUNT))

    for frame in frames[:40]:
        expected = STAGE_GUIDANCE[frame.stage][frame.stage_variant]
        assert frame.stage_guidance == expected
        assert expected in render_cot_case_section(template, frame)


def test_stage_variants_do_not_repeat_across_stages():
    """변주 문장이 단계 사이에서 겹치면 안 된다.

    "확인된 것과 미확인을 갈라 적는다" 같은 문장은 ``검토 착수``에도
    ``시행 중간점검``에도 말이 된다. 그런 문장이 서면 단계 축이 골격을 가르던
    힘이 약해진다 — ``SubjectCase``가 (사안, 형식)을 묶어 없앤 그 겹침이다.
    """

    seen: dict[str, str] = {}
    for stage, variants in STAGE_GUIDANCE.items():
        assert len(set(variants)) == len(variants), f"{stage} 안에서 변주가 겹친다"
        for variant in variants:
            assert variant not in seen, (
                f"{stage}의 변주가 {seen[variant]}에도 있다: {variant[:30]!r}"
            )
            seen[variant] = stage


def test_templates_only_cover_clause_1_to_4():
    for template in _TEMPLATES:
        assert template.subclause_key not in _S_TRACK_SUBCLAUSES
