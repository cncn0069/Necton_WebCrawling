from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pymupdf
import pytest

from rd2.extraction import hwp_text, storage
from rd2.extraction.hwp_text import extract_hwp_document
from rd2.extraction.pdf_text import _physical_line_payload, extract_pdf_document
from rd2.extraction.pipeline import (
    RUN_MANIFEST_NAME,
    iter_source_documents,
    process_document,
    run_extraction,
)
from rd2.extraction.storage import (
    SCHEMA_VERSION,
    build_extraction_id,
    compute_source_sha256,
    extraction_output_path,
    is_current_extraction,
    read_json_gz,
    write_json_gz_atomic,
)
from rd2.extractors.hwp import ExtractedHwpDocument

_SCRIPTS_DIR = str(Path(__file__).parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import extract_documents as extraction_cli  # noqa: E402


def _source_path(tmp_path: Path, name: str) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    path = data_root / "moe" / "report" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return data_root, path


def _make_two_line_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=200, height=160)
    page.insert_text((20, 40), "first physical line", fontsize=10)
    page.insert_text((20, 58), "second physical line", fontsize=10)
    document.save(path)
    document.close()


def _make_blank_pdf(path: Path) -> None:
    document = pymupdf.open()
    document.new_page(width=200, height=160)
    document.save(path)
    document.close()


def _make_encrypted_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=200, height=160)
    page.insert_text((20, 40), "encrypted text")
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    document.close()


def test_pdf_v2_schema_stores_physical_lines_and_rounded_geometry(tmp_path: Path):
    data_root, source_path = _source_path(tmp_path, "42_sample.pdf")
    _make_two_line_pdf(source_path)

    payload = extract_pdf_document(source_path, data_root=data_root)

    assert set(payload) == {
        "schema_version",
        "extraction_id",
        "source_sha256",
        "source_path",
        "source",
        "doc_type",
        "doc_id",
        "source_format",
        "extraction",
        "status",
        "error",
        "quality",
        "pages",
    }
    assert payload["schema_version"] == 2
    assert payload["source_path"] == "data/moe/report/42_sample.pdf"
    assert payload["source"] == "moe"
    assert payload["doc_type"] == "report"
    assert payload["doc_id"] == "42"
    assert payload["source_format"] == "pdf"
    assert payload["status"] == "ok"
    assert payload["error"] is None
    assert set(payload["quality"]) == {
        "has_text_layer",
        "needs_ocr",
        "needs_quarantine",
        "pages_needing_ocr",
        "avg_chars_per_page",
        "warnings",
    }

    page = payload["pages"][0]
    assert set(page) == {"page", "width_pt", "height_pt", "rotation", "lines"}
    assert page["page"] == 1
    assert page["width_pt"] == 200.0
    assert page["height_pt"] == 160.0
    assert [line["text"] for line in page["lines"]] == [
        "first physical line",
        "second physical line",
    ]
    assert [line["line_id"] for line in page["lines"]] == [0, 1]
    assert [line["order"] for line in page["lines"]] == [0, 1]
    for line in page["lines"]:
        assert set(line) == {
            "line_id",
            "block_id",
            "order",
            "text",
            "bbox_pt",
            "style_runs",
        }
        assert all(value == round(value, 1) for value in line["bbox_pt"])
        assert line["style_runs"][0]["start"] == 0
        assert line["style_runs"][-1]["end"] == len(line["text"])


