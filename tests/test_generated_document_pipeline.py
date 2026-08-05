from pathlib import Path
import re
from typing import Annotated, get_args, get_origin

import fitz
import pytest
from pydantic import ValidationError
import rd2.generators.document_security_marking as security_marking
import rd2.generators.paged_output as paged_output

from rd2.generators.generated_document_pipeline import (
    AttachmentReferenceBlock,
    FailedGenerationPayloadError,
    GeneratedDocumentContentMismatch,
    GeneratedBlock,
    ParagraphBlock,
    blocks_to_body_text,
    build_template_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.administrative_rule_rendering import (
    ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS,
)
from rd2.generators.document_security_marking import SecurityMarkingError
from rd2.generators.guide_rendering import GUIDE_TEMPLATE_VARIANTS
from rd2.generators.interpretation_compilation_rendering import (
    INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS,
)
from rd2.generators.meeting_minutes_rendering import (
    MEETING_MINUTES_TEMPLATE_VARIANTS,
)
from rd2.generators.notice_rendering import NOTICE_TEMPLATE_VARIANTS
from rd2.generators.official_document_variations import EXPECTED_PAGE_COUNTS
from rd2.generators.paged_output import (
    RenderedSourceTextError,
    _fragmentation_tolerant_text_present,
    _normalized,
    finalize_manifest_page_limits,
    validate_pdf_source_texts,
)
from rd2.generators.press_release_rendering import (
    PRESS_RELEASE_TEMPLATE_VARIANTS,
)
from rd2.generators.research_report_rendering import (
    RESEARCH_REPORT_TEMPLATE_VARIANTS,
)
from rd2.generators.status_report_rendering import (
    STATUS_REPORT_TEMPLATE_VARIANTS,
)
from rd2.source_generation.contracts import DocumentBlock


def _payload() -> dict:
    return {
        "result": {
            "contract_version": "2.0.0",
            "source_classification": {
                "document_type": "policy_material",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generation_target": {
                "classification": "S",
                "clause_no": "5",
                "subclause_key": "bid_contract",
                "generation_mode": "counterfactual",
            },
            "generated_document": {
                "contract_version": "2.0.0",
                "title": "정책연구 평가결과 보고서",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "g1",
                        "text": (
                            "기후변화와 농업역량 강화 방안에 대한 "
                            "정책연구 평가 결과를 보고합니다."
                        ),
                    },
                    {
                        "kind": "key_value",
                        "block_id": "g2",
                        "entries": [
                            {
                                "key": "연구기관/책임연구원",
                                "value": "이엔에스 / 이효정",
                            },
                            {
                                "key": "연구 방식",
                                "value": "위탁형",
                            },
                        ],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "g3",
                        "text": (
                            "정책연구의 목표 부합성과 추진방법의 "
                            "적절성을 평가하였습니다."
                        ),
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "g4",
                        "items": [
                            "정책연구 목적과의 부합성",
                            "추진방법의 적절성",
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "g5",
                        "columns": ["구분", "성명", "역할"],
                        "rows": [
                            ["평가위원", "김동조", "평가"],
                            ["과제담당관", "성현재", "확인"],
                        ],
                    },
                ],
                "body_text": (
                    "기후변화와 농업역량 강화 방안에 대한 "
                    "정책연구 평가 결과를 보고합니다.\n\n"
                    "연구기관/책임연구원: 이엔에스 / 이효정\n"
                    "연구 방식: 위탁형\n\n"
                    "정책연구의 목표 부합성과 추진방법의 "
                    "적절성을 평가하였습니다.\n\n"
                    "- 정책연구 목적과의 부합성\n"
                    "- 추진방법의 적절성\n\n"
                    "구분\t성명\t역할\n"
                    "평가위원\t김동조\t평가\n"
                    "과제담당관\t성현재\t확인"
                ),
            },
        },
        "receipt": {
            "stage": "generation",
            "model_id": "gpt-4o",
            "response_id": "resp-test",
            "request_id": "request-test",
        },
        "provenance": None,
        "failure": None,
    }


_PAGINATION_TEMPLATE_CASES = tuple(
    (document_type, str(variant["slug"]))
    for document_type, variants in (
        ("directive", ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS),
        ("guide", GUIDE_TEMPLATE_VARIANTS),
        (
            "interpretation_compilation",
            INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS,
        ),
        ("meeting_minutes", MEETING_MINUTES_TEMPLATE_VARIANTS),
        ("bid_notice", NOTICE_TEMPLATE_VARIANTS),
        ("press_release", PRESS_RELEASE_TEMPLATE_VARIANTS),
        ("research_report", RESEARCH_REPORT_TEMPLATE_VARIANTS),
        ("status_report", STATUS_REPORT_TEMPLATE_VARIANTS),
    )
    for variant in variants
) + tuple(("policy_material", slug) for slug in EXPECTED_PAGE_COUNTS)


