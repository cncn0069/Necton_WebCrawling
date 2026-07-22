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


def test_one_sample_per_template_selects_all_61_templates_once():
    """61 = 56 + 5호 bid_contract/decision_review 그룹 확장분(T5-10~T5-15,
    2026-07-21 신규 문서유형-조건부 행정상태 축 추가로 5호가 10개→16개)."""
    selected = samples._selected_samples(1)

    assert len(selected) == 61
    assert len({sample.template_id for sample in selected}) == 61


def test_remaining_27_templates_have_real_source_references():
    """27 = 61 - 34(SAMPLES에 개별 선언된 템플릿, 2026-07-22 5~8호 재작업으로
    12개에서 34개로 확장). 나머지는 문서유형 기본 참조(_SOURCE_BY_DOC_TYPE)를 쓴다."""
    dedicated_ids = {sample.template_id for sample in samples.SAMPLES}
    remaining = [sample for sample in samples._selected_samples(1) if sample.template_id not in dedicated_ids]

    assert len(remaining) == 27
    assert all(sample.provenance_level == "structural_reference" for sample in remaining)
    assert all(sample.sources and sample.sources[0].path.startswith("data/") for sample in remaining)


def test_relative_output_directory_is_supported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(samples, "_verify_samples", lambda **kwargs: None)
    monkeypatch.setattr(samples, "_selected_samples", lambda count: ())

    manifest = samples.generate_samples(Path("relative-output"), samples_per_template=1)

    assert manifest == []
    assert (tmp_path / "relative-output" / "template_samples_manifest.json").is_file()
