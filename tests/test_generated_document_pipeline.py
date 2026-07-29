from pathlib import Path

import fitz
import pytest
from pydantic import ValidationError

from rd2.generators.generated_document_pipeline import (
    FailedGenerationPayloadError,
    GeneratedDocumentContentMismatch,
    ParagraphBlock,
    blocks_to_body_text,
    build_template_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)


def _payload() -> dict:
    return {
        "result": {
            "contract_version": "1.0.0",
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
                "contract_version": "1.0.0",
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
            "stage": "pass1",
            "model_id": "gpt-4o",
            "response_id": "resp-test",
            "request_id": "request-test",
        },
        "provenance": None,
        "failure": None,
    }


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


def test_missing_document_metadata_is_not_synthesized() -> None:
    envelope = parse_generation_payload(_payload())
    context = build_template_context(envelope, seed=100)

    assert context["source_agency_name"] == ""
    assert context["source_agency_category"] == "generic_public"
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


def test_body_text_mismatch_is_rejected_before_rendering() -> None:
    payload = _payload()
    payload["result"]["generated_document"]["body_text"] += "\n변조된 내용"

    with pytest.raises(GeneratedDocumentContentMismatch):
        parse_generation_payload(payload)


def test_failed_generation_is_rejected_unless_explicitly_allowed() -> None:
    payload = _payload()
    payload["failure"] = {
        "stage": "pass1",
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
    payload["result"]["contract_version"] = "2.0.0"
    payload["result"]["generated_document"]["contract_version"] = "2.0.0"

    with pytest.raises(ValidationError, match="expected 1.x.x"):
        parse_generation_payload(payload)


def test_same_major_version_allows_metadata_but_rejects_unknown_block_kind() -> None:
    payload = _payload()
    payload["result"]["contract_version"] = "1.1.0"
    document = payload["result"]["generated_document"]
    document["contract_version"] = "1.1.0"
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
    assert len(
        {entry["identity"]["agency_name"] for entry in manifest}
    ) == 1
    assert len(
        {entry["identity"]["agency_pool_index"] for entry in manifest}
    ) == 1
    assert all(entry["identity"]["agency_pool_size"] == 300 for entry in manifest)
    assert all(
        entry["identity"]["organization_category"] == "generic_public"
        for entry in manifest
    )
    assert all(
        entry["identity"]["selection_category"] == "generic_public"
        for entry in manifest
    )

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
    ):
        assert synthetic_text not in rendered_text


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
