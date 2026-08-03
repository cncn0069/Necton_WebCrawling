from __future__ import annotations

from pathlib import Path

from rd2.generators.output_naming import (
    generation_output_filename,
    rename_rendered_files,
    requested_output_filename,
)
from scripts.render_generated_documents import _renderer_payload


def test_renderer_projection_preserves_military_secret_grade():
    payload = {
        "generation_plan": {
            "generation_route": "fully_synthetic",
            "final_target": {
                "classification": "C",
                "clause_no": "2",
                "subclause_key": "security_defense",
                "generation_mode": "counterfactual",
                "military_secret_grade": "1급",
            },
        },
        "generation_artifact": {
            "contract_version": "2.3.0",
            "generated_document": {
                "contract_version": "2.3.0",
                "title": "군사기밀 문서",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "g1",
                        "text": "합성 본문",
                    }
                ],
            },
        },
    }

    projected = _renderer_payload(payload)

    assert projected["result"]["generation_target"][
        "military_secret_grade"
    ] == "1급"


def test_renderer_projection_carries_ordering_agency_into_document():
    payload = {
        "ordering_agency": "행정안전부",
        "generation_plan": {
            "generation_route": "fully_synthetic",
            "final_target": {"classification": "C"},
        },
        "generation_artifact": {
            "contract_version": "2.3.0",
            "generated_document": {
                "contract_version": "2.3.0",
                "title": "대외비 문서",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "g1",
                        "text": "합성 본문",
                    }
                ],
            },
        },
    }

    projected = _renderer_payload(payload)

    assert projected["result"]["generated_document"]["agency_name"] == (
        "행정안전부"
    )
    assert "agency_name" not in payload["generation_artifact"][
        "generated_document"
    ]


def test_renderer_carries_agency_into_existing_result_without_overwriting_input():
    payload = {
        "ordering_agency": "행정안전부",
        "result": {
            "generated_document": {
                "agency_name": "국방부",
            }
        },
    }

    assert _renderer_payload(payload) is payload
    assert payload["result"]["generated_document"]["agency_name"] == "국방부"

    missing = {
        "ordering_agency": "행정안전부",
        "result": {"generated_document": {}},
    }
    projected = _renderer_payload(missing)
    assert projected["result"]["generated_document"]["agency_name"] == (
        "행정안전부"
    )
    assert missing["result"]["generated_document"] == {}


def test_requested_filename_preserves_korean_source_stem():
    payload = {"output_filename": "36534390_결재문서본문.pdf"}

    assert requested_output_filename(payload, 1) == "36534390_결재문서본문.pdf"


def test_requested_filename_sanitizes_generated_title():
    payload = {"output_filename": '보고서: 검토/승인? "최종".pdf'}

    assert requested_output_filename(payload, 1) == "보고서_ 검토_승인_ _최종_.pdf"


def test_source_aligned_uses_source_name_and_new_document_uses_title():
    assert generation_output_filename(
        generation_route="source_aligned",
        source_filename="36534390_결재문서본문.hwpx",
        generated_title="바뀐 제목",
    ) == "36534390_결재문서본문.pdf"
    assert generation_output_filename(
        generation_route="anchored",
        source_filename="36534390_결재문서본문.hwpx",
        generated_title="바뀐 제목",
    ) == "36534390_결재문서본문.pdf"
    assert generation_output_filename(
        generation_route="fully_synthetic",
        source_filename="36534390_결재문서본문.hwpx",
        generated_title="새로 생성한 검토 보고서",
    ) == "새로 생성한 검토 보고서.pdf"


def test_rename_rendered_files_uses_exact_name_per_template(tmp_path: Path):
    rendered = []
    for template in ("01_classic", "02_modern"):
        parent = tmp_path / template
        parent.mkdir()
        pdf = parent / "01_stacked.pdf"
        html = parent / "01_stacked.html"
        pdf.write_bytes(b"%PDF")
        html.write_text("<html></html>", encoding="utf-8")
        rendered.append(
            {
                "template_slug": template,
                "variation_slug": "01_stacked",
                "pdf": str(pdf),
                "html": str(html),
            }
        )

    rename_rendered_files(rendered, "원문파일명.pdf")

    assert all(Path(str(entry["pdf"])).name == "원문파일명.pdf" for entry in rendered)
    assert all(Path(str(entry["html"])).name == "원문파일명.html" for entry in rendered)


def test_multiple_variations_get_suffix_to_avoid_overwrite(tmp_path: Path):
    parent = tmp_path / "01_classic"
    parent.mkdir()
    rendered = []
    for variation in ("01_stacked", "02_compact"):
        pdf = parent / f"{variation}.pdf"
        html = parent / f"{variation}.html"
        pdf.write_bytes(b"%PDF")
        html.write_text("<html></html>", encoding="utf-8")
        rendered.append(
            {
                "template_slug": "01_classic",
                "variation_slug": variation,
                "pdf": str(pdf),
                "html": str(html),
            }
        )

    rename_rendered_files(rendered, "생성 문서 제목.pdf")

    assert {Path(str(entry["pdf"])).name for entry in rendered} == {
        "생성 문서 제목__01_stacked.pdf",
        "생성 문서 제목__02_compact.pdf",
    }
