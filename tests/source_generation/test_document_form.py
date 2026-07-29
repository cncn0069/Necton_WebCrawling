from __future__ import annotations

from rd2.administrative_status import AdminStatus
from rd2.source_generation.classification_taxonomy import ClauseNumber, SubclauseKey
from rd2.source_generation.contracts import (
    AttachmentReferenceBlock,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationTarget,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    TableBlock,
    TargetClassification,
)
from rd2.source_generation.document_form import check_document_form


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


def _header() -> KeyValueBlock:
    return KeyValueBlock(
        block_id="head",
        entries=(
            KeyValueEntry(key="문서번호", value="계약과-1187"),
            KeyValueEntry(key="수신", value="기획조정실장"),
            KeyValueEntry(key="시행일자", value="2026-07-29"),
        ),
    )


def test_prose_only_document_is_reported_as_missing_the_form():
    """실측 회귀: 본문 300자 줄글만 나와 P2가 35/37건을 other로 판정했다."""

    document = GeneratedDocumentIR(
        title="지역 행정서비스 개선 검토",
        blocks=(ParagraphBlock(block_id="p1", text="두 가지 개선안을 검토했다."),),
    )

    report = check_document_form(document, _target())

    assert report.passed is False
    assert report.has_header is False
    assert any("문서 머리" in m for m in report.missing)


def test_document_with_header_passes_when_no_status_demands_more():
    document = GeneratedDocumentIR(
        title="사업자 선정 평가 검토",
        blocks=(_header(), ParagraphBlock(block_id="p1", text="평가 기준을 정했다.")),
    )

    report = check_document_form(document, _target())

    assert report.passed is True
    assert report.has_header is True


def test_approval_pending_requires_an_approval_table_not_a_sentence():
    """상태를 문장으로 서술하는 대신 결재란 빈칸으로 드러내야 한다."""

    narrated = GeneratedDocumentIR(
        title="개선안 검토",
        blocks=(
            _header(),
            ParagraphBlock(block_id="p1", text="최종 결재는 아직 이루어지지 않았습니다."),
        ),
    )
    assert "결재란" in " ".join(
        check_document_form(narrated, _target(AdminStatus.APPROVAL_PENDING)).missing
    )

    shown = GeneratedDocumentIR(
        title="개선안 검토",
        blocks=(
            _header(),
            TableBlock(
                block_id="approval",
                columns=("구분", "기안", "검토", "결재"),
                rows=(("성명", "김주무관", "이과장", ""), ("일자", "2026-07-20", "2026-07-22", "")),
            ),
        ),
    )
    report = check_document_form(shown, _target(AdminStatus.APPROVAL_PENDING))
    assert report.has_approval_block is True
    assert report.passed is True


def test_attachment_missing_status_requires_an_attachment_block():
    without = GeneratedDocumentIR(
        title="자료 송부", blocks=(_header(), ParagraphBlock(block_id="p1", text="본문"))
    )
    assert not check_document_form(
        without, _target(AdminStatus.ATTACHMENT_MISSING)
    ).passed

    with_attachment = GeneratedDocumentIR(
        title="자료 송부",
        blocks=(
            _header(),
            AttachmentReferenceBlock(
                block_id="att", attachment_id="붙임1", label="검토자료 1부"
            ),
        ),
    )
    assert check_document_form(
        with_attachment, _target(AdminStatus.ATTACHMENT_MISSING)
    ).passed


def test_unrelated_documents_are_not_forced_to_carry_an_approval_table():
    """관계없는 문서에 결재란을 강제하면 그것대로 현실에 없는 형태가 된다."""

    document = GeneratedDocumentIR(
        title="입찰 평가 기준", blocks=(_header(), ParagraphBlock(block_id="p1", text="본문"))
    )

    report = check_document_form(document, _target())

    assert report.has_approval_block is False
    assert report.passed is True