def _dense_mixed_block(index: int, repeated: str) -> dict:
    marker = f"[블록{index:03d}]"
    important = " ※ 중요내용 ※" if index == 20 else ""
    kind = index % 5
    if kind == 0:
        return {
            "kind": "paragraph",
            "block_id": f"stress-{index:03d}",
            "text": f"{marker}{important} {repeated * 4}",
        }
    if kind == 1:
        return {
            "kind": "key_value",
            "block_id": f"stress-{index:03d}",
            "entries": [
                {
                    "key": f"{marker} 핵심항목",
                    "value": f"{important} {repeated * 3}",
                },
                {"key": "검증상태", "value": "전체 원문 유지"},
            ],
        }
    if kind == 2:
        return {
            "kind": "bullet_list",
            "block_id": f"stress-{index:03d}",
            "items": [
                f"{marker}{important} {repeated * 2}",
                f"후속 항목 {repeated * 2}",
                "마지막 항목도 페이지 경계에서 유지됩니다.",
            ],
        }
    if kind == 3:
        return {
            "kind": "table",
            "block_id": f"stress-{index:03d}",
            "columns": ["구분", "내용"],
            "rows": [
                [f"{marker}{important}", repeated * 2],
                ["추가", repeated * 2],
                ["확인", "셀 전체가 페이지 안에 유지됩니다."],
            ],
        }
    return {
        "kind": "attachment_reference",
        "block_id": f"stress-{index:03d}",
        "attachment_id": f"ATT-{index:03d}",
        "label": f"{marker}{important} 장문 붙임",
        "description": repeated * 3,
    }


def _add_document_metadata(payload: dict) -> dict:
    payload["result"]["generated_document"]["document_metadata"] = {
        "approval_line": {
            "slots": [
                {
                    "role": "담당",
                    "name": "김가온",
                    "status": "approved",
                    "approved_at": "2024-12-20",
                    "stamp": {
                        "mode": "synthetic",
                        "stamp_text": "해솔공공서비스원장인",
                        "seed": 18273,
                        "profile": "damaged",
                        "shape": "round",
                    },
                },
                {
                    "role": "과장",
                    "name": "이도담",
                    "status": "approved",
                    "approved_at": "2024-12-21",
                    "stamp": None,
                },
                {
                    "role": "기관장",
                    "name": None,
                    "status": "pending",
                    "approved_at": None,
                    "stamp": None,
                },
            ]
        },
        "administrative_events": [
            {
                "type": "review_deadline",
                "date": "2025-01-15",
                "text": "2025년 1월 15일까지 심사할 예정입니다.",
            }
        ],
    }
    return payload


def _add_attachment_reference(payload: dict) -> dict:
    document = payload["result"]["generated_document"]
    document["blocks"].append(
        {
            "kind": "attachment_reference",
            "block_id": "g6",
            "attachment_id": "att-fire-report",
            "label": "화재현장 출동보고서 1부",
            "description": "끝.",
        }
    )
    document["body_text"] += (
        "\n\n[첨부] 화재현장 출동보고서 1부 (att-fire-report): 끝."
    )
    return payload


def _block_kinds(block_union: object) -> set[str]:
    union = (
        get_args(block_union)[0]
        if get_origin(block_union) is Annotated
        else block_union
    )
    return {
        get_args(model.model_fields["kind"].annotation)[0]
        for model in get_args(union)
    }


def _insert_key_value_block(
    payload: dict,
    *,
    index: int,
    block_id: str,
    key: str,
    value: str,
) -> dict:
    document = payload["result"]["generated_document"]
    document["blocks"].insert(
        index,
        {
            "kind": "key_value",
            "block_id": block_id,
            "entries": [{"key": key, "value": value}],
        },
    )
    return document


def test_renderer_block_kinds_match_generated_document_ir() -> None:
    assert _block_kinds(GeneratedBlock) == _block_kinds(DocumentBlock)


@pytest.mark.parametrize("key", ("문서번호", "시행일자"))
@pytest.mark.parametrize("value", ("", "   "))
def test_renderer_accepts_missing_header_fields_without_draft_status(
    key: str,
    value: str,
) -> None:
    payload = _payload()
    assert "administrative_statuses" not in payload["result"]["generation_target"]
    document = _insert_key_value_block(
        payload,
        index=0,
        block_id="header",
        key=key,
        value=value,
    )
    document["body_text"] = f"{key}: {value}\n\n{document['body_text']}"

    parsed = parse_generation_payload(payload).result.generated_document

    assert parsed.blocks[0].entries[0].key == key
    assert parsed.blocks[0].entries[0].value == value


@pytest.mark.parametrize("key", ("수신", "담당자"))
@pytest.mark.parametrize("value", ("", "   "))
def test_renderer_rejects_other_blank_key_value_fields(
    key: str,
    value: str,
) -> None:
    payload = _payload()
    _insert_key_value_block(
        payload,
        index=0,
        block_id="header",
        key=key,
        value=value,
    )

    with pytest.raises(
        ValidationError,
        match="blank key-value is only allowed for draft document number/date",
    ):
        parse_generation_payload(payload)


