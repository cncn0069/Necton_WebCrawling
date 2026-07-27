"""rd2.audit.orchestrator end-to-end 테스트 — 설계 문서 §12.5(입력 무결성),
§14.5(atomic 게시), §14.9(1-pass 스트리밍)의 통합 계약을 고정한다.
"""

import csv
import json

import pytest

from rd2.audit.orchestrator import determine_run_status, run_audit
from rd2.audit.row_contract import AuditContractError, REQUIRED_COLUMNS
from rd2.generators.generation_plan_schema import build_generation_plan

_AGENCY_WEIGHTS = {
    "central_ministry": 0.2,
    "education_office": 0.2,
    "metro_local_government": 0.2,
    "public_corporation": 0.2,
    "research_institute": 0.2,
}


def _build_plan(*, c_target=500, s_target=650):
    return build_generation_plan(
        c_target=c_target,
        s_target=s_target,
        minimum_per_valid_cell=1,
        agency_weights=_AGENCY_WEIGHTS,
        allocation_seed=42,
        max_rows_per_candidate=1,
        candidate_profile_rows=[],
        candidate_manifest={"run_id": "cand-run-1", "rule_version": "candidate-rules-v2-20260722"},
        candidate_profile_digest="sha256:" + "0" * 64,
        created_at="2026-07-23T00:00:00+00:00",
    )


def _csv_row(plan_cell, *, row_id, plan_run_id, slot="0", **overrides):
    row = {name: "" for name in REQUIRED_COLUMNS}
    row.update(
        row_id=row_id,
        status="ok",
        body_text=f"본문 내용입니다 {row_id}.",
        title=f"제목 {row_id}",
        cso_classification=plan_cell.classification,
        clause_no=plan_cell.clause_no,
        cso_subclause_key=plan_cell.subclause_key,
        doc_type=plan_cell.doc_type,
        document_status=plan_cell.admin_status,
        ordering_agency="고용노동부",
        agency_category=plan_cell.agency_category,
        template_id="T1-1",
        seed_candidate_id=f"cand-{row_id}",
        seed_extraction_id=f"ext-{row_id}",
        seed_source_path=f"src/{row_id}",
        coverage_cell_key=plan_cell.coverage_cell_key,
        coverage_slot=slot,
        coverage_plan_run_id=plan_run_id,
    )
    row.update(overrides)
    return row


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


