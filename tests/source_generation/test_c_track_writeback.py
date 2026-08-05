"""C트랙 생성물이 ``documents`` 행이 되는 경로.

여기서 지키려는 것은 두 가지다.

1. **좌표가 프레임을 되돌린다.** ``CaseFrame.case_index``는 전개 순번이 아니라
   mixed-radix 좌표인데, 순번으로 오해하면 다른 프레임이 조용히 나온다(실측:
   국가정보원 건이 국가사이버안보센터 대신 국제범죄정보센터로 조립됐다).
   행만 봐서는 드러나지 않는 어긋남이라 테스트가 대신 본다.
2. **원문이 없다는 사실이 값으로 남는다.** ``body_text``/``content``/``ref_id``가
   NULL이어야 한다. 빈 문자열이나 0이 들어가면 "원문을 봤는데 비어 있었다"와
   구분되지 않는다.
"""

from __future__ import annotations

import json

import pytest

from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.source_generation.c_track_templates import (
    C_TRACK_TEMPLATES,
    case_frame,
    expand_cases,
    render_cot_case_section,
    render_cot_fixed_prefix,
)
from rd2.source_generation.c_track_writeback import (
    C_TRACK_SOURCE,
    build_c_track_row,
    c_track_generation_target,
    c_track_row_metadata,
    c_track_source_document_id,
)
from rd2.source_generation.contracts import (
    ConfidentialSnippet,
    GeneratedDocumentIR,
)
from rd2.source_generation.doc_type_bucket import (
    DOC_TYPE_BUCKETS,
    doc_type_for_form,
)
from rd2.source_generation.rds_writeback import (
    RowMetadata,
    SourceRow,
    build_generated_document,
)

TEMPLATES = list(C_TRACK_TEMPLATES.values())


def _document() -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        title="비밀 재분류 심의 결과 보고",
        blocks=(
            {
                "block_id": "b0",
                "kind": "paragraph",
                "text": "심의 대상 12건 중 10건을 처리하고 2건을 보류함.",
            },
            {
                "block_id": "b1",
                "kind": "paragraph",
                "text": "지정 취급자는 3인으로 한정함.",
            },
        ),
    )


def _row(frame, template, **overrides):
    defaults = dict(
        template=template,
        frame=frame,
        document=_document(),
        seed=0,
        system_prompt=render_cot_fixed_prefix(template),
        user_prompt=render_cot_case_section(template, frame),
    )
    defaults.update(overrides)
    return build_c_track_row(**defaults)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.agency)
def test_case_index_is_a_coordinate_not_a_position(template):
    """전개된 프레임은 자기 ``case_index`` 하나로 그대로 되세워진다."""

    for frame in expand_cases(template, 20, seed=7):
        assert case_frame(template, frame.case_index) == frame


def test_case_frame_rejects_out_of_range_coordinates():
    template = TEMPLATES[0]
    with pytest.raises(ValueError):
        case_frame(template, template.case_count)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.agency)
def test_metadata_comes_from_the_case_frame(template):
    """기관·부서는 아는 값이다 — UNKNOWN으로 떨어지지 않는다."""

    frame = case_frame(template, 0)
    metadata = c_track_row_metadata(frame)

    assert metadata.ordering_agency == template.agency
    assert metadata.department == frame.department
    assert metadata.unit_task == frame.subject
    # 사건 프레임에 날짜축도 분류체계도 없다. 지어내지 않는다.
    assert metadata.production_date is None
    assert metadata.subject_category is None
    # doc_type은 여기서 정하지 않는다. 이 칸을 채우면 형식→6칸 어댑터보다
    # 먼저 읽혀 C트랙만 어댑터를 우회한다.
    assert metadata.doc_type is None


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.agency)
def test_row_doc_type_is_one_of_the_six_buckets(template):
    """행에 나가는 값은 형식 17종이 아니라 6칸 중 하나다."""

    frame = case_frame(template, 0)
    row = _row(frame, template)

    assert row.doc_type == doc_type_for_form(frame.document_form)
    assert row.doc_type in DOC_TYPE_BUCKETS
    # 합쳐도 원래 형식은 생성물 envelope에 그대로 남는다 — 되짚을 수 있다.
    envelope = json.loads(row.generated_text)["result"]
    assert envelope["source_classification"]["document_form"] == (
        frame.document_form.value
    )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.agency)
