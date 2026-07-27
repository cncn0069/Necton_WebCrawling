"""rd2.audit.review_sampler 회귀 테스트 — 설계 문서 §8 + §14.2."""

import math

from rd2.audit.review_sampler import (
    RowSummary,
    pdf_status,
    select_anomaly_samples,
    select_representative_samples,
    select_review_samples,
)


def _summary(row_id, cell_key, *, body_length=100, seed_candidate_id="", normalized_hash="", fingerprint=""):
    return RowSummary(
        row_id=row_id,
        coverage_cell_key=cell_key,
        seed_candidate_id=seed_candidate_id,
        seed_extraction_id="",
        body_length=body_length,
        normalized_hash=normalized_hash or f"hash-{row_id}",
        structure_fingerprint=fingerprint or "fp-default",
    )


def _coverage_row(cell_key, *, planned_target, actual_ok, shortage=0, status="filled"):
    return {
        "coverage_cell_key": cell_key,
        "planned_target": planned_target,
        "actual_ok": actual_ok,
        "shortage": shortage,
        "coverage_status": status,
    }


class TestSelectAnomalySamples:
    def test_raw_exact_duplicate_takes_priority_over_normalized(self):
        """설계 문서 §8: primary reason 우선순위는 raw exact -> normalized 고정
        순서다 — normalized 그룹이 더 커도 raw exact 그룹이 먼저 선택돼야 한다."""
        row_summaries = {
            "row-raw": _summary("row-raw", "cell-1"),
            "row-norm": _summary("row-norm", "cell-2"),
        }
        duplicate_groups = [
            {"metric": "raw_exact", "group_id": "g1", "representative_row_id": "row-raw", "group_size": 2},
            {"metric": "normalized_exact", "group_id": "g2", "representative_row_id": "row-norm", "group_size": 5},
        ]
        selected = select_anomaly_samples(
            quota=2, row_summaries=row_summaries, duplicate_groups=duplicate_groups,
            coverage_actual_rows=[], format_result={},
        )
        assert selected[0].reason_code == "raw_exact_duplicate"
        assert selected[0].row_id == "row-raw"

    def test_not_run_reasons_never_selected(self):
        row_summaries = {"row-a": _summary("row-a", "cell-1")}
        selected = select_anomaly_samples(
            quota=10, row_summaries=row_summaries, duplicate_groups=[],
            coverage_actual_rows=[], format_result={},
        )
        assert all(s.reason_code not in {"semantic_neighbor", "agency_proxy_mismatch"} for s in selected)

    def test_coverage_shortage_candidate_picked(self):
        row_summaries = {"row-a": _summary("row-a", "cell-short")}
        coverage_actual_rows = [_coverage_row("cell-short", planned_target=10, actual_ok=2, shortage=8, status="shortage")]
        selected = select_anomaly_samples(
            quota=5, row_summaries=row_summaries, duplicate_groups=[],
            coverage_actual_rows=coverage_actual_rows, format_result={},
        )
        assert any(s.reason_code == "coverage_shortage" and s.row_id == "row-a" for s in selected)

    def test_shared_source_candidate_picked(self):
        row_summaries = {
            "row-a": _summary("row-a", "cell-1", seed_candidate_id="cand-x"),
            "row-b": _summary("row-b", "cell-1", seed_candidate_id="cand-x"),
            "row-c": _summary("row-c", "cell-1", seed_candidate_id="cand-y"),
        }
        selected = select_anomaly_samples(
            quota=5, row_summaries=row_summaries, duplicate_groups=[],
            coverage_actual_rows=[], format_result={},
        )
        shared = [s for s in selected if s.reason_code == "shared_source"]
        assert len(shared) == 1
        assert shared[0].row_id == "row-a"  # 두 후보 중 row_id 오름차순 first

    def test_quota_limits_selection_count(self):
        row_summaries = {f"row-{i}": _summary(f"row-{i}", "cell-1", seed_candidate_id=f"cand-{i}") for i in range(5)}
        duplicate_groups = [
            {"metric": "raw_exact", "group_id": f"g{i}", "representative_row_id": f"row-{i}", "group_size": 2}
            for i in range(5)
        ]
        selected = select_anomaly_samples(
            quota=2, row_summaries=row_summaries, duplicate_groups=duplicate_groups,
            coverage_actual_rows=[], format_result={},
        )
        assert len(selected) == 2

    def test_row_not_selected_twice_across_reasons(self):
        row_summaries = {"row-a": _summary("row-a", "cell-1", seed_candidate_id="cand-x")}
        duplicate_groups = [
            {"metric": "raw_exact", "group_id": "g1", "representative_row_id": "row-a", "group_size": 2},
        ]
        selected = select_anomaly_samples(
            quota=10, row_summaries=row_summaries, duplicate_groups=duplicate_groups,
            coverage_actual_rows=[], format_result={},
        )
        assert len([s for s in selected if s.row_id == "row-a"]) == 1


