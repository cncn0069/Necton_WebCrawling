import json
from pathlib import Path

import fitz
import pytest
from pydantic import ValidationError

from rd2.generators.generated_document_pipeline import (
    build_research_report_context,
    parse_generation_payload,
    render_generation_payload,
    source_text_atoms,
)
from rd2.generators.research_report_rendering import (
    build_research_variation_specs,
    render_research_report_variations,
)
from scripts.render_generated_documents import render_input_file


def _research_payload() -> dict:
    return {
        "result": {
            "contract_version": "2.0.0",
            "source_classification": {
                "document_type": "research_report",
                "classification": "O",
            },
            "generation_route": "fully_synthetic",
            "generated_document": {
                "contract_version": "2.0.0",
                "title": "장기 정책 효과와 현장 적용 가능성에 관한 종합 연구",
                "agency_name": None,
                "document_metadata": {
                    "approval_line": {
                        "slots": [
                            {
                                "role": "검토",
                                "name": "김가온",
                                "status": "approved",
                                "approved_at": "2026-07-29",
                                "stamp": {
                                    "mode": "synthetic",
                                    "stamp_text": "김가온인",
                                    "seed": 4217,
                                    "profile": "dry_ink",
                                    "shape": "round",
                                },
                            }
                        ]
                    },
                    "administrative_events": [
                        {
                            "type": "review_deadline",
                            "date": "2026-08-15",
                            "text": "후속 검토 결과는 지정된 절차에 따라 반영합니다.",
                        }
                    ],
                },
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "r1",
                        "text": "첫번째고유문단은 연구의 목적과 적용 범위를 설명합니다.",
                    },
                    {
                        "kind": "key_value",
                        "block_id": "r2",
                        "entries": [
                            {"key": "수행 방식", "value": "혼합형 조사"},
                            {"key": "검토 범위", "value": "전국 단위 사례"},
                        ],
                    },
                    {
                        "kind": "table",
                        "block_id": "r3",
                        "columns": [
                            "구분",
                            "지표",
                            "기준",
                            "상반기",
                            "하반기",
                            "변화",
                            "검토",
                            "비고",
                        ],
                        "rows": [
                            [
                                "성과",
                                "접근성",
                                "기준값",
                                "초기값",
                                "후기값",
                                "증가",
                                "적정",
                                "계속",
                            ]
                        ],
                    },
                    {
                        "kind": "paragraph",
                        "block_id": "r4",
                        "text": "두번째고유문단은 표 다음의 해석과 한계를 설명합니다.",
                    },
                    {
                        "kind": "bullet_list",
                        "block_id": "r5",
                        "items": ["첫번째고유항목", "두번째고유항목"],
                    },
                    {
                        "kind": "table",
                        "block_id": "r6",
                        "columns": ["단계", "내용"],
                        "rows": [["후속", "별도 검토"]],
                    },
                    {
                        "kind": "attachment_reference",
                        "block_id": "r7",
                        "attachment_id": "research-appendix-very-long-identifier-001",
                        "label": "고유붙임자료 1부",
                        "description": "세부 산출 근거",
                    },
                ],
                "body_text": (
                    "첫번째고유문단은 연구의 목적과 적용 범위를 설명합니다.\n\n"
                    "수행 방식: 혼합형 조사\n"
                    "검토 범위: 전국 단위 사례\n\n"
                    "구분\t지표\t기준\t상반기\t하반기\t변화\t검토\t비고\n"
                    "성과\t접근성\t기준값\t초기값\t후기값\t증가\t적정\t계속\n\n"
                    "두번째고유문단은 표 다음의 해석과 한계를 설명합니다.\n\n"
                    "- 첫번째고유항목\n"
                    "- 두번째고유항목\n\n"
                    "단계\t내용\n"
                    "후속\t별도 검토\n\n"
                    "[첨부] 고유붙임자료 1부 "
                    "(research-appendix-very-long-identifier-001): 세부 산출 근거"
                ),
            },
        },
        "receipt": {
            "model_id": "gpt-4o",
            "response_id": "research-response",
            "request_id": "research-request",
        },
        "failure": None,
    }


def _pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "".join(page.get_text() for page in document)


def test_research_context_preserves_block_order_without_synthetic_agency() -> None:
    envelope = parse_generation_payload(_research_payload())
    context = build_research_report_context(envelope, seed=100)

    assert envelope.result.source_classification is not None
    assert envelope.result.source_classification.document_type == "research_report"
    assert context["agency_name"] == ""
    assert [block["block_id"] for block in context["blocks"]] == [
        "r1",
        "r2",
        "r3",
        "r4",
        "r5",
        "r6",
        "r7",
    ]
    assert [block["kind"] for block in context["blocks"]].count("table") == 2
    assert context["blocks"][2]["is_wide"] is True
    assert context["blocks"][5]["is_wide"] is False
    assert context["administrative_events"][0]["text"].startswith("후속 검토")
    assert context["signers"][0]["stamp_data_uri"].startswith(
        "data:image/png;base64,"
    )


