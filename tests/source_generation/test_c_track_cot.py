"""C트랙 3단계(분석-작성-기록) 경로의 계약·프롬프트 회귀 테스트."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from rd2.source_generation.c_track_templates import (
    C_TRACK_TEMPLATES,
    CaseSlot,
    expand_cases,
    render_cot_case_section,
    render_cot_fixed_prefix,
    render_cot_stage_1,
    render_fixed_prefix,
)
from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
)
from rd2.source_generation.contracts import (
    BulletListBlock,
    CTrackCoTResponse,
    GeneratedDocumentIR,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
)

_TEMPLATES = list(C_TRACK_TEMPLATES.values())
_IDS = [f"{t.subclause_key.value}-{t.agency}" for t in _TEMPLATES]

_S_TRACK_CLAUSES = (
    ClauseNumber.CLAUSE_5,
    ClauseNumber.CLAUSE_6,
    ClauseNumber.CLAUSE_7,
    ClauseNumber.CLAUSE_8,
)


def _document(*, quote: str = "○ 야간 계호 인력 3명을 02:30 교대로 조정함") -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        blocks=(
            KeyValueBlock(
                block_id="h1",
                entries=(
                    KeyValueEntry(key="문서번호", value="교정보안-2026-0417"),
                    KeyValueEntry(key="비밀등급", value="대외비"),
                ),
            ),
            ParagraphBlock(block_id="p1", text="□ 점검 결과"),
            BulletListBlock(block_id="b1", items=(quote, "- 결원 2명 확인됨")),
        ),
        title="2026년 대전교도소 야간 계호 근무 실태 점검 결과",
    )


#: 계약이 3개 이상, 2개 이상 block을 요구한다.
_SNIPPETS = (
    {"block_id": "b1", "quote": "○ 야간 계호 인력 3명을 02:30 교대로 조정함"},
    {"block_id": "b1", "quote": "- 결원 2명 확인됨"},
    {"block_id": "p1", "quote": "□ 점검 결과"},
)


def _snippets(first: dict) -> tuple[dict, ...]:
    """첫 발췌만 바꾼 목록. 개수·block 요건은 그대로 채운다."""

    return (first, *_SNIPPETS[1:])


def _response(**overrides) -> CTrackCoTResponse:
    payload = {
        "security_analysis": {
            "vulnerability": "교대 시각이 알려지면 그 공백에 맞춰 이동이 가능함",
            "impact": "야간 계호의 억제 효과가 사라져 도주 시도 대응이 늦어짐",
        },
        "document": _document(),
        "confidential_snippets": _SNIPPETS,
        "reasoning_for_storage": "교대 시각과 인력 수가 그대로 적혀 있어 제4호 대상임",
    }
    payload.update(overrides)
    return CTrackCoTResponse(**payload)


def test_analysis_comes_before_the_document():
    """필드 순서가 곧 사고 순서다 — 분석이 문서보다 **앞**이어야 한다.

    모델은 스키마 순서대로 값을 낸다. 분석을 뒤로 돌리면 이미 쓴 문서를 사후에
    합리화하는 값이 되고, 그건 문서를 이끌지 못한다. ``GeneratorResponse``가
    ``ground_plan``을 ``document`` 앞에 둔 것과 같은 장치라 순서가 바뀌면
    조용히 효력만 사라진다 — 그래서 순서 자체를 테스트한다.
    """

    fields = list(CTrackCoTResponse.model_fields)
    assert fields.index("security_analysis") < fields.index("document")
    assert fields.index("document") < fields.index("confidential_snippets")
    assert fields.index("confidential_snippets") < fields.index("reasoning_for_storage")
    assert list(CTrackCoTResponse.model_json_schema()["properties"])[:2] == [
        "contract_version",
        "security_analysis",
    ]


def test_no_legal_basis_field():
    """조항은 템플릿이 잠근 값이라 모델에게 묻지 않는다."""

    assert "legal_basis" not in CTrackCoTResponse.model_fields


def test_snippet_must_be_in_its_block():
    with pytest.raises(ValidationError, match="confidential snippet not found"):
        _response(
            confidential_snippets=_snippets(
                {"block_id": "b1", "quote": "○ 야간 계호 인력 5명을 04:00 교대로 조정함"}
            )
        )


def test_snippet_block_id_must_exist():
    with pytest.raises(ValidationError, match="unknown generated document block_id"):
        _response(
            confidential_snippets=_snippets(
                {"block_id": "b9", "quote": "○ 야간 계호 인력 3명을 02:30 교대로 조정함"}
            )
        )


def test_snippet_matching_ignores_whitespace_only():
    """들여쓰기는 흘려도 되지만 글자는 아니다.

    개조식 항목은 들여쓰기가 곧 깊이라(``BULLET_MARKERS``) 발췌하면서 앞뒤
    공백이 어긋나기 쉽다. 공백만 무시하고 나머지는 그대로 대조한다.
    """

    response = _response(
        confidential_snippets=_snippets(
            {"block_id": "b1", "quote": "○  야간 계호 인력 3명을  02:30 교대로 조정함"}
        )
    )
    assert len(response.confidential_snippets) == 3


def test_snippets_cannot_be_empty():
    with pytest.raises(ValidationError):
        _response(confidential_snippets=())


def test_a_single_snippet_is_not_enough():
    """실호출(2026-08-05)에서 하한이 1이면 모델이 정확히 하나만 낸다.

    하필 그 하나가 본문의 다른 절과 수치가 어긋나는 block이었다 — 로그가 문서의
    한 자리만 가리키면 "가장 민감한 문구"를 고른 것이 아니라 처음 눈에 띈 것을
    집은 것이 된다.
    """

    with pytest.raises(ValidationError, match="at least 3 items"):
        _response(confidential_snippets=_SNIPPETS[:1])


def test_snippets_must_span_two_blocks():
    with pytest.raises(ValidationError, match="at least two blocks"):
        _response(
            confidential_snippets=(
                _SNIPPETS[0],
                _SNIPPETS[1],
                {"block_id": "b1", "quote": "3명을 02:30 교대로"},
            )
        )


def test_duplicate_snippets_are_rejected():
    with pytest.raises(ValidationError, match="duplicate confidential snippet"):
        _response(
            confidential_snippets=(_SNIPPETS[0], _SNIPPETS[0], _SNIPPETS[2])
        )


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_title_rule_labels_exist_in_the_case_section(template):
    """[제목] 규칙이 부르는 라벨이 실제로 값과 함께 실려 있어야 한다.

    절 이름만 맞추는 것으로는 부족하다는 것이 감사에서 드러났다 — ``[다루는
    사건]`` 절은 멀쩡히 있는데 규칙이 부르던 ``대상시설``만 ``[조건]``의
    ``대상 장소``로 올라가, 60/60 프레임에서 규칙이 값 없는 이름을 가리켰다.
    제목을 건마다 구별시키는 축 중 값 종류가 가장 많은 것이 사정권 밖으로
    나가면 제목이 ``document_name``으로 돌아간다(실측 2026-08-03, 10건 중 8건).

    검사하는 것은 라벨이 아니라 **값**이다. 프롬프트 v5에서 ``[다루는 사건]``의
    ``라벨: 값`` 표가 문장으로 바뀌었으므로(``CaseSlot.phrasing``) ``- 촉발계기: ``
    같은 형태를 찾으면 값이 멀쩡히 실려 있는데도 실패한다. 이 테스트가 지키려는
    것은 표기 형태가 아니라 "규칙이 부르는 축에 값이 있는가"다.
    """

    prefix = render_cot_fixed_prefix(template)
    title_rule = prefix.split("[제목]")[1].split("[문체]")[0]
    called = [label for label in ("대상 장소", "촉발계기", "진행단계") if label in title_rule]
    assert called, "제목 규칙이 아무 축도 부르지 않는다"

    for frame in expand_cases(template, 30, seed=7):
        section = render_cot_case_section(template, frame)
        for slot_name, value in frame.slot_values.items():
            assert value in section, f"[제목]이 기대는 {slot_name}에 값이 없다"
        assert frame.stage in section, "[제목]이 부르는 진행단계에 값이 없다"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_place_slot_is_flagged_not_name_matched(template):
    """장소 축은 슬롯 이름이 아니라 ``place`` 표시로 갈린다.

    ``"대상시설"``을 문자열로 박아 두면 국방부 템플릿이 ``대상부대``,
    경찰청이 ``관할구역``으로 부르는 순간 그 슬롯이 조용히 사건 축으로 떨어져
    ``[조건]``에 장소가 없는 프롬프트가 된다.
    """

    place_slots = [slot for slot in template.slots if slot.place]
    assert len(place_slots) == 1, "장소 축은 정확히 하나여야 한다"

    frame = next(iter(expand_cases(template, 1, seed=7)))
    section = render_cot_case_section(template, frame)
    condition_block = section.split("[다루는 사건]")[0]
    assert f"- 대상 장소: {frame.slot_values[place_slots[0].name]}" in condition_block


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_every_slot_can_carry_its_guidance(template):
    """슬롯 축마다 guidance를 실을 통로가 있어야 한다.

    장소 슬롯만 값을 찍고 guidance를 읽지 않던 자리가 있었다. 지금 등록된
    장소 슬롯에 guidance가 비어 있어 렌더 차이는 없었지만, 나중에 누가 붙이면
    대조군에만 반영되고 3단계 경로에서는 조용히 사라진다 — 그러면 두 경로
    차이가 골격 차이가 아니라 재료 차이가 되어 대조가 성립하지 않는다.
    """

    canary = {
        slot.name: f"GUIDANCE-CANARY-{slot.name}" for slot in template.slots
    }
    patched = [
        CaseSlot(
            name=slot.name,
            values=slot.values,
            guidance={value: canary[slot.name] for value in slot.values},
            place=slot.place,
        )
        for slot in template.slots
    ]
    probe = replace(template, slots=tuple(patched))
    for frame in expand_cases(probe, 12, seed=7):
        section = render_cot_case_section(probe, frame)
        for slot in probe.slots:
            assert canary[slot.name] in section, f"{slot.name} guidance가 실리지 않는다"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_stage_1_names_the_adversaries_with_their_gain(template):
    """[단계 1]은 적대자를 **이름과 이득으로 함께** 세워야 한다.

    이름만 나열하면 모델이 낱말로 짐작한다 — ``SUBCLAUSE_EXAMPLES``가 항목명이
    아니라 값을 담는 것과 같은 이유다. 그리고 위치가 한 종류로 쏠리면 [단계 1]의
    답이 한 방향으로 좁아져 같은 템플릿의 문서들이 같은 취약점만 말한다.
    """

    stage_1 = render_cot_stage_1(template)
    for adversary in template.adversaries:
        assert adversary.who in stage_1
        assert adversary.what_they_gain in stage_1
        assert f"({adversary.position_label})" in stage_1

    assert len(template.adversaries) >= 2
    positions = {adversary.position for adversary in template.adversaries}
    assert len(positions) >= 2, "적대자 위치가 한 종류로 쏠렸다"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_adversary_gain_is_a_result_not_a_procedure(template):
    """이득 문장이 통제의 **공백**이나 **회피 방법**을 지목하면 안 된다.

    감사(2026-08-05) 초안에서 12건이 이 선을 넘었고 넘는 방식이 셋으로
    반복됐다 — 통제 공백을 이득으로 지목("어느 구간이 확인되지 않은 채"),
    회피 방법을 이득에 적기("감시가 걸리지 않는 통로로"), 수단 열거("증거
    정리·도피·출국·진술 맞추기"). 문자열 검사로는 그 셋의 흔한 표현만 잡히지만,
    새 템플릿을 쓸 때 걸리는 것만으로도 값이 있다.
    """

    banned = (
        "걸리지 않는",
        "확인되지 않는",
        "대조되지 않는",
        "기록에 남지 않",
        "느슨해지는",
        "비는 구간",
        "우회",
    )
    for adversary in template.adversaries:
        for phrase in banned:
            assert phrase not in adversary.what_they_gain, (
                f"{adversary.who}의 이득이 절차·공백을 지목한다: {phrase!r}"
            )


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_both_prefixes_carry_the_boundary_rule(template):
    """경계는 두 경로 모두에 실린다.

    대조군을 글자 그대로 보존하는 것보다 우선한다 — 두 경로가 같은 종류의
    문서를 만들고, 한쪽만 경계를 빼두는 것은 실험 순도로 정당화되지 않는다.

    문장 끝까지 맞추지 않는다. v5에서 이 절이 목록에서 산문 한 문단으로 바뀌며
    ``…이름을 쓴다``가 ``…이름을 쓰고``가 됐다(실측 근거는 ``_BOUNDARY_RULE``
    주석). 이 테스트가 지키는 것은 표현이 아니라 **두 경로 모두에 경계가 실리고
    그 안에 대체 지시가 살아 있는가**다.
    """

    for prompt in (render_cot_fixed_prefix(template), render_fixed_prefix(template)):
        assert "[경계]" in prompt
        assert "기관·부서·시설은 실재하는 이름" in prompt
        assert "무엇이 무력화되는지" in prompt


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_prompt_prescribes_instead_of_forbidding(template):
    """v3 — 금지문 자리에 **대체문**이 서야 한다.

    v2의 "…를 적지 않는다"는 효과가 없었다. 실호출 대조에서 지목한 낱말은 v1과
    같은 횟수로 남았고, 새로 생긴 마커는 프롬프트에 없는 낱말이었다. 금지는
    쓰지 말 것만 말하고 **쓸 것**을 주지 않아 모델이 형식만 따랐다.

    회귀를 막을 자리가 둘이다 — 금지문이 다시 늘어나는 것, 그리고 종결형 지정이
    사라지는 것. 후자가 이 판의 핵심이다(드리프트가 어휘가 아니라 문형에서 났다).
    """

    prompt = render_cot_fixed_prefix(template)
    boundary = prompt.split("[경계]")[1].split("[단계 1")[0]
    stage_1 = prompt.split("[단계 1")[1].split("[단계 2")[0]

    for section in (boundary, stage_1):
        assert "자리에" in section, "금지문만 있고 대체문이 없다"

    # 쓸 문형을 지정한다 — 이게 v3의 처방이다.
    assert "무력화된다" in stage_1 or "성립하지 않게 된다" in stage_1
    # 질문 1의 주어가 행위자로 돌아가면 안 된다.
    assert "각자가 무엇을 할 수 있는가" not in stage_1


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_boundary_gives_no_literal_to_copy(template):
    """베낄 문자열을 두지 않는다.

    v2의 ``"○○ 장비 1식"처럼``이 생성 본문에 따옴표까지 그대로 두 번 복사됐다.
    ``minimal_prompt``가 같은 실패를 기록해 뒀다 — 예시 36슬롯 중 22개가 글자
    그대로 복사. 저쪽은 반환 전 점검으로 막았지만 여기서는 예시를 아예 두지
    않는 쪽을 택했다.
    """

    prompt = render_cot_fixed_prefix(template)
    boundary = prompt.split("[경계]")[1].split("[단계 1")[0]
    assert "○○" not in boundary
    assert "처럼" not in boundary


def test_contract_schema_does_not_leak_design_notes():
    """계약 docstring은 **모델이 읽는다**.

    pydantic이 클래스 docstring을 JSON Schema ``description``으로 싣고
    ``gateway.parse``가 그 스키마를 요청에 넣는다. 감사 실측(2026-08-05):
    설계 근거 docstring 때문에 스키마에 ``[비공개 근거]``·``[출력 순서]``가
    실려 나갔다 — v2 프롬프트가 **의도적으로 지운 절 이름**이라, 모델은
    프롬프트에 없는 절을 가리키는 설명을 함께 읽고 있었다.
    """

    schema = json.dumps(CTrackCoTResponse.model_json_schema(), ensure_ascii=False)
    for leaked in (
        "[비공개 근거]",
        "[출력 순서]",
        "minimal_prompt",
        "GeneratorResponse",
        "EvidenceQuote",
        "ground_plan",
        "legal_basis",
    ):
        assert leaked not in schema, f"스키마 description으로 {leaked}가 새어 나간다"


@pytest.mark.parametrize("template", _TEMPLATES, ids=_IDS)
def test_cot_prompt_never_names_an_s_track_subclause(template):
    """단일 출력 경로와 같은 금지가 3단계 경로에도 걸린다."""

    s_track = {
        subclause.value
        for clause in _S_TRACK_CLAUSES
        for subclause in SUBCLAUSES_BY_CLAUSE[clause]
    }
    leaked: set[str] = set()
    for frame in expand_cases(template, 50, seed=7):
        prompt = (
            f"{render_cot_fixed_prefix(template)}\n\n"
            f"{render_cot_case_section(template, frame)}"
        )
        leaked |= {value for value in s_track if value in prompt}
        leaked |= {
            f"제{clause.value}호"
            for clause in _S_TRACK_CLAUSES
            if f"제{clause.value}호" in prompt
        }
    assert not leaked, f"S-track references leaked into C-track CoT prompt: {sorted(leaked)}"