def test_renderer_rejects_blank_key_after_key_validator_refactor() -> None:
    payload = _payload()
    _insert_key_value_block(
        payload,
        index=0,
        block_id="header",
        key="   ",
        value="기획과-17",
    )

    with pytest.raises(ValidationError, match="key must not be blank"):
        parse_generation_payload(payload)


def test_renderer_rejects_blank_draft_header_field_outside_first_block() -> None:
    payload = _payload()
    _insert_key_value_block(
        payload,
        index=1,
        block_id="late-header",
        key="시행일자",
        value="",
    )

    with pytest.raises(
        ValidationError,
        match="blank draft header values are only allowed in the first block",
    ):
        parse_generation_payload(payload)


def test_structured_blocks_are_the_source_of_truth_for_template_context() -> None:
    envelope = parse_generation_payload(_payload())
    document = envelope.result.generated_document
    context = build_template_context(envelope, seed=100)

    assert blocks_to_body_text(document.blocks) == document.body_text
    assert context["title"] == "정책연구 평가결과 보고서"
    assert context["intro"].endswith("평가 결과를 보고합니다.")
    assert context["details"] == [
        {"label": "연구기관/책임연구원", "value": "이엔에스 / 이효정"},
        {"label": "연구 방식", "value": "위탁형"},
    ]
    assert context["table"]["headers"] == ["구분", "성명", "역할"]
    assert context["table"]["rows"][1] == ["과제담당관", "성현재", "확인"]
    assert "정책연구 목적과의 부합성" in source_text_atoms(document)


def test_official_context_compacts_marked_heading_and_bullet_blocks() -> None:
    payload = _payload()
    payload["result"]["generated_document"]["blocks"] = [
        {
            "kind": "paragraph",
            "block_id": "heading",
            "text": "□ 추진 배경 및 경위임",
        },
        {
            "kind": "bullet_list",
            "block_id": "items",
            "items": [
                "○ 첫 번째 확인사항임",
                "- 두 번째 확인사항임",
                "※ 외부 공유 금지임",
            ],
        },
        {
            "kind": "paragraph",
            "block_id": "intro",
            "text": "일반적인 설명 문장입니다.",
        },
    ]
    payload["result"]["generated_document"]["body_text"] = (
        "□ 추진 배경 및 경위임\n\n"
        "○ 첫 번째 확인사항임\n"
        "- 두 번째 확인사항임\n"
        "※ 외부 공유 금지임\n\n"
        "일반적인 설명 문장입니다."
    )

    envelope = parse_generation_payload(payload)
    context = build_template_context(envelope, seed=100)

    assert context["intro"] == ""
    assert context["sections"][0] == {
        "text": "추진 배경 및 경위임",
        "items": [
            {
                "label": "가",
                "text": "첫 번째 확인사항임",
                "level": 0,
                "role": "bullet",
            },
            {
                "label": "",
                "text": "두 번째 확인사항임",
                "level": 1,
                "role": "bullet",
            },
            {
                "label": "",
                "text": "참고: 외부 공유 금지임",
                "level": 0,
                "role": "note",
            },
        ],
    }
    assert context["sections"][1]["text"] == "일반적인 설명 문장입니다."
    assert len(context["source_blocks"]) == 2
    assert context["source_blocks"][0]["kind"] == "paragraph"
    assert "□" not in context["source_blocks"][0]["text"]
    assert "○" not in context["source_blocks"][0]["text"]
    assert "※" not in context["source_blocks"][0]["text"]


def test_attachment_reference_matches_generated_document_ir_contract() -> None:
    envelope = parse_generation_payload(_add_attachment_reference(_payload()))
    document = envelope.result.generated_document
    attachment = document.blocks[-1]
    context = build_template_context(envelope, seed=100)

    assert isinstance(attachment, AttachmentReferenceBlock)
    assert attachment.render_text() == (
        "[첨부] 화재현장 출동보고서 1부 (att-fire-report): 끝."
    )
    assert blocks_to_body_text(document.blocks) == document.body_text
    assert context["attachments"] == [
        "화재현장 출동보고서 1부 (att-fire-report): 끝."
    ]
    assert {"att-fire-report", "화재현장 출동보고서 1부", "끝."} <= set(
        source_text_atoms(document)
    )


def test_all_templates_render_attachment_reference(tmp_path: Path) -> None:
    manifest = render_generation_payload(
        _add_attachment_reference(_payload()),
        tmp_path,
        per_template=1,
        base_seed=20260728,
    )

    assert len(manifest) == 10
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["source_text_present"] is True for entry in manifest)
    for entry in manifest:
        with fitz.open(str(entry["pdf"])) as pdf:
            rendered_text = "".join(
                page.get_text() for page in pdf
            ).replace("\n", "")
        assert "화재현장 출동보고서 1부" in rendered_text
        assert "att-fire-report" in rendered_text
        assert "끝." in rendered_text