def test_research_variation_specs_are_deterministic_and_change_structure() -> None:
    first = build_research_variation_specs(
        "research_02_modular_policy",
        count=3,
        base_seed=91,
    )
    second = build_research_variation_specs(
        "research_02_modular_policy",
        count=3,
        base_seed=91,
    )

    assert first == second
    assert [(spec.key_value_columns, spec.list_columns) for spec in first] == [
        (3, 2),
        (2, 1),
        (1, 2),
    ]
    assert len({spec.horizontal_margin_mm for spec in first}) == 3


def test_research_renderer_rejects_invalid_selection_arguments(
    tmp_path: Path,
) -> None:
    envelope = parse_generation_payload(_research_payload())
    context = build_research_report_context(envelope, seed=100)

    with pytest.raises(ValueError, match="Unknown research report template"):
        build_research_variation_specs("unknown-template")
    with pytest.raises(ValueError, match="count must be at least 1"):
        build_research_variation_specs(
            "research_01_classic_flow",
            count=0,
        )
    with pytest.raises(ValueError, match="count must be at most 10"):
        build_research_variation_specs(
            "research_01_classic_flow",
            count=11,
        )
    with pytest.raises(ValueError, match="per_template must be at least 1"):
        render_research_report_variations(
            context,
            tmp_path,
            per_template=0,
        )
    with pytest.raises(ValueError, match="per_template must be at most 10"):
        render_research_report_variations(
            context,
            tmp_path,
            per_template=11,
        )
    with pytest.raises(ValueError, match="max_pages must be at least 1"):
        render_research_report_variations(
            context,
            tmp_path,
            max_pages=0,
        )
    with pytest.raises(
        ValueError,
        match="At least one research report template",
    ):
        render_research_report_variations(
            context,
            tmp_path,
            template_slugs=set(),
        )


def test_source_classification_validation_and_missing_field_compatibility(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    payload["result"]["source_classification"]["document_type"] = " "
    with pytest.raises(ValidationError, match="document_type must not be blank"):
        parse_generation_payload(payload)

    payload = _research_payload()
    payload["result"]["source_classification"]["document_type"] = (
        "research_report "
    )
    with pytest.raises(ValidationError):
        parse_generation_payload(payload)

    payload = _research_payload()
    payload["result"]["source_classification"]["document_type"] = "unknown_type"
    with pytest.raises(ValidationError):
        parse_generation_payload(payload)

    payload = _research_payload()
    payload["result"].pop("source_classification")
    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260729,
        template_slugs={"01_classic_municipal"},
    )

    assert manifest[0]["renderer_family"] == "official_document"
    assert manifest[0]["input"]["document_type"] is None


def test_research_document_type_routes_to_three_templates_and_preserves_text(
    tmp_path: Path,
) -> None:
    manifest = render_generation_payload(
        _research_payload(),
        tmp_path,
        per_template=1,
        base_seed=20260729,
    )

    assert len(manifest) == 3
    assert {
        entry["template_slug"] for entry in manifest
    } == {
        "research_01_classic_flow",
        "research_02_modular_policy",
        "research_03_academic_flow",
    }
    assert all(entry["status"] == "ok" for entry in manifest)
    assert all(entry["renderer_family"] == "research_report" for entry in manifest)
    assert all(entry["input"]["document_type"] == "research_report" for entry in manifest)
    assert all(entry["identity"]["identity_source"] == "none" for entry in manifest)
    assert all(entry["actual_pages"] <= 10 for entry in manifest)

    expected_order = (
        "첫번째고유문단",
        "수행 방식",
        "구분",
        "두번째고유문단",
        "첫번째고유항목",
        "단계",
        "고유붙임자료",
    )
    for entry in manifest:
        pdf_path = Path(str(entry["pdf"]))
        rendered_text = "".join(_pdf_text(pdf_path).split())
        positions = [
            rendered_text.index("".join(token.split()))
            for token in expected_order
        ]
        assert positions == sorted(positions)
        assert "후속검토결과는지정된절차에따라반영합니다." in rendered_text
        assert "2026-08-15" in rendered_text
        assert "김가온" in rendered_text
        assert "2026-07-29" in rendered_text
        assert all(
            "".join(atom.split()) in rendered_text
            for atom in source_text_atoms(
                parse_generation_payload(_research_payload())
                .result.generated_document
            )
        )
        with fitz.open(pdf_path) as pdf:
            assert any(page.rect.width > page.rect.height for page in pdf)

        html = Path(str(entry["html"])).read_text(encoding="utf-8")
        for invented_preview_text in (
            "공공정책연구원",
            "연구 개요",
            "평가 목적",
            "연구 책임자",
        ):
            assert invented_preview_text not in html