def test_pdf_style_runs_coalesce_adjacent_identical_styles_and_decode_flags():
    raw_line = {
        "spans": [
            {
                "text": "AB",
                "bbox": [1.04, 2.06, 3.04, 4.06],
                "font": "Example",
                "size": 10.04,
                "flags": 16,
                "color": 0,
            },
            {
                "text": "CD",
                "bbox": [3.04, 2.06, 5.04, 4.06],
                "font": "Example",
                "size": 10.02,
                "flags": 16,
                "color": 0,
            },
            {
                "text": "E",
                "bbox": [5.04, 2.06, 6.04, 4.06],
                "font": "Example",
                "size": 10.16,
                "flags": 2,
                "color": 7,
            },
        ]
    }

    line = _physical_line_payload(raw_line, line_id=7, block_id=3, order=2)

    assert line is not None
    assert line["text"] == "ABCDE"
    assert line["bbox_pt"] == [1.0, 2.1, 6.0, 4.1]
    assert line["style_runs"] == [
        {
            "start": 0,
            "end": 4,
            "font": "Example",
            "size_pt": 10.0,
            "bold": True,
            "italic": False,
            "color": 0,
        },
        {
            "start": 4,
            "end": 5,
            "font": "Example",
            "size_pt": 10.2,
            "bold": False,
            "italic": True,
            "color": 7,
        },
    ]


def test_blank_pdf_is_a_successful_needs_ocr_snapshot(tmp_path: Path):
    data_root, source_path = _source_path(tmp_path, "blank.pdf")
    _make_blank_pdf(source_path)

    payload = extract_pdf_document(source_path, data_root=data_root)

    assert payload["status"] == "needs_ocr"
    assert payload["error"] is None
    assert payload["quality"]["has_text_layer"] is False
    assert payload["quality"]["needs_ocr"] is True
    assert payload["quality"]["needs_quarantine"] is False
    assert payload["quality"]["pages_needing_ocr"] == [1]
    assert payload["pages"][0]["lines"] == []


def test_encrypted_pdf_is_serialized_as_quarantined_error(tmp_path: Path):
    data_root, source_path = _source_path(tmp_path, "encrypted.pdf")
    _make_encrypted_pdf(source_path)

    payload = extract_pdf_document(source_path, data_root=data_root)

    assert payload["status"] == "error"
    assert "encrypted" in payload["error"]
    assert payload["quality"]["needs_quarantine"] is True
    assert payload["quality"]["warnings"] == ["encrypted"]
    assert payload["pages"] == []


def test_hwp_v2_uses_one_logical_page_with_null_geometry(tmp_path: Path, monkeypatch):
    data_root, source_path = _source_path(tmp_path, "77_sample.hwpx")
    source_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        hwp_text,
        "extract_hwp",
        lambda path: ExtractedHwpDocument(source_path=path, text="첫 문단\n\n둘째 문단"),
    )

    payload = extract_hwp_document(source_path, data_root=data_root)

    assert payload["schema_version"] == 2
    assert payload["source_format"] == "hwpx"
    assert payload["status"] == "ok"
    assert set(payload["quality"]) == {
        "has_text_layer",
        "needs_ocr",
        "needs_quarantine",
        "pages_needing_ocr",
        "avg_chars_per_page",
        "warnings",
    }
    page = payload["pages"][0]
    assert (page["width_pt"], page["height_pt"], page["rotation"]) == (None, None, None)
    assert [line["text"] for line in page["lines"]] == ["첫 문단", "둘째 문단"]
    assert [line["line_id"] for line in page["lines"]] == [0, 1]
    assert all(line["block_id"] is None for line in page["lines"])
    assert all(line["bbox_pt"] is None for line in page["lines"])
    assert all(line["style_runs"] == [] for line in page["lines"])


def test_encrypted_hwp_is_quarantined_without_fake_page(tmp_path: Path, monkeypatch):
    data_root, source_path = _source_path(tmp_path, "encrypted.hwp")
    source_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        hwp_text,
        "extract_hwp",
        lambda path: ExtractedHwpDocument(
            source_path=path,
            is_encrypted=True,
            error="encrypted",
        ),
    )

    payload = extract_hwp_document(source_path, data_root=data_root)

    assert payload["status"] == "quarantine"
    assert payload["error"] == "encrypted"
    assert payload["quality"]["needs_quarantine"] is True
    assert payload["pages"] == []