def test_pipeline_can_render_one_selected_structural_variation(
    tmp_path: Path,
) -> None:
    manifest = render_generation_payload(
        _payload(),
        tmp_path,
        per_template=1,
        base_seed=20260730,
        variation_offset=2,
        template_slugs={"01_classic_municipal"},
    )

    assert len(manifest) == 1
    assert manifest[0]["template_slug"] == "01_classic_municipal"
    assert str(manifest[0]["variation_slug"]).startswith("03_")


def test_missing_document_metadata_is_not_synthesized() -> None:
    envelope = parse_generation_payload(_payload())
    context = build_template_context(envelope, seed=100)

    assert context["source_agency_name"] == ""
    assert context["source_agency_category"] == ""
    assert context["signers"] == []
    for key in (
        "recipient",
        "via",
        "issuer_title",
        "copy_recipients",
        "document_number",
        "issue_date",
        "postal_code",
        "address",
        "website",
        "phone",
        "fax",
        "email",
        "venue",
        "disclosure",
        "slogan",
        "brand_note",
    ):
        assert context[key] == ""


def test_optional_approval_metadata_is_rendered_without_filling_blanks() -> None:
    envelope = parse_generation_payload(_add_document_metadata(_payload()))
    document = envelope.result.generated_document
    context = build_template_context(envelope, seed=100)

    assert [signer["role"] for signer in context["signers"]] == [
        "담당",
        "과장",
        "기관장",
    ]
    assert context["signers"][0]["stamp_data_uri"].startswith(
        "data:image/png;base64,"
    )
    assert context["signers"][0]["date"] == "2024-12-20"
    assert context["signers"][1]["stamp_data_uri"] == ""
    assert context["signers"][2]["name"] == ""
    assert context["signers"][2]["date"] == ""
    assert context["approval_manifest"][0]["stamp"]["parameters"][
        "profile"
    ] == "damaged"
    assert context["approval_manifest"][0]["stamp"]["parameters"][
        "shape"
    ] == "round"
    assert context["approval_manifest"][0]["stamp"]["placement"]["mode"] in {
        "standard",
        "boundary",
        "lower_overlap",
    }
    assert context["administrative_events"] == [
        {
            "type": "review_deadline",
            "date": "2025-01-15",
            "text": "2025년 1월 15일까지 심사할 예정입니다.",
        }
    ]
    assert "2025년 1월 15일까지 심사할 예정입니다." in {
        section["text"] for section in context["sections"]
    }
    atoms = source_text_atoms(document)
    assert "담당" in atoms
    assert "김가온" in atoms
    assert "2024-12-20" in atoms
    assert "2025년 1월 15일까지 심사할 예정입니다." in atoms


def test_stamp_and_approval_date_are_rejected_for_unapproved_slot() -> None:
    payload = _add_document_metadata(_payload())
    pending = payload["result"]["generated_document"]["document_metadata"][
        "approval_line"
    ]["slots"][2]
    pending["stamp"] = {
        "mode": "synthetic",
        "stamp_text": "합성확인인",
    }

    with pytest.raises(
        ValidationError,
        match="stamp is only allowed when status is approved",
    ):
        parse_generation_payload(payload)

    pending["stamp"] = None
    pending["approved_at"] = "2024-12-22"
    with pytest.raises(
        ValidationError,
        match="approved_at is only allowed when status is approved",
    ):
        parse_generation_payload(payload)


def test_body_text_mismatch_is_rejected_before_rendering() -> None:
    payload = _payload()
    payload["result"]["generated_document"]["body_text"] += "\n변조된 내용"

    with pytest.raises(GeneratedDocumentContentMismatch):
        parse_generation_payload(payload)


def test_failed_generation_is_rejected_unless_explicitly_allowed() -> None:
    payload = _payload()
    payload["failure"] = {
        "stage": "generation",
        "code": "evidence_invalid",
        "retryable": False,
        "message": "evidence quote mismatch",
    }

    with pytest.raises(FailedGenerationPayloadError, match="evidence_invalid"):
        parse_generation_payload(payload)

    envelope = parse_generation_payload(payload, allow_failed=True)
    assert envelope.failure is not None


def test_body_text_only_input_falls_back_to_unmodified_paragraph_blocks() -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["blocks"] = []
    document["body_text"] = "첫 번째 문단입니다.\n\n두 번째 문단입니다."

    parsed = parse_generation_payload(payload).result.generated_document

    assert all(isinstance(block, ParagraphBlock) for block in parsed.blocks)
    assert [block.text for block in parsed.blocks] == [
        "첫 번째 문단입니다.",
        "두 번째 문단입니다.",
    ]


def test_invalid_table_width_and_contract_drift_are_rejected() -> None:
    payload = _payload()
    payload["result"]["generated_document"]["blocks"][-1]["rows"][0].pop()

    with pytest.raises(ValidationError, match="expected 3"):
        parse_generation_payload(payload)

    payload = _payload()
    payload["result"]["contract_version"] = "1.0.0"
    payload["result"]["generated_document"]["contract_version"] = "1.0.0"

    with pytest.raises(ValidationError, match="expected 2.x.x"):
        parse_generation_payload(payload)