class TestRunAuditEndToEnd:
    def test_publishes_all_artifacts(self, tmp_path):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        rows = [_csv_row(cell, row_id=f"row-{i}", plan_run_id=plan.run_id, slot=str(i)) for i in range(3)]
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, rows)
        output_dir = tmp_path / "audit-out"

        summary = run_audit(
            plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10, output_dir=output_dir
        )

        assert (output_dir / "coverage_actual.csv").exists()
        assert (output_dir / "duplicate_groups.csv").exists()
        assert (output_dir / "diversity_pairs.csv").exists()
        assert (output_dir / "review_samples.csv").exists()
        assert (output_dir / "row_errors.csv").exists()
        assert (output_dir / "diversity_summary.json").exists()
        assert (output_dir / "_audit_manifest.json").exists()
        assert not list(output_dir.glob(".*.tmp"))

        published = json.loads((output_dir / "diversity_summary.json").read_text(encoding="utf-8"))
        assert published == summary
        assert summary["run"]["row_count_ok"] == 3
        assert summary["run"]["row_error_count"] == 0

    def test_raw_exact_duplicate_flows_through_to_review_samples(self, tmp_path):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        base = _csv_row(cell, row_id="row-a", plan_run_id=plan.run_id, slot="0")
        dup = dict(base, row_id="row-b", coverage_slot="1")
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, [base, dup])

        summary = run_audit(
            plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10,
            output_dir=tmp_path / "audit-out",
        )
        assert summary["exact_duplicates"]["metrics"]["raw_exact_group_count"] == 1

    def test_duplicate_row_id_fails_fast(self, tmp_path):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        row = _csv_row(cell, row_id="row-a", plan_run_id=plan.run_id, slot="0")
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, [row, dict(row, coverage_slot="1")])  # 같은 row_id 두 번

        with pytest.raises(AuditContractError, match="row_id"):
            run_audit(
                plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10,
                output_dir=tmp_path / "audit-out",
            )

    def test_duplicate_coverage_slot_fails_fast(self, tmp_path):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        row_a = _csv_row(cell, row_id="row-a", plan_run_id=plan.run_id, slot="same-slot")
        row_b = _csv_row(cell, row_id="row-b", plan_run_id=plan.run_id, slot="same-slot")
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, [row_a, row_b])

        with pytest.raises(AuditContractError, match="coverage slot"):
            run_audit(
                plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10,
                output_dir=tmp_path / "audit-out",
            )

    def test_plan_run_id_mismatch_fails_fast(self, tmp_path):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        row = _csv_row(cell, row_id="row-a", plan_run_id="sha256:" + "f" * 64, slot="0")
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, [row])

        with pytest.raises(AuditContractError, match="run_id 불일치"):
            run_audit(
                plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10,
                output_dir=tmp_path / "audit-out",
            )

    def test_missing_required_columns_fails_fast(self, tmp_path):
        """오늘의 실제 generate_cs_pilot.py CSV_FIELDNAMES는 agency_category/
        coverage_cell_key/coverage_slot/coverage_plan_run_id가 없다(§5 이전) —
        그런 CSV를 넣으면 명확히 fail-fast해야 한다는 걸 회귀 테스트로 고정한다."""
        plan = _build_plan()
        legacy_fieldnames = [
            "row_id", "status", "body_text", "title", "cso_classification", "clause_no",
            "cso_subclause_key", "doc_type", "document_status", "ordering_agency",
            "template_id", "seed_candidate_id", "seed_extraction_id", "seed_source_path",
            "cell_key", "cell_fallback_ratio", "cell_zero_candidate_exception",
        ]
        csv_path = tmp_path / "legacy_generated.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=legacy_fieldnames)
            writer.writeheader()

        with pytest.raises(AuditContractError, match="필수 필드가 없습니다"):
            run_audit(
                plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10,
                output_dir=tmp_path / "audit-out",
            )

    def test_refuses_to_overwrite_completed_audit_run(self, tmp_path):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        row = _csv_row(cell, row_id="row-a", plan_run_id=plan.run_id, slot="0")
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, [row])
        output_dir = tmp_path / "audit-out"

        run_audit(plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10, output_dir=output_dir)
        with pytest.raises(RuntimeError, match="이미 완료된"):
            run_audit(plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10, output_dir=output_dir)

    def test_write_failure_leaves_no_completion_manifest(self, tmp_path, monkeypatch):
        plan = _build_plan()
        cell = next(c for c in plan.cells if c.classification == "C")
        row = _csv_row(cell, row_id="row-a", plan_run_id=plan.run_id, slot="0")
        csv_path = tmp_path / "generated.csv"
        _write_csv(csv_path, [row])
        output_dir = tmp_path / "audit-out"

        import rd2.audit.orchestrator as orchestrator_module

        real_replace = orchestrator_module.os.replace
        call_count = {"n": 0}

        def flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated disk failure")
            return real_replace(src, dst)

        monkeypatch.setattr(orchestrator_module.os, "replace", flaky_replace)

        with pytest.raises(OSError):
            run_audit(plan=plan, input_csv=csv_path, pdf_dir=None, sample_count=10, output_dir=output_dir)

        assert not (output_dir / "_audit_manifest.json").exists()


class TestDetermineRunStatus:
    def test_complete_when_no_errors_and_no_shortage(self):
        status = determine_run_status(
            row_errors=[], coverage_actual_rows=[{"shortage": 0, "overfill": 0}], total_ok=5
        )
        assert status == "complete"

    def test_degraded_when_shortage_present(self):
        status = determine_run_status(
            row_errors=[], coverage_actual_rows=[{"shortage": 3, "overfill": 0}], total_ok=5
        )
        assert status == "degraded"

    def test_failed_when_zero_ok_rows(self):
        status = determine_run_status(row_errors=[], coverage_actual_rows=[], total_ok=0)
        assert status == "failed"
