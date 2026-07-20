import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import generate_template_samples as samples  # noqa: E402


def test_sample_declarations_validate_without_local_source_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, "_REPO_ROOT", tmp_path)

    samples._verify_samples()


def test_strict_source_validation_reports_missing_local_corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, "_REPO_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="원본 참조 파일 없음"):
        samples._verify_samples(require_source_files=True)
