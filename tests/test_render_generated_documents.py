from __future__ import annotations

import json
from pathlib import Path

import scripts.render_generated_documents as render_cli
from rd2.generators.output_naming import (
    generation_output_filename,
    rename_rendered_files,
    requested_output_filename,
)
from scripts.render_generated_documents import (
    _prepare_renderer_payload,
    _renderer_payload,
)


def _fake_synthetic_scan(
    _source_pdf: Path,
    _output_pdf: Path,
    *,
    seed: int,
) -> dict[str, object]:
    return {
        "applied": True,
        "seed": seed,
        "image_only": True,
        "page_count": 1,
        "page_parameters": [],
    }


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


def test_renderer_preparation_infers_missing_document_type_from_provenance():
    payload = {
        "provenance": {"document_form": "meeting_minutes"},
        "result": {
            "generated_document": {
                "title": "정기 운영위원회 회의록",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "p1",
                        "text": "회의 결과를 기록한다.",
                    }
                ],
            }
        },
    }

    prepared, resolution = _prepare_renderer_payload(payload)

    assert resolution.source == "inferred"
    assert resolution.document_type == "meeting_minutes"
    assert prepared["result"]["source_classification"] == {
        "document_type": "meeting_minutes"
    }
    assert "source_classification" not in payload["result"]


def test_renderer_projection_infers_form_from_pipeline_source_classification():
    payload = {
        "source_assessment": {
            "source_classification": {
                "document_form": "meeting_minutes",
                "classification": "S",
            }
        },
        "generation_plan": {
            "generation_route": "anchored",
            "final_target": {"classification": "S"},
        },
        "generation_artifact": {
            "contract_version": "2.3.0",
            "generated_document": {
                "contract_version": "2.3.0",
                "title": "정기 운영위원회 회의록",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "p1",
                        "text": "회의 결과를 기록한다.",
                    }
                ],
            },
            "provenance": {"source_document_id": "source-1"},
        },
    }

    prepared, resolution = _prepare_renderer_payload(payload)

    assert resolution.document_form == "meeting_minutes"
    assert resolution.document_type == "meeting_minutes"
    assert prepared["result"]["source_classification"] == {
        "document_form": "meeting_minutes",
        "classification": "S",
        "document_type": "meeting_minutes",
    }


def test_directory_batch_routes_inferred_type_and_records_resolution(
    tmp_path: Path,
    monkeypatch,
):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    payload = {
        "output_filename": "운영위원회 회의록.pdf",
        "provenance": {"document_form": "meeting_minutes"},
        "result": {
            "contract_version": "2.3.0",
            "generation_route": "anchored",
            "generated_document": {
                "contract_version": "2.3.0",
                "title": "운영위원회 회의록",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "block_id": "p1",
                        "text": "회의 결과를 기록한다.",
                    }
                ],
            },
        },
    }
    (input_dir / "payload.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    def fake_render(
        prepared_payload: dict,
        document_output_dir: Path,
        **kwargs: object,
    ) -> list[dict[str, object]]:
        assert prepared_payload["result"]["source_classification"] == {
            "document_type": "meeting_minutes"
        }
        template_slug = next(iter(kwargs["template_slugs"]))
        assert str(template_slug).startswith("meeting_")
        template_dir = document_output_dir / str(template_slug)
        template_dir.mkdir(parents=True)
        pdf_path = template_dir / "generated.pdf"
        html_path = template_dir / "generated.html"
        pdf_path.write_bytes(b"%PDF")
        html_path.write_text("<html></html>", encoding="utf-8")
        return [
            {
                "status": "ok",
                "template_slug": template_slug,
                "variation_slug": "01_test",
                "pdf": str(pdf_path),
                "html": str(html_path),
            }
        ]

    monkeypatch.setattr(render_cli, "render_generation_payload", fake_render)
    monkeypatch.setattr(
        render_cli,
        "verify_rendered_sensitive_evidence",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        render_cli,
        "render_synthetic_scan_pdf",
        _fake_synthetic_scan,
    )

    manifest = render_cli.render_input_directory(
        input_dir,
        output_dir,
        selection_seed=20260804,
    )

    assert manifest["success_count"] == 1
    assert manifest["rejected_count"] == 0
    document = manifest["documents"][0]
    assert document["document_type"] == "meeting_minutes"
    assert document["selection"]["renderer_family"] == "meeting_minutes"
    assert document["document_type_resolution"] == {
        "document_type": "meeting_minutes",
        "source": "inferred",
        "document_form": "meeting_minutes",
        "reason": "document_form:meeting_minutes",
    }


def test_single_file_manifest_records_inference_on_success_and_rejection(
    tmp_path: Path,
    monkeypatch,
):
    def payload(title: str, filename: str) -> dict:
        return {
            "output_filename": filename,
            "provenance": {"document_form": "meeting_minutes"},
            "result": {
                "contract_version": "2.3.0",
                "generation_route": "anchored",
                "generated_document": {
                    "contract_version": "2.3.0",
                    "title": title,
                    "blocks": [
                        {
                            "kind": "paragraph",
                            "block_id": "p1",
                            "text": "회의 결과를 기록한다.",
                        }
                    ],
                },
            },
        }

    input_path = tmp_path / "payloads.json"
    input_path.write_text(
        json.dumps(
            [
                payload("성공 회의록", "성공.pdf"),
                payload("실패 회의록", "실패.pdf"),
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_render(
        prepared_payload: dict,
        document_output_dir: Path,
        **_kwargs: object,
    ) -> list[dict[str, object]]:
        assert prepared_payload["result"]["source_classification"] == {
            "document_type": "meeting_minutes"
        }
        if prepared_payload["result"]["generated_document"]["title"].startswith(
            "실패"
        ):
            raise ValueError("render rejected")
        template_dir = document_output_dir / "meeting_01_registry"
        template_dir.mkdir(parents=True)
        pdf_path = template_dir / "generated.pdf"
        html_path = template_dir / "generated.html"
        pdf_path.write_bytes(b"%PDF")
        html_path.write_text("<html></html>", encoding="utf-8")
        return [
            {
                "status": "ok",
                "template_slug": "meeting_01_registry",
                "variation_slug": "01_test",
                "pdf": str(pdf_path),
                "html": str(html_path),
            }
        ]

    monkeypatch.setattr(render_cli, "render_generation_payload", fake_render)
    monkeypatch.setattr(
        render_cli,
        "verify_rendered_sensitive_evidence",
        lambda *_args, **_kwargs: [],
    )

    manifest = render_cli.render_input_file(input_path, tmp_path / "output")

    assert [entry["status"] for entry in manifest] == ["ok", "rejected"]
    for entry in manifest:
        assert entry["document_type"] == "meeting_minutes"
        assert entry["document_type_resolution"] == {
            "document_type": "meeting_minutes",
            "source": "inferred",
            "document_form": "meeting_minutes",
            "reason": "document_form:meeting_minutes",
        }


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
