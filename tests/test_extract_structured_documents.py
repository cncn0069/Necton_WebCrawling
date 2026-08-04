from __future__ import annotations

import json
from pathlib import Path

import extract_structured_documents as script  # noqa: E402
from rd2.extractors.hwp import ExtractedHwpDocument  # noqa: E402
from rd2.extractors.pdf import ExtractedDocument, ExtractedPage, ExtractedTable  # noqa: E402


def _set_roots(monkeypatch, tmp_path: Path) -> Path:
    data_root = tmp_path / "data"
    structured_root = data_root / "structured"
    monkeypatch.setattr(script, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "_DATA_ROOT", data_root)
    monkeypatch.setattr(script, "_STRUCTURED_ROOT", structured_root)
    monkeypatch.setattr(script, "_LOG_PATH", structured_root / "_extraction_log.jsonl")
    return data_root


def test_pdf_sidecar_contains_text_tables_and_ocr_state(monkeypatch, tmp_path: Path):
    data_root = _set_roots(monkeypatch, tmp_path)
    pdf_path = data_root / "moe" / "report" / "sample.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"placeholder")
    result = ExtractedDocument(
        source_path=pdf_path,
        pages=[
            ExtractedPage(
                page_number=1,
                text="본문",
                tables=[ExtractedTable(rows=[["항목", "값"]], page_number=1)],
                needs_ocr=False,
            ),
            ExtractedPage(page_number=2, text="", needs_ocr=True),
        ],
    )
    monkeypatch.setattr(script, "extract_pdf", lambda path: result)

    status = script._process_one(pdf_path, force=False)

    assert status == "needs_ocr"
    payload = json.loads(script._output_path(pdf_path).read_text(encoding="utf-8"))
    assert payload["source_path"] == "data/moe/report/sample.pdf"
    assert payload["document_format"] == "pdf"
    assert payload["text"] == "본문\n\n"
    assert payload["tables"] == [{"page_number": 1, "rows": [["항목", "값"]]}]
    assert payload["needs_ocr"] is True
    assert payload["needs_quarantine"] is False


def test_invalid_hwp_is_written_as_quarantine(monkeypatch, tmp_path: Path):
    data_root = _set_roots(monkeypatch, tmp_path)
    hwp_path = data_root / "seoul" / "broken.hwp"
    hwp_path.parent.mkdir(parents=True)
    hwp_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        script,
        "extract_hwp",
        lambda path: ExtractedHwpDocument(source_path=path, is_valid=False),
    )

    status = script._process_one(hwp_path, force=False)

    assert status == "quarantine"
    payload = json.loads(script._output_path(hwp_path).read_text(encoding="utf-8"))
    assert payload["needs_quarantine"] is True
    assert payload["is_valid"] is False
    assert payload["pages"] == []


def test_extractor_exception_is_recorded_without_raising(monkeypatch, tmp_path: Path):
    data_root = _set_roots(monkeypatch, tmp_path)
    pdf_path = data_root / "moe" / "corrupt.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"placeholder")

    def fail(_path):
        raise ValueError("corrupt file")

    monkeypatch.setattr(script, "extract_pdf", fail)

    assert script._process_one(pdf_path, force=False) == "error"
    payload = json.loads(script._output_path(pdf_path).read_text(encoding="utf-8"))
    assert payload["needs_quarantine"] is True
    assert payload["error"] == "ValueError: corrupt file"


def test_output_paths_preserve_input_extensions(monkeypatch, tmp_path: Path):
    data_root = _set_roots(monkeypatch, tmp_path)
    pdf_path = data_root / "moe" / "same.pdf"
    hwp_path = data_root / "moe" / "same.hwp"

    assert script._output_path(pdf_path).name == "same.pdf.json"
    assert script._output_path(hwp_path).name == "same.hwp.json"


def test_discovery_ignores_pipeline_output_directories(monkeypatch, tmp_path: Path):
    data_root = _set_roots(monkeypatch, tmp_path)
    for name in ("moe", "seoul", "structured", "extracted", ".cache"):
        (data_root / name).mkdir(parents=True)

    assert script._discover_sources() == ["moe", "seoul"]
