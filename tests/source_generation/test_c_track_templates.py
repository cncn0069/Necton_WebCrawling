"""C트랙 템플릿의 트랙 분리와 전개 다양성 회귀 테스트."""

import pytest

from rd2.source_generation.c_track_templates import (
    C_TRACK_TEMPLATES,
    expand_cases,
    render_generation_prompt,
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
def test_document_form_actually_changes_the_prompt(template):
    """형식이 바뀌면 프롬프트 내용이 실제로 달라져야 한다.

    사안과 형식이 독립 축이던 때는 ``계획안``·``승인·품의``·``대응계획서``가
    표제부까지 같아 프롬프트에서 label 한 단어만 달랐다 — 형식 축이 6배를
    차지하면서 아무 일도 하지 않았다. ``SubjectCase``가 두 축을 묶은 뒤로는
    같은 사안이라도 형식마다 다른 문서명과 다른 담기는 항목이 나온다.
    """

    by_form: dict[str, set[str]] = {}
    for frame in expand_cases(template, template.case_count, seed=7):
        key = f"{frame.subject}|{frame.document_form.value}"
        if key in by_form:
            continue
        case = frame.subject_case
        by_form[key] = {case.document_name, *case.contents}

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


def test_templates_only_cover_clause_1_to_4():
    for template in _TEMPLATES:
        assert template.subclause_key not in _S_TRACK_SUBCLAUSES
