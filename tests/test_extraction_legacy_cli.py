from __future__ import annotations

from pathlib import Path

import annotate_documents  # noqa: E402
import extract_documents  # noqa: E402
import extract_hwp_text  # noqa: E402
import extract_pdf_text  # noqa: E402


def test_legacy_pdf_cli_translates_to_unified_v2(monkeypatch):
    captured: list[str] = []

    def fake_main(argv):
        captured.extend(argv)
        return 0

    monkeypatch.setattr(extract_documents, "main", fake_main)

    result = extract_pdf_text.main(
        ["--pdf", "data/moe/report/sample.pdf", "--force"]
    )

    assert result == 0
    assert captured == [
        "--document",
        "data/moe/report/sample.pdf",
        "--force",
        "--format",
        "pdf",
    ]


def test_legacy_hwp_cli_routes_hwp_and_hwpx_only(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    source_root = data_root / "moe" / "report"
    source_root.mkdir(parents=True)
    for name in ("a.hwp", "b.hwpx", "c.pdf"):
        (source_root / name).write_bytes(b"placeholder")

    captured: list[Path] = []

    def fake_run(documents, **_kwargs):
        captured.extend(documents)
        return {
            "status": "complete",
            "counts": {
                "total": len(captured),
                "processed": len(captured),
                "skipped": 0,
                "succeeded": len(captured),
                "failed": 0,
            },
        }

    monkeypatch.setattr(extract_hwp_text, "_DATA_ROOT", data_root)
    monkeypatch.setattr(extract_hwp_text, "_EXTRACTED_ROOT", data_root / "extracted")
    monkeypatch.setattr(extract_hwp_text, "run_extraction", fake_run)

    assert extract_hwp_text.main(["--source", "all"]) == 0
    assert [path.name for path in captured] == ["a.hwp", "b.hwpx"]


def test_legacy_annotation_cli_is_write_free(capsys):
    assert annotate_documents.main(["--source", "all", "--force"]) == 0
    assert "메모리에서 주석" in capsys.readouterr().out
