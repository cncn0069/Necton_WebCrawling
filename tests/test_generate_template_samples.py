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


def test_one_sample_per_template_selects_all_56_templates_once():
    """56 = 53 + press_release 3건(5호/2호/3호, 2026-07-21 신규)."""
    selected = samples._selected_samples(1)

    assert len(selected) == 56
    assert len({sample.template_id for sample in selected}) == 56


def test_remaining_44_templates_have_real_source_references():
    dedicated_ids = {sample.template_id for sample in samples.SAMPLES}
    remaining = [sample for sample in samples._selected_samples(1) if sample.template_id not in dedicated_ids]

    assert len(remaining) == 44
    assert all(sample.provenance_level == "structural_reference" for sample in remaining)
    assert all(sample.sources and sample.sources[0].path.startswith("data/") for sample in remaining)


def test_relative_output_directory_is_supported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(samples, "_verify_samples", lambda **kwargs: None)
    monkeypatch.setattr(samples, "_selected_samples", lambda count: ())

    manifest = samples.generate_samples(Path("relative-output"), samples_per_template=1)

    assert manifest == []
    assert (tmp_path / "relative-output" / "template_samples_manifest.json").is_file()