def test_minor_contract_version_drift_from_the_source_module_is_accepted() -> None:
    """rd2.source_generation.contracts는 이 모듈을 import하지 않고 IR 형태를
    독자적으로 재선언한다. 그 소스 모듈의 ``CONTRACT_SCHEMA_VERSION``은
    ``SourceAssessment`` 등 여러 계약이 공유하는 단일 상수라, 이 모듈이 실제로
    쓰는 ``GeneratedDocumentIR``의 필드가 안 바뀌어도(예: primary_subclause
    필드 추가로 2.0.0 -> 2.1.0) 마이너 버전이 오를 수 있다. v1 같은 실제 구조
    드리프트만 막고 마이너 버전은 받아야 한다.
    """

    payload = _payload()
    payload["result"]["contract_version"] = "2.1.0"
    payload["result"]["generated_document"]["contract_version"] = "2.1.0"

    parsed = parse_generation_payload(payload)

    assert parsed.result.contract_version == "2.1.0"


def test_v2_allows_metadata_but_rejects_unknown_block_kind() -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["trace_id"] = "trace-123"
    document["blocks"][0]["source_note"] = "compatible metadata"

    envelope = parse_generation_payload(payload)

    assert envelope.result.generated_document.trace_id == "trace-123"
    assert envelope.result.generated_document.blocks[0].source_note == (
        "compatible metadata"
    )

    payload = _payload()
    payload["result"]["generated_document"]["blocks"][0]["kind"] = "unknown"

    with pytest.raises(ValidationError):
        parse_generation_payload(payload)


def test_all_templates_preserve_every_source_atom(tmp_path: Path) -> None:
    manifest = render_generation_payload(
        _payload(),
        tmp_path,
        per_template=1,
        base_seed=20260728,
    )

    assert len(manifest) == 10
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["source_text_present"] is True for entry in manifest)
    assert all(entry["input"]["content_sha256"] for entry in manifest)
    assert all(Path(str(entry["pdf"])).is_file() for entry in manifest)
    assert all(Path(str(entry["html"])).is_file() for entry in manifest)
    assert all("security_marking" not in entry for entry in manifest)
    assert all(entry["identity"]["identity_source"] == "none" for entry in manifest)
    assert all(entry["identity"]["agency_name"] == "" for entry in manifest)
    assert all(entry["identity"]["agency_pool_index"] is None for entry in manifest)
    assert all(entry["identity"]["agency_pool_size"] == 0 for entry in manifest)
    assert all(entry["identity"]["organization_category"] is None for entry in manifest)
    assert all(entry["identity"]["selection_category"] is None for entry in manifest)

    rendered_text = ""
    for entry in manifest:
        with fitz.open(str(entry["pdf"])) as pdf:
            rendered_text += "\n".join(page.get_text() for page in pdf)
    assert "사전 점검 항목은 계획에 따라 이행 중" not in rendered_text
    assert "행사 정보" not in rendered_text
    assert "프로그램 운영 구성" not in rendered_text
    assert "현장 확인 서식" not in rendered_text
    assert "붙임" not in rendered_text
    assert "첨부 문서" not in rendered_text
    for synthetic_text in (
        "김가온",
        "이도담",
        "박하람",
        "가상문서-",
        "관계기관장",
        "업무담당부서",
        "02-0000-0000",
        "비공개",
        "공개구분",
        "협조자",
        "직인생략",
        "최종 판정",
        "조건부 적합",
        "재점검",
        "라온공공정책지원원",
    ):
        assert synthetic_text not in rendered_text


def test_official_continuation_keeps_source_content_out_of_preview_cover(
    tmp_path: Path,
) -> None:
    """장문 공문은 서식 표지와 원본 연속본문을 중복 출력하지 않는다."""

    payload = _payload()
    document = payload["result"]["generated_document"]
    document["title"] = "장문 공문 중복 방지 회귀 테스트"
    document["body_text"] = None
    document["blocks"] = [
        {
            "kind": "paragraph",
            "block_id": f"continuation-{index:02d}",
            "text": f"[연속원문{index:02d}] 이 항목은 한 번만 출력되어야 합니다.",
        }
        for index in range(1, 14)
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260805,
    )

    assert len(manifest) == 10
    for entry in manifest:
        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        assert 'class="pagination-continuation"' in html
        assert "<main class=\"pagination-continuation\">\n  <h1>" not in html

        with fitz.open(str(entry["pdf"])) as rendered:
            rendered_text = _normalized(
                "\n".join(page.get_text() for page in rendered)
            )
        assert rendered_text.count(_normalized(document["title"])) == 1
        for index in range(1, 14):
            assert rendered_text.count(f"[연속원문{index:02d}]") == 1


