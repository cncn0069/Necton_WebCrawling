from __future__ import annotations

import sys
from pathlib import Path

import pytest

import run_pipeline  # noqa: E402


def test_from_scratch_runs_unified_extractor_then_candidates(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(run_pipeline, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--clause", "5", "--from-scratch", "--source", "all", "--dry-run"],
    )

    run_pipeline.main()

    assert [command[1] for command in commands] == [
        "scripts/extract/extract_documents.py",
        "scripts/augment/find_candidates.py",
        "scripts/augment/run_llm_augment.py",
    ]
    assert commands[0][2:] == ["--source", "all"]
    assert commands[1][2:] == ["--clause", "5"]
    assert commands[2][2:] == ["--clause", "5", "--limit", "1", "--dry-run"]


def test_subset_source_is_rejected_before_any_pipeline_stage(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(run_pipeline, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--clause", "5", "--from-scratch", "--source", "moe"],
    )

    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main()

    assert exc_info.value.code == 2
    assert commands == []


def test_force_is_forwarded_only_to_stages_that_support_it(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(run_pipeline, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--clause", "5", "--from-scratch", "--force", "--dry-run"],
    )

    run_pipeline.main()

    assert commands[0][-1] == "--force"
    assert "--force" not in commands[1]
    assert commands[2][-1] == "--force"


def test_partial_candidate_override_is_forwarded_to_candidate_and_llm_stages(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(run_pipeline, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_pipeline.py", "--clause", "5", "--allow-partial-candidates", "--dry-run"],
    )

    run_pipeline.main()

    assert commands[0][1:] == [
        "scripts/augment/find_candidates.py", "--clause", "5", "--allow-partial",
    ]
    assert "--allow-partial-candidates" in commands[1]
