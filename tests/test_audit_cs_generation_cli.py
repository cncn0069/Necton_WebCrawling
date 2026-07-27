"""scripts/audit_cs_generation.py end-to-end 테스트."""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit_cs_generation as audit_cli  # noqa: E402

from rd2.audit.row_contract import REQUIRED_COLUMNS
from rd2.generators.generation_plan_schema import build_generation_plan, write_generation_plan_atomic

_AGENCY_WEIGHTS = {
    "central_ministry": 0.2,
    "education_office": 0.2,
    "metro_local_government": 0.2,
    "public_corporation": 0.2,
    "research_institute": 0.2,
}


def _build_and_publish_plan(plan_dir: Path):
    plan = build_generation_plan(
        c_target=500,
        s_target=650,
        minimum_per_valid_cell=1,
        agency_weights=_AGENCY_WEIGHTS,
        allocation_seed=42,
        max_rows_per_candidate=1,
        candidate_profile_rows=[],
        candidate_manifest={"run_id": "cand-run-1", "rule_version": "candidate-rules-v2-20260722"},
        candidate_profile_digest="sha256:" + "0" * 64,
        created_at="2026-07-23T00:00:00+00:00",
    )
    write_generation_plan_atomic(plan_dir, plan)
    return plan


def _write_generated_csv(csv_path: Path, plan) -> None:
    cell = next(c for c in plan.cells if c.classification == "C")
    row = {name: "" for name in REQUIRED_COLUMNS}
    row.update(
        row_id="row-a",
        status="ok",
        body_text="본문 내용입니다.",
        title="제목",
        cso_classification=cell.classification,
        clause_no=cell.clause_no,
        cso_subclause_key=cell.subclause_key,
        doc_type=cell.doc_type,
        document_status=cell.admin_status,
        ordering_agency="고용노동부",
        agency_category=cell.agency_category,
        template_id="T1-1",
        seed_candidate_id="cand-a",
        seed_extraction_id="ext-a",
        seed_source_path="src/a",
        coverage_cell_key=cell.coverage_cell_key,
        coverage_slot="0",
        coverage_plan_run_id=plan.run_id,
    )
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerow(row)


class TestAuditCsGenerationCli:
    def test_end_to_end_publishes_audit(self, tmp_path):
        plan_dir = tmp_path / "run-1"
        plan = _build_and_publish_plan(plan_dir)
        csv_path = tmp_path / "generated.csv"
        _write_generated_csv(csv_path, plan)

        exit_code = audit_cli.main(
            [
                "--plan", str(plan_dir / "generation_plan.json"),
                "--input", str(csv_path),
                "--sample-count", "10",
                "--output-dir", str(plan_dir),
            ]
        )
        assert exit_code == 0
        assert (plan_dir / "diversity_summary.json").exists()
        assert (plan_dir / "_audit_manifest.json").exists()
        # v1a의 _manifest.json(plan completion marker)은 그대로 남아있어야 한다.
        assert (plan_dir / "_manifest.json").exists()

        summary = json.loads((plan_dir / "diversity_summary.json").read_text(encoding="utf-8"))
        assert summary["run"]["plan_run_id"] == plan.run_id

    def test_contract_violation_returns_exit_code_1(self, tmp_path, capsys):
        plan_dir = tmp_path / "run-1"
        _build_and_publish_plan(plan_dir)
        bad_csv = tmp_path / "bad.csv"
        with bad_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["row_id", "status"])
            writer.writeheader()

        exit_code = audit_cli.main(
            [
                "--plan", str(plan_dir / "generation_plan.json"),
                "--input", str(bad_csv),
                "--output-dir", str(tmp_path / "audit-out"),
            ]
        )
        assert exit_code == 1
        assert "계약 위반" in capsys.readouterr().err