def test_output_path_preserves_source_extension_for_same_stem(tmp_path: Path):
    data_root = tmp_path / "data"
    extracted_root = data_root / "extracted"
    pdf = data_root / "moe" / "report" / "same.pdf"
    hwp = data_root / "moe" / "report" / "same.hwp"

    assert extraction_output_path(pdf, data_root, extracted_root).name == "same.pdf.json.gz"
    assert extraction_output_path(hwp, data_root, extracted_root).name == "same.hwp.json.gz"
    assert extraction_output_path(pdf, data_root, extracted_root) != extraction_output_path(
        hwp, data_root, extracted_root
    )


def test_gzip_json_roundtrip_is_compact_and_deterministic(tmp_path: Path):
    payload = {"schema_version": 2, "text": "한글", "nested": {"ok": True}}
    first = tmp_path / "first.json.gz"
    second = tmp_path / "second.json.gz"

    write_json_gz_atomic(first, payload)
    write_json_gz_atomic(second, payload)

    assert read_json_gz(first) == payload
    assert first.read_bytes() == second.read_bytes()


def test_atomic_write_failure_preserves_existing_target(tmp_path: Path, monkeypatch):
    target = tmp_path / "document.json.gz"
    original = {"version": "old"}
    write_json_gz_atomic(target, original)

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        write_json_gz_atomic(target, {"version": "new"})

    assert read_json_gz(target) == original
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_extraction_identity_and_current_cache_invalidate_on_source_or_config_change(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source bytes")
    source_digest = compute_source_sha256(source)
    assert source_digest == hashlib.sha256(b"source bytes").hexdigest()

    extraction = {
        "profile": "layout-lite-v1",
        "extractor": "test",
        "extractor_version": "1",
        "config": {"precision": 1},
    }
    extraction_id = build_extraction_id(source_digest, extraction)
    changed_id = build_extraction_id(source_digest, {**extraction, "config": {"precision": 2}})
    assert extraction_id != changed_id

    output = tmp_path / "source.pdf.json.gz"
    write_json_gz_atomic(
        output,
        {
            "schema_version": SCHEMA_VERSION,
            "source_sha256": source_digest,
            "extraction_id": extraction_id,
        },
    )
    assert is_current_extraction(output, source_digest)
    assert is_current_extraction(output, source_digest, expected_extraction_id=extraction_id)
    assert not is_current_extraction(output, source_digest, expected_extraction_id=changed_id)
    assert not is_current_extraction(output, "0" * 64)


def test_process_document_reextracts_when_source_content_changes(tmp_path: Path):
    data_root, source_path = _source_path(tmp_path, "broken.pdf")
    extracted_root = data_root / "extracted"
    source_path.write_bytes(b"not a pdf")

    first_action, output_path, first = process_document(
        source_path,
        data_root=data_root,
        extracted_root=extracted_root,
    )
    second_action, _, second = process_document(
        source_path,
        data_root=data_root,
        extracted_root=extracted_root,
    )
    source_path.write_bytes(b"changed, still not a pdf")
    third_action, _, third = process_document(
        source_path,
        data_root=data_root,
        extracted_root=extracted_root,
    )

    assert first_action == "processed"
    assert second_action == "skipped"
    assert third_action == "processed"
    assert first["source_sha256"] == second["source_sha256"]
    assert third["source_sha256"] != first["source_sha256"]
    assert read_json_gz(output_path)["source_sha256"] == third["source_sha256"]


def test_run_manifest_is_atomic_and_records_partial_failures(tmp_path: Path):
    data_root, source_path = _source_path(tmp_path, "invalid.pdf")
    extracted_root = data_root / "extracted"
    source_path.write_bytes(b"not a pdf")

    manifest = run_extraction(
        [source_path],
        data_root=data_root,
        extracted_root=extracted_root,
    )

    assert manifest["status"] == "partial"
    assert manifest["counts"] == {
        "total": 1,
        "processed": 1,
        "skipped": 0,
        "succeeded": 0,
        "failed": 1,
    }
    assert manifest["failures"][0]["source_path"] == "data/moe/report/invalid.pdf"
    assert manifest["artifacts"] == []
    assert read_json_gz(extracted_root / RUN_MANIFEST_NAME) == manifest


def test_run_manifest_records_deterministic_success_and_skip_artifacts(tmp_path: Path):
    data_root, first_source = _source_path(tmp_path, "a.pdf")
    _, second_source = _source_path(tmp_path, "b.pdf")
    extracted_root = data_root / "extracted"
    _make_two_line_pdf(first_source)
    _make_two_line_pdf(second_source)

    first_manifest = run_extraction(
        [second_source, first_source],
        data_root=data_root,
        extracted_root=extracted_root,
    )

    assert first_manifest["status"] == "complete"
    assert first_manifest["counts"]["succeeded"] == 2
    assert [entry["source_path"] for entry in first_manifest["artifacts"]] == [
        "data/moe/report/a.pdf",
        "data/moe/report/b.pdf",
    ]
    assert [entry["output_path"] for entry in first_manifest["artifacts"]] == [
        "moe/report/a.pdf.json.gz",
        "moe/report/b.pdf.json.gz",
    ]
    assert all(entry["status"] == "ok" for entry in first_manifest["artifacts"])
    assert all(entry["extraction_id"] for entry in first_manifest["artifacts"])

    second_manifest = run_extraction(
        [second_source, first_source],
        data_root=data_root,
        extracted_root=extracted_root,
    )

    assert second_manifest["counts"]["skipped"] == 2
    assert second_manifest["artifacts"] == first_manifest["artifacts"]


def test_cli_limit_slices_deterministically_ordered_documents(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data"
    extracted_root = data_root / "extracted"
    source_root = data_root / "moe" / "report"
    source_root.mkdir(parents=True)
    for name in ("c.pdf", "a.pdf", "b.pdf"):
        (source_root / name).write_bytes(b"placeholder")

    captured: list[Path] = []

    def fake_run(documents, **kwargs):
        captured.extend(documents)
        return {
            "status": "complete",
            "counts": {"total": 2, "processed": 2, "skipped": 0, "succeeded": 2, "failed": 0},
            "failures": [],
        }

    monkeypatch.setattr(extraction_cli, "_DATA_ROOT", data_root)
    monkeypatch.setattr(extraction_cli, "_EXTRACTED_ROOT", extracted_root)
    monkeypatch.setattr(extraction_cli, "run_extraction", fake_run)

    assert extraction_cli.main(["--source", "moe", "--format", "pdf", "--limit", "2"]) == 0
    assert [path.name for path in captured] == ["a.pdf", "b.pdf"]


def test_cli_refuses_empty_scope_before_replacing_manifest(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("empty scope must not publish a manifest")

    monkeypatch.setattr(extraction_cli, "_DATA_ROOT", data_root)
    monkeypatch.setattr(extraction_cli, "_EXTRACTED_ROOT", data_root / "extracted")
    monkeypatch.setattr(extraction_cli, "run_extraction", fake_run)

    with pytest.raises(SystemExit):
        extraction_cli.main(["--source", "missing"])
    assert called is False


def test_source_scope_cannot_escape_data_root(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()

    with pytest.raises(ValueError, match="escapes data root"):
        list(iter_source_documents(data_root, source="../outside"))


def test_cli_partial_exit_requires_allow_partial(tmp_path: Path, monkeypatch):
    data_root, source_path = _source_path(tmp_path, "invalid.pdf")
    source_path.write_bytes(b"placeholder")
    extracted_root = data_root / "extracted"

    def fake_run(documents, **kwargs):
        return {
            "status": "partial",
            "counts": {"total": 1, "processed": 1, "skipped": 0, "succeeded": 0, "failed": 1},
            "failures": [{"source_path": str(source_path), "error": "broken"}],
        }

    monkeypatch.setattr(extraction_cli, "_DATA_ROOT", data_root)
    monkeypatch.setattr(extraction_cli, "_EXTRACTED_ROOT", extracted_root)
    monkeypatch.setattr(extraction_cli, "run_extraction", fake_run)

    assert extraction_cli.main(["--document", str(source_path)]) == 1
    assert extraction_cli.main(["--document", str(source_path), "--allow-partial"]) == 0