def test_row_says_there_was_no_source(template):
    frame = case_frame(template, 0)
    row = _row(frame, template)

    assert row.body_text is None
    assert row.content is None
    assert row.ref_id is None
    assert row.is_synthetic is True
    assert row.generated_yn == "1"
    # 생성 평문은 사라지지 않는다 — 읽을 수 있는 칸으로 간다.
    assert "심의 대상 12건" in row.generated_text


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.agency)
def test_row_carries_the_locked_clause(template):
    frame = case_frame(template, 0)
    row = _row(frame, template)

    assert row.cso_classification is CsoClassification.C
    assert row.cso_sub_clause == template.clause_no.value
    assert row.disclosure_status is DisclosureStatus.CLOSED
    assert row.ordering_agency == template.agency
    assert row.department == frame.department
    assert row.source == f"gen_{C_TRACK_SOURCE}"


def test_source_url_is_deterministic_and_split_by_coordinate():
    template = TEMPLATES[0]
    first = case_frame(template, 0)
    second = case_frame(template, 1)

    assert _row(first, template).source_url == _row(first, template).source_url
    assert _row(first, template).source_url != _row(second, template).source_url
    # seed가 빠지면 다른 전개의 같은 좌표가 같은 행으로 뭉개진다.
    assert c_track_source_document_id(first, seed=0) != c_track_source_document_id(
        first, seed=1
    )


def test_snippets_land_in_the_non_disclosure_reason():
    template = TEMPLATES[0]
    frame = case_frame(template, 0)
    row = _row(
        frame,
        template,
        snippets=(
            ConfidentialSnippet(block_id="b0", quote="심의 대상 12건 중 10건을 처리"),
            ConfidentialSnippet(block_id="b1", quote="지정 취급자는 3인으로 한정함."),
        ),
        storage_reasoning="취급자 한정과 처리 실적이 그대로 드러난다.",
    )

    reason = row.non_disclosure_reason
    assert "근거 block: [b0] 심의 대상 12건" in reason
    assert "[b1] 지정 취급자는 3인으로 한정함." in reason
    assert "저장 사유: 취급자 한정과" in reason
    # 생성기가 지목한 자리와 검증기 판정은 접두사로 갈라져야 한다.
    assert "검증 근거:" not in reason


def test_generated_text_carries_the_whole_result_envelope():
    """생성물은 ``generated_text`` 한 칸으로 완결된다.

    본문만 두면 읽는 쪽이 이 문서가 무엇인지 모른다 — 문서형식과 조항이 IR
    밖에 있다. 세 칸으로 나눠 두면 조인해야 하고 그중 하나만 비어도 조용히
    반쪽이 된다(2026-08-05 사용자 결정).
    """

    template = TEMPLATES[0]
    frame = case_frame(template, 0)
    row = _row(frame, template, prompt_version="c-track-cot-test")

    payload = json.loads(row.generated_text)
    result = payload["result"]
    assert result["generation_route"] == "fully_synthetic"
    assert result["source_classification"]["document_form"] == frame.document_form.value
    assert result["generation_target"]["subclause_key"] == template.subclause_key.value
    assert [
        block["block_id"] for block in result["generated_document"]["blocks"]
    ] == ["b0", "b1"]
    # 평문은 envelope의 IR이 만든 값이다. 같은 값을 두 칸에 두지 않는다.
    assert row.body_text is None

    # 나머지 두 칸은 아예 없다 — 이 행의 생성물이 어디 있는지가 갈리면 안 된다.
    assert not hasattr(row, "pdf_renderd_json")
    assert not hasattr(row, "batch_json")


def test_military_grade_only_rides_along_when_it_is_one():
    """'대외비'는 군사기밀 등급이 아니라 문서 표기다."""

    for template in TEMPLATES:
        frame = case_frame(template, 0)
        target = c_track_generation_target(frame)
        if frame.security_grade in {"1급", "2급", "3급"}:
            assert target.military_secret_grade == frame.security_grade
        else:
            assert target.military_secret_grade is None


def test_source_row_and_metadata_cannot_both_be_given():
    """둘 다 있으면 어느 쪽이 이겼는지 행만 봐서는 드러나지 않는다."""

    with pytest.raises(ValueError):
        build_generated_document(
            document=_document(),
            source_document_id="x-1",
            target=c_track_generation_target(case_frame(TEMPLATES[0], 0)),
            generation_route="fully_synthetic",
            source_row=SourceRow(id=1, source="alio"),
            metadata=RowMetadata(ordering_agency="법무부"),
        )
