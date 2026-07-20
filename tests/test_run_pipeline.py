from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import run_pipeline  # noqa: E402


def test_from_scratch_runs_both_extractors_before_annotation(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(run_pipeline, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--clause", "5", "--from-scratch", "--source", "moe", "--dry-run"],
    )

    run_pipeline.main()

    assert [command[1] for command in commands[:3]] == [
        "scripts/extract_pdf_text.py",
        "scripts/extract_structured_documents.py",
        "scripts/annotate_documents.py",
    ]
    assert all(command[2:] == ["--source", "moe"] for command in commands[:3])


def test_force_is_forwarded_to_all_from_scratch_stages(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(run_pipeline, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--clause", "5", "--from-scratch", "--force", "--dry-run"],
    )

    run_pipeline.main()

    assert all(command[-1] == "--force" for command in commands[:3])