def test_c_payload_without_military_grade_gets_confidential_security_skin(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["result"]["generation_target"] = {
        "classification": "C",
        "clause_no": "2",
        "subclause_key": "security_defense",
        "generation_mode": "counterfactual",
    }
    payload["result"]["generated_document"]["agency_name"] = "행정안전부"

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260803,
        template_slugs={"01_classic_municipal"},
    )

    marking = manifest[0]["security_marking"]
    assert marking["kind"] == "confidential"
    assert marking["asset"] == "logo/synthetic_confidential.png"
    assert marking["asset_kind"] == "synthetic_security_stamp"
    assert marking["security_template"]["slug"] == "04_restricted_memo"
    assert marking["palette"]["ink_hex"] == "#22272C"
    assert "agency_marking" not in manifest[0]
    assert manifest[0]["input"]["generation_target"][
        "military_secret_grade"
    ] is None


def test_pipeline_keeps_first_twelve_pages_after_natural_layout(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["result"]["source_classification"]["document_type"] = "guide"
    payload["result"]["generation_target"] = {
        "classification": "C",
        "clause_no": "2",
        "subclause_key": "security_defense",
        "generation_mode": "counterfactual",
    }
    document = payload["result"]["generated_document"]
    document["agency_name"] = "행정안전부"
    repeated = (
        "장문 블록은 페이지 경계에서 문장 순서를 유지해야 하며 "
        "앞뒤 블록과 겹치지 않고 다음 페이지로 자연스럽게 이어져야 합니다. "
    )
    document["title"] = "12페이지 절단 회귀 테스트"
    document["body_text"] = None
    document["blocks"] = [
        {
            "kind": "paragraph",
            "block_id": f"stress-{index:03d}",
            "text": (
                f"[블록{index:03d}시작] {repeated * 5} "
                f"[블록{index:03d}종료]"
            ),
        }
        for index in range(1, 81)
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260803,
        template_slugs={"guide_01_classic"},
    )

    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["status"] == "ok_truncated"
    assert entry["truncated"] is True
    assert entry["original_page_count"] > 12
    assert entry["retained_page_count"] == 12
    assert entry["discarded_page_count"] == entry["original_page_count"] - 12
    assert entry["actual_pages"] == 12
    with fitz.open(str(entry["pdf"])) as document_pdf:
        assert document_pdf.page_count == 12


def test_dense_field_report_keeps_twelve_body_pages_plus_unlimited_cover(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["result"]["source_classification"][
        "document_type"
    ] = "policy_material"
    payload["result"]["generation_target"] = {
        "classification": "C",
        "clause_no": "2",
        "subclause_key": "security_defense",
        "generation_mode": "counterfactual",
        "military_secret_grade": "3급",
    }
    document = payload["result"]["generated_document"]
    document["agency_name"] = "행정안전부"
    document["title"] = "현장보고형 대외비 표지 안전영역 회귀 테스트"
    document["body_text"] = None
    repeated = (
        "장문 블록은 페이지 경계에서 순서를 유지해야 하며 "
        "보안표지와 겹치지 않고 다음 페이지로 이어져야 합니다. "
    )
    document["blocks"] = [
        _dense_mixed_block(index, repeated)
        for index in range(1, 81)
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260803,
        template_slugs={"05_field_report"},
    )

    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["status"] == "ok_truncated"
    assert entry["actual_pages"] == 12
    assert entry["source_text_present"] is True
    marking = entry["security_marking"]
    assert marking["kind"] == "military_secret"
    assert marking["content_page_count"] == 12
    assert marking["final_pdf_page_count"] == 13
    assert marking["placement"]["cover"]["counted_in_page_limit"] is False
    with fitz.open(str(entry["pdf"])) as rendered:
        assert rendered.page_count == 13
        assert len(rendered[0].get_images(full=True)) == 1
        assert "[블록" not in rendered[0].get_text()
        assert "현장보고형" in rendered[1].get_text()
        assert any(
            "[블록" in rendered[page_number].get_text()
            for page_number in range(2, rendered.page_count)
        )


@pytest.mark.parametrize(
    ("document_type", "template_slug"),
    _PAGINATION_TEMPLATE_CASES,
    ids=[slug for _, slug in _PAGINATION_TEMPLATE_CASES],
)
def test_every_template_keeps_dense_blocks_inside_page_bounds(
    tmp_path: Path,
    document_type: str,
    template_slug: str,
) -> None:
    payload = _payload()
    payload["result"]["source_classification"][
        "document_type"
    ] = document_type
    document = payload["result"]["generated_document"]
    repeated = (
        "다중 블록 본문은 페이지 경계에서 순서를 유지해야 하며 "
        "앞뒤 블록과 겹치거나 페이지 바깥으로 잘리면 안 됩니다. "
    )
    document["title"] = f"{template_slug} 전수 페이지 분할 테스트"
    document["body_text"] = None
    document["blocks"] = [
        _dense_mixed_block(index, repeated)
        for index in range(1, 81)
    ]

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260803,
        template_slugs={template_slug},
    )

    assert len(manifest) == 1
    entry = manifest[0]
    assert entry["status"] == "ok_truncated"
    assert entry["source_text_present"] is True
    assert entry["source_text_validation_scope"] == "pre_truncation_pdf"
    assert entry["truncated"] is True
    assert entry["original_page_count"] > 12
    assert entry["retained_page_count"] == 12
    assert entry["discarded_page_count"] > 0

    with fitz.open(str(entry["pdf"])) as rendered:
        assert rendered.page_count == 12
        retained_text = "\n".join(page.get_text() for page in rendered)
        assert "중요내용" in retained_text
        for page in rendered:
            marker_blocks = [
                block
                for block in page.get_text("blocks", sort=False)
                if int(block[6]) == 0
                and re.search(r"\[블록\d{3}\]", str(block[4]))
            ]
            for block in marker_blocks:
                rectangle = fitz.Rect(*block[:4])
                assert rectangle.x0 >= -1
                assert rectangle.y0 >= -1
                assert rectangle.x1 <= page.rect.x1 + 1
                assert rectangle.y1 <= page.rect.y1 + 1
            for index, left in enumerate(marker_blocks):
                left_rect = fitz.Rect(*left[:4])
                for right in marker_blocks[index + 1 :]:
                    right_rect = fitz.Rect(*right[:4])
                    intersection = left_rect & right_rect
                    if intersection.is_empty:
                        continue
                    overlap_ratio = intersection.get_area() / min(
                        left_rect.get_area(),
                        right_rect.get_area(),
                    )
                    assert overlap_ratio < 0.08


def test_military_c_payload_renders_explicit_grade_and_falls_back_to_confidential(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["result"]["generation_target"] = {
        "classification": "C",
        "clause_no": "2",
        "subclause_key": "security_defense",
        "generation_mode": "counterfactual",
        "military_secret_grade": "2급",
    }
    payload["result"]["generated_document"]["agency_name"] = "국방부"

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260803,
        template_slugs={"03_internal_approval"},
    )

    marking = manifest[0]["security_marking"]
    assert marking["kind"] == "military_secret"
    assert marking["military_secret_grade"] == "2급"
    assert marking["placement"]["strategy"] == (
        "front_cover_top_bottom_and_neutral_frame"
    )
    with fitz.open(str(manifest[0]["pdf"])) as document:
        assert document.page_count == manifest[0]["actual_pages"] + 1
        assert len(document[0].get_images(full=True)) == 1
        assert all(page.get_images(full=True) for page in document.pages(1))

    payload["result"]["generation_target"].pop("military_secret_grade")
    confidential_manifest = render_generation_payload(
        payload,
        tmp_path / "missing-grade",
        per_template=1,
        base_seed=20260803,
        template_slugs={"03_internal_approval"},
    )
    confidential = confidential_manifest[0]["security_marking"]
    assert confidential["kind"] == "confidential"
    assert "military_secret_grade" not in confidential
    with fitz.open(str(confidential_manifest[0]["pdf"])) as document:
        assert document.page_count == confidential_manifest[0]["actual_pages"]


def test_long_source_validation_rejects_middle_corruption() -> None:
    source = "가" * 48 + "원문중간구간" * 40 + "나" * 48
    corrupted = "가" * 48 + "변조된중간구간" * 40 + "나" * 48

    assert not _fragmentation_tolerant_text_present(
        source,
        _normalized(corrupted),
    )


def test_source_validation_accepts_pdf_typography_and_hwp_control_equivalents() -> None:
    """보이는 본문은 같은데 추출 표현만 달라진 두 실측 사례를 허용한다."""

    rendered = _normalized("서약서 관계공무원에게 취업을 알선·제공하지 않는다.")

    assert _fragmentation_tolerant_text_present(
        "서\x01 \x01 약\x01 \x01 서",
        rendered,
    )
    assert _fragmentation_tolerant_text_present(
        "관계공무원에게 취업을 알선・제공하지 않는다.",
        rendered,
    )


def test_source_validation_accepts_bounded_page_furniture_in_table_cell() -> None:
    source = (
        "구 분 · 예산 구분 · 세부항목 · 산출내역 · 금액 (천원) · 비율(%) "
        "구분 · 전체 국고 보조금 · 인건비 운영비 사업비 소 계 · 100 기타"
    )
    split_at = source.index("사업비")
    rendered = _normalized(
        source[:split_at] + " 공모 보건복지부 - 6 - " + source[split_at:]
    )

    assert 20 <= len(_normalized(source)) < 80
    assert _fragmentation_tolerant_text_present(source, rendered)


def test_source_validation_rejects_unbounded_fragmentation_for_medium_text() -> None:
    source = "중간길이원문" * 6
    rendered = _normalized(source[:18] + ("삽입문자" * 80) + source[18:])

    assert not _fragmentation_tolerant_text_present(source, rendered)


def test_missing_source_error_does_not_echo_confidential_text(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "missing-source.pdf"
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "렌더링된 공개 문장")
    document.save(pdf_path)
    document.close()
    secret = "외부 로그에 남으면 안 되는 기밀 원문"

    with pytest.raises(RenderedSourceTextError) as error:
        validate_pdf_source_texts(pdf_path, (secret,))

    assert secret not in str(error.value)


def test_c_marking_failure_removes_unmarked_rendered_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    payload["result"]["generation_target"] = {
        "classification": "C",
        "clause_no": "2",
        "subclause_key": "security_defense",
        "generation_mode": "counterfactual",
        "military_secret_grade": "1급",
    }
    payload["result"]["generated_document"]["agency_name"] = "행정안전부"
    output_dir = tmp_path / "failed-marking"
    monkeypatch.setitem(
        security_marking.MILITARY_SECRET_MARK_FILENAMES,
        "1급",
        "missing-military-mark.png",
    )

    with pytest.raises(SecurityMarkingError, match="이미지가 없습니다"):
        render_generation_payload(
            payload,
            output_dir,
            per_template=1,
            base_seed=20260803,
            template_slugs={"01_classic_municipal"},
        )

    assert not list(output_dir.rglob("*.pdf"))
    assert not list(output_dir.rglob("*.html"))


def test_page_limit_batch_validates_all_pdfs_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_paths: list[Path] = []
    for index in range(2):
        pdf_path = tmp_path / f"batch-{index}.pdf"
        document = fitz.open()
        for page_number in range(12):
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 100), f"batch {index} page {page_number}")
        document.save(pdf_path)
        document.close()
        pdf_paths.append(pdf_path)

    original_bytes = [path.read_bytes() for path in pdf_paths]
    calls = 0

    def validate_then_fail(_path: Path, _texts: tuple[str, ...]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RenderedSourceTextError("missing source")

    monkeypatch.setattr(
        paged_output,
        "validate_pdf_source_texts",
        validate_then_fail,
    )
    manifest = [
        {"status": "ok", "pdf": str(pdf_path)}
        for pdf_path in pdf_paths
    ]

    with pytest.raises(RenderedSourceTextError, match="missing source"):
        finalize_manifest_page_limits(
            manifest,
            max_pages=10,
            render_page_budget=1_000,
            required_source_texts=("원문",),
        )

    assert [path.read_bytes() for path in pdf_paths] == original_bytes
    assert all("truncated" not in entry for entry in manifest)


def test_all_templates_render_typed_approval_stamps(tmp_path: Path) -> None:
    manifest = render_generation_payload(
        _add_document_metadata(_payload()),
        tmp_path,
        per_template=1,
        base_seed=20260728,
    )

    assert len(manifest) == 10
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(
        entry["approval"][0]["stamp"]["parameters"]["profile"] == "damaged"
        for entry in manifest
    )
    assert {
        entry["approval"][0]["stamp"]["parameters"]["seed"]
        for entry in manifest
    } == {18273}
    for entry in manifest:
        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        assert "data:image/png;base64," in html
        with fitz.open(str(entry["pdf"])) as pdf:
            rendered_text = "\n".join(page.get_text() for page in pdf)
        assert "김가온" in rendered_text
        normalized_text = "".join(rendered_text.split())
        assert "기관장" in normalized_text
        assert "2025년1월15일까지심사할예정입니다." in normalized_text


def test_four_signers_expand_grid_templates_to_four_columns(
    tmp_path: Path,
) -> None:
    payload = _add_document_metadata(_payload())
    slots = payload["result"]["generated_document"]["document_metadata"][
        "approval_line"
    ]["slots"]
    slots.append(
        {
            "role": "승인",
            "name": "박하람",
            "status": "approved",
            "approved_at": "2024-12-22",
            "stamp": {
                "mode": "synthetic",
                "stamp_text": "박하람인",
                "seed": 10006,
                "profile": "normal",
                "shape": "square",
            },
        }
    )

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260728,
        template_slugs={"04_personnel_notice"},
    )

    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")
    assert "signer-count-4" in html
    assert manifest[0]["actual_pages"] == 1
    assert manifest[0]["status"] == "ok"