def test_research_template_escapes_model_provided_html(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    document = payload["result"]["generated_document"]
    document["blocks"][0]["text"] = (
        '태그 <img src="file:///private/etc/passwd"> & 본문'
    )
    document.pop("body_text")

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260729,
        template_slugs={"research_01_classic_flow"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")

    assert '&lt;img src=&#34;file:///private/etc/passwd&#34;&gt;' in html
    assert '<img src="file:///private/etc/passwd">' not in html
    assert "&amp; 본문" in html


def test_research_renderer_preserves_agency_and_handles_title_classes(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    document = payload["result"]["generated_document"]
    document["agency_name"] = "입력기관명보존원"
    document["title"] = "가" * 40
    document.pop("document_metadata")
    context = build_research_report_context(
        parse_generation_payload(payload),
        seed=100,
    )

    assert context["title_class"] == "title-long"
    assert context["signers"] == []
    assert context["administrative_events"] == []

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260729,
        template_slugs={"research_01_classic_flow"},
    )
    html = Path(str(manifest[0]["html"])).read_text(encoding="utf-8")
    assert "입력기관명보존원" in html
    assert 'class="title-long"' in html
    assert "research-approval" not in html
    assert "administrative-events" not in html
    assert manifest[0]["identity"] == {
        "identity_source": "input",
        "agency_name": "입력기관명보존원",
    }

    payload["result"]["generated_document"]["title"] = "나" * 70
    extra_long = build_research_report_context(
        parse_generation_payload(payload),
        seed=100,
    )
    assert extra_long["title_class"] == "title-extra-long"


def test_research_renderer_rejects_missing_source_and_keeps_input_metadata(
    tmp_path: Path,
) -> None:
    envelope = parse_generation_payload(_research_payload())
    context = build_research_report_context(envelope, seed=100)

    with pytest.raises(RuntimeError, match="missing source text"):
        render_research_report_variations(
            context,
            tmp_path,
            per_template=1,
            base_seed=20260729,
            template_slugs={"research_01_classic_flow"},
            required_source_texts=("PDF에 존재하지 않는 원문",),
            input_metadata={"request_id": "trace-rejected-input"},
        )

    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest[0]["status"] == "rejected"
    assert manifest[0]["source_text_present"] is False
    assert manifest[0]["input"]["request_id"] == "trace-rejected-input"


def test_batch_cli_function_routes_research_report_input(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "research-input.json"
    input_path.write_text(
        json.dumps(_research_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    batch = render_input_file(
        input_path,
        tmp_path / "output",
        per_template=1,
        base_seed=20260729,
        template_slugs={"research_03_academic_flow"},
    )

    assert batch[0]["status"] == "ok"
    assert batch[0]["render_count"] == 1
    manifest = json.loads(
        (
            Path(str(batch[0]["output_dir"])) / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest[0]["template_slug"] == "research_03_academic_flow"
    assert manifest[0]["input"]["document_type"] == "research_report"


def test_research_renderer_rejects_page_overflow_without_truncation(
    tmp_path: Path,
) -> None:
    envelope = parse_generation_payload(_research_payload())
    context = build_research_report_context(envelope, seed=100)

    with pytest.raises(RuntimeError, match="maximum 1 pages"):
        render_research_report_variations(
            context,
            tmp_path,
            per_template=1,
            base_seed=100,
            template_slugs={"research_01_classic_flow"},
            required_source_texts=source_text_atoms(
                envelope.result.generated_document
            ),
            max_pages=1,
        )

    manifest = (
        tmp_path / "manifest.json"
    ).read_text(encoding="utf-8")
    assert '"status": "rejected"' in manifest
    assert '"max_pages": 1' in manifest


def test_research_renderer_rejects_oversized_input_before_pdf_render(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    document = payload["result"]["generated_document"]
    document["blocks"][0]["text"] = "과대입력" * 10_001
    document.pop("body_text")

    with pytest.raises(ValueError, match="exceeds render budget"):
        render_generation_payload(
            payload,
            tmp_path,
            per_template=1,
            template_slugs={"research_01_classic_flow"},
        )

    assert not list(tmp_path.rglob("*.pdf"))


def test_research_renderer_rejects_excessive_title_before_pdf_render(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    payload["result"]["generated_document"]["title"] = "가" * 301

    with pytest.raises(ValueError, match="title exceeds 300 characters"):
        render_generation_payload(
            payload,
            tmp_path,
            per_template=1,
            template_slugs={"research_01_classic_flow"},
        )

    assert not list(tmp_path.rglob("*.pdf"))


def test_batch_cli_bounds_long_request_id(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    payload["receipt"]["request_id"] = "request-" + ("x" * 400)
    input_path = tmp_path / "long-request-id.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    batch = render_input_file(
        input_path,
        tmp_path / "output",
        per_template=1,
        template_slugs={"research_01_classic_flow"},
    )

    assert batch[0]["status"] == "ok"
    assert len(batch[0]["document_id"]) <= 96


def test_long_paragraph_flows_across_pages_without_false_missing_text(
    tmp_path: Path,
) -> None:
    payload = _research_payload()
    document = payload["result"]["generated_document"]
    document["blocks"][0]["text"] = (
        "장문입력검증을위한연속문장입니다. "
        "문단은페이지경계에서자연스럽게이어져야합니다. "
    ) * 240
    document.pop("body_text")

    manifest = render_generation_payload(
        payload,
        tmp_path,
        per_template=1,
        base_seed=20260729,
        template_slugs={"research_01_classic_flow"},
    )

    assert manifest[0]["status"] == "ok"
    assert manifest[0]["source_text_present"] is True
    assert 1 < manifest[0]["actual_pages"] <= 10