class TestSelectRepresentativeSamples:
    def test_cell_cap_limits_two_per_cell(self):
        """설계 문서 §8: cell당 최대 2건 — quota가 cap*cell수와 정확히 맞아떨어지면
        cap을 늘릴 필요가 없으니 각 cell은 정확히 2건씩만 내야 한다."""
        row_summaries = {}
        for i in range(5):
            row_summaries[f"a-{i}"] = _summary(f"a-{i}", "cell-A", body_length=100 + i)
        for i in range(5):
            row_summaries[f"b-{i}"] = _summary(f"b-{i}", "cell-B", body_length=100 + i)
        coverage_actual_rows = [
            _coverage_row("cell-A", planned_target=10, actual_ok=5),
            _coverage_row("cell-B", planned_target=10, actual_ok=5),
        ]
        selected = select_representative_samples(
            quota=4, row_summaries=row_summaries, coverage_actual_rows=coverage_actual_rows,
            chosen_row_ids=set(),
        )
        assert len(selected) == 4
        counts: dict[str, int] = {}
        for sample in selected:
            counts[sample.coverage_cell_key] = counts.get(sample.coverage_cell_key, 0) + 1
        assert all(count <= 2 for count in counts.values())

    def test_rollover_relaxes_cap_when_quota_unmet(self):
        row_summaries = {
            f"row-{i}": _summary(f"row-{i}", "cell-1", body_length=100 + i)
            for i in range(5)
        }
        coverage_actual_rows = [_coverage_row("cell-1", planned_target=10, actual_ok=5)]
        selected = select_representative_samples(
            quota=4, row_summaries=row_summaries, coverage_actual_rows=coverage_actual_rows,
            chosen_row_ids=set(),
        )
        assert len(selected) == 4  # cap 2 -> relaxed until quota met

    def test_already_chosen_rows_excluded(self):
        row_summaries = {
            "row-a": _summary("row-a", "cell-1"),
            "row-b": _summary("row-b", "cell-1"),
        }
        coverage_actual_rows = [_coverage_row("cell-1", planned_target=10, actual_ok=2)]
        selected = select_representative_samples(
            quota=5, row_summaries=row_summaries, coverage_actual_rows=coverage_actual_rows,
            chosen_row_ids={"row-a", "row-b"},
        )
        assert selected == []

    def test_cells_with_zero_actual_ok_excluded(self):
        row_summaries = {"row-a": _summary("row-a", "cell-1")}
        coverage_actual_rows = [_coverage_row("cell-1", planned_target=10, actual_ok=0)]
        selected = select_representative_samples(
            quota=5, row_summaries=row_summaries, coverage_actual_rows=coverage_actual_rows,
            chosen_row_ids=set(),
        )
        assert selected == []


class TestSelectReviewSamples:
    def test_anomaly_quota_formula(self):
        # min(10, ceil(sample_count*2/3))
        for sample_count, expected in [(15, 10), (12, 8), (9, 6), (3, 2)]:
            assert min(10, math.ceil(sample_count * 2 / 3)) == expected

    def test_total_never_exceeds_sample_count(self):
        row_summaries = {f"row-{i}": _summary(f"row-{i}", "cell-1", body_length=i) for i in range(20)}
        coverage_actual_rows = [_coverage_row("cell-1", planned_target=20, actual_ok=20)]
        selected = select_review_samples(
            sample_count=15, row_summaries=row_summaries, duplicate_groups=[],
            coverage_actual_rows=coverage_actual_rows, format_result={}, pdf_dir=None,
        )
        assert len(selected) <= 15

    def test_sample_rank_is_sequential(self):
        row_summaries = {f"row-{i}": _summary(f"row-{i}", "cell-1", body_length=i) for i in range(5)}
        coverage_actual_rows = [_coverage_row("cell-1", planned_target=5, actual_ok=5)]
        selected = select_review_samples(
            sample_count=5, row_summaries=row_summaries, duplicate_groups=[],
            coverage_actual_rows=coverage_actual_rows, format_result={}, pdf_dir=None,
        )
        assert [s.sample_rank for s in selected] == list(range(1, len(selected) + 1))

    def test_missing_pdf_dir_marks_unavailable_in_secondary_reasons(self):
        row_summaries = {"row-a": _summary("row-a", "cell-1")}
        coverage_actual_rows = [_coverage_row("cell-1", planned_target=1, actual_ok=1)]
        selected = select_review_samples(
            sample_count=1, row_summaries=row_summaries, duplicate_groups=[],
            coverage_actual_rows=coverage_actual_rows, format_result={}, pdf_dir=None,
        )
        assert "pdf_unavailable" in selected[0].secondary_reasons


class TestPdfStatus:
    def test_no_pdf_dir_is_unavailable(self):
        path, status = pdf_status("row-1", None)
        assert status == "pdf_unavailable"
        assert path == ""

    def test_missing_file_is_unavailable(self, tmp_path):
        path, status = pdf_status("row-1", tmp_path)
        assert status == "pdf_unavailable"
        assert path == str(tmp_path / "row-1.pdf")

    def test_valid_pdf_is_ok(self, tmp_path):
        reportlab_canvas = pytest_importorskip_reportlab()
        pdf_path = tmp_path / "row-1.pdf"
        c = reportlab_canvas.Canvas(str(pdf_path))
        c.drawString(100, 750, "test")
        c.save()
        path, status = pdf_status("row-1", tmp_path)
        assert status == "ok"

    def test_corrupt_file_is_unavailable(self, tmp_path):
        pdf_path = tmp_path / "row-1.pdf"
        pdf_path.write_bytes(b"not a real pdf")
        path, status = pdf_status("row-1", tmp_path)
        assert status == "pdf_unavailable"


def pytest_importorskip_reportlab():
    import pytest as _pytest

    reportlab_canvas = _pytest.importorskip("reportlab.pdfgen.canvas")
    return reportlab_canvas
