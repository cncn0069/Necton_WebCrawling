from __future__ import annotations

import pytest
from pydantic import ValidationError

from rd2.administrative_status import AdminStatus
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DOCUMENT_FORM_DEFINITIONS,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    GeneratedDocumentIR,
    GenerationMode,
    GenerationPlan,
    GenerationTarget,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    PLANNER_POLICY_VERSION,
    TableBlock,
    TargetClassification,
)
from rd2.source_generation.document_form import (
    check_document_form,
    render_generator_form_section,
)


def _target(*statuses: AdminStatus) -> GenerationTarget:
    if statuses:
        return GenerationTarget(
            classification=TargetClassification.S,
            administrative_statuses=statuses,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def _approval_table() -> TableBlock:
    return TableBlock(
        block_id="approval",
        columns=("기안", "검토", "결재"),
        rows=(("김기안", "이검토", ""),),
    )


def _document(*entries: KeyValueEntry, draft: bool = False) -> GeneratedDocumentIR:
    blocks = [
        KeyValueBlock(block_id="header", entries=entries),
        ParagraphBlock(block_id="body", text="형식 검증용 본문입니다."),
    ]
    if draft:
        blocks.append(_approval_table())
    return GeneratedDocumentIR(title="형식 검증 문서", blocks=tuple(blocks))


@pytest.mark.parametrize("key", ("문서번호", "시행일자"))
def test_only_draft_header_fields_can_represent_an_empty_key_value(key: str):
    entry = KeyValueEntry(key=key, value="")

    assert entry.value == ""


@pytest.mark.parametrize("value", ("", "   "))
def test_other_key_value_fields_remain_non_empty(value: str):
    with pytest.raises(ValidationError, match="blank key-value"):
        KeyValueEntry(key="수신", value=value)


def test_blank_draft_header_value_cannot_be_hidden_in_a_later_key_value_block():
    with pytest.raises(ValidationError, match="only allowed in the first block"):
        GeneratedDocumentIR(
            title="잘못 배치한 초안 표제부",
            blocks=(
                ParagraphBlock(block_id="body", text="본문입니다."),
                KeyValueBlock(
                    block_id="late-header",
                    entries=(KeyValueEntry(key="문서번호", value=""),),
                ),
            ),
        )


def test_draft_official_letter_keeps_both_blank_header_fields():
    document = _document(
        KeyValueEntry(key="문서번호", value=""),
        KeyValueEntry(key="시행일자", value=""),
        draft=True,
    )

    report = check_document_form(
        document,
        _target(AdminStatus.DRAFT),
        document_form=DocumentForm.OFFICIAL_LETTER,
    )

    assert report.has_header is True
    assert report.passed is True


@pytest.mark.parametrize(
    ("entries", "message"),
    (
        (
            (
                KeyValueEntry(key="문서번호", value="기획과-17"),
                KeyValueEntry(key="시행일자", value=""),
            ),
            "문서번호 값은 공란",
        ),
        (
            (
                KeyValueEntry(key="문서번호", value=""),
                KeyValueEntry(key="수신", value="기획조정실장"),
            ),
            "시행일자 항목 없음",
        ),
    ),
)
def test_draft_rejects_filled_or_missing_draft_header_fields(
    entries: tuple[KeyValueEntry, ...], message: str
):
    report = check_document_form(
        _document(*entries, draft=True),
        _target(AdminStatus.DRAFT),
        document_form=DocumentForm.OFFICIAL_LETTER,
    )

    assert report.passed is False
    assert any(message in item for item in report.missing)


def test_non_draft_document_rejects_a_blank_header_field():
    document = _document(
        KeyValueEntry(key="문서번호", value=""),
        KeyValueEntry(key="수신", value="기획조정실장"),
        KeyValueEntry(key="시행일자", value="2026-08-01"),
    )

    report = check_document_form(
        document,
        _target(),
        document_form=DocumentForm.OFFICIAL_LETTER,
    )

    assert report.has_header is True
    assert report.passed is False
    assert "확정 문서 표제부 문서번호 값이 비어 있음" in report.missing


def test_draft_form_without_document_number_or_execution_date_keeps_its_own_header():
    document = _document(
        KeyValueEntry(key="회차", value="제3차"),
        KeyValueEntry(key="개최일시", value="2026-08-01 10:00"),
        draft=True,
    )

    report = check_document_form(
        document,
        _target(AdminStatus.DRAFT),
        document_form=DocumentForm.MEETING_MINUTES,
    )

    assert report.passed is True


def test_other_form_preserves_a_non_official_header():
    document = _document(
        KeyValueEntry(key="신청번호", value="AP-2026-17"),
        KeyValueEntry(key="신청일자", value="2026-08-01"),
    )

    report = check_document_form(
        document,
        _target(),
        document_form=DocumentForm.OTHER,
    )

    assert report.passed is True


def test_draft_other_form_blanks_only_dynamic_header_keys_that_exist():
    filled = _document(
        KeyValueEntry(key="문서번호", value="외부양식-17"),
        KeyValueEntry(key="신청일자", value="2026-08-01"),
        draft=True,
    )
    blanked = _document(
        KeyValueEntry(key="문서번호", value=""),
        KeyValueEntry(key="신청일자", value="2026-08-01"),
        draft=True,
    )

    filled_report = check_document_form(
        filled,
        _target(AdminStatus.DRAFT),
        document_form=DocumentForm.OTHER,
    )
    blanked_report = check_document_form(
        blanked,
        _target(AdminStatus.DRAFT),
        document_form=DocumentForm.OTHER,
    )

    assert "초안 표제부 문서번호 값은 공란이어야 함" in filled_report.missing
    assert blanked_report.passed is True


def test_other_form_prompt_does_not_force_an_official_letter_header():
    prompt = render_generator_form_section(DocumentForm.OTHER)

    assert "원문에서 식별되는 목록 밖 형식의 항목" in prompt
    assert "신청서·접수대장·확인서에 공문 전용" in prompt
    assert "표제부 항목: 문서번호 / 수신 / 시행일자" not in prompt


def test_generator_does_not_sacrifice_the_locked_form_to_the_target_clause():
    prompt = render_generator_form_section(DocumentForm.REPORT)

    assert "목표 조항을 따르고" not in prompt
    assert "서식 요소로만 지킨다" not in prompt


def test_generator_uses_generation_elements_instead_of_classifier_includes():
    definition = DOCUMENT_FORM_DEFINITIONS[DocumentForm.OFFICIAL_LETTER]
    prompt = render_generator_form_section(DocumentForm.OFFICIAL_LETTER)

    assert "이 형식이면 항상 필요한 요소:" in prompt
    assert "아래에서 하나만 선택한다" in prompt
    assert "서로 다른 변형을 한 문서에 섞지 않는다" in prompt
    for item in definition.required_elements:
        assert f"- {item}" in prompt
    for item in definition.variant_patterns:
        assert f"- {item}" in prompt
    for item in definition.includes:
        assert f"- {item}" not in prompt


def test_form_compatibility_change_bumps_the_planner_policy_version():
    schema = GenerationPlan.model_json_schema()

    assert PLANNER_POLICY_VERSION == "source-generation-planner-v3"
    assert schema["properties"]["planner_policy_version"]["const"] == (
        PLANNER_POLICY_VERSION
    )