def test_input_agency_name_is_preserved_and_disables_synthetic_identity(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["result"]["generated_document"]["agency_name"] = "고용노동부"

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260728,
        template_slugs={"03_internal_approval"},
    )

    identity = manifest[0]["identity"]
    assert identity["identity_source"] == "input"
    assert identity["agency_name"] == "고용노동부"
    assert identity["agency_pool_index"] is None
    assert identity["profile"] == "none"
    with fitz.open(str(manifest[0]["pdf"])) as pdf:
        rendered_text = "\n".join(page.get_text() for page in pdf)
    assert "고용노동부" in rendered_text
    assert "다온" not in rendered_text


def test_source_fields_are_protected_from_identity_token_replacement(
    tmp_path: Path,
) -> None:
    payload = _payload()
    document = payload["result"]["generated_document"]
    document["title"] = "한빛 정책연구 평가결과"
    old_text = document["blocks"][0]["text"]
    document["blocks"][0]["text"] = "한빛 연구 결과를 보고합니다."
    document["body_text"] = document["body_text"].replace(
        old_text,
        document["blocks"][0]["text"],
    )

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260728,
        template_slugs={"01_classic_municipal"},
    )

    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")
    assert "한빛 정책연구 평가결과" in html
    assert "한빛 연구 결과를 보고합니다." in html
