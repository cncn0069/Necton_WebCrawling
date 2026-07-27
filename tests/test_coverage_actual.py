"""rd2.audit.coverage_actual 회귀 테스트 — 설계 문서 §7.1 + §12.4."""

from rd2.audit.coverage_actual import CoverageActualAccumulator
from rd2.audit.row_contract import AuditRow
from rd2.generators.coverage_plan import CoverageCell


class _FakePlan:
    def __init__(self, run_id: str, cells: list[CoverageCell]) -> None:
        self.run_id = run_id
        self.cells = cells


def _cell(key_suffix: str, requested_target: int, cell_state: str = "matrix_valid") -> CoverageCell:
    return CoverageCell(
        classification="C", clause_no="1", subclause_key="legal_secret",
        doc_type="report", agency_category="central_ministry", admin_status="",
        cell_state=cell_state, requested_target=requested_target, real_candidate_count=1,
        planned_span_seeded=requested_target, planned_fallback=0, candidate_shortage=0,
        allocation_weight=1.0, allocation_reason=f"test-{key_suffix}",
    )


def _row(row_id: str, coverage_cell_key: str, *, status: str = "ok", seed_candidate_id: str = "") -> AuditRow:
    return AuditRow(
        source_row_number=1, row_id=row_id, status=status, body_text="본문", title="제목",
        classification="C", clause_no="1", subclause_key="legal_secret", doc_type="report",
        document_status="", ordering_agency="고용노동부", agency_category="central_ministry",
        template_id="T1-1", seed_candidate_id=seed_candidate_id, seed_extraction_id="",
        seed_source_path="", coverage_cell_key=coverage_cell_key, coverage_slot="0",
        coverage_plan_run_id="sha256:abc",
    )


class TestCoverageActualAccumulator:
    def test_filled_when_actual_equals_planned(self):
        cell = _cell("a", requested_target=2)
        plan = _FakePlan("sha256:run", [cell])
        acc = CoverageActualAccumulator()
        acc.add_row(_row("r1", cell.coverage_cell_key))
        acc.add_row(_row("r2", cell.coverage_cell_key))
        result = acc.finalize(plan)
        assert len(result) == 1
        row = result[0]
        assert row["planned_target"] == 2
        assert row["attempted"] == 2
        assert row["actual_ok"] == 2
        assert row["shortage"] == 0
        assert row["overfill"] == 0
        assert row["coverage_status"] == "filled"
        assert row["fill_rate"] == "1.000000"

    def test_shortage_when_below_target(self):
        cell = _cell("a", requested_target=5)
        plan = _FakePlan("sha256:run", [cell])
        acc = CoverageActualAccumulator()
        acc.add_row(_row("r1", cell.coverage_cell_key))
        result = acc.finalize(plan)[0]
        assert result["shortage"] == 4
        assert result["overfill"] == 0
        assert result["coverage_status"] == "shortage"

    def test_overfill_when_above_target(self):
        cell = _cell("a", requested_target=1)
        plan = _FakePlan("sha256:run", [cell])
        acc = CoverageActualAccumulator()
        acc.add_row(_row("r1", cell.coverage_cell_key))
        acc.add_row(_row("r2", cell.coverage_cell_key))
        result = acc.finalize(plan)[0]
        assert result["shortage"] == 0
        assert result["overfill"] == 1
        assert result["coverage_status"] == "overfill"

    def test_zero_target_is_not_applicable(self):
        cell = _cell("a", requested_target=0, cell_state="excluded")
        plan = _FakePlan("sha256:run", [cell])
        acc = CoverageActualAccumulator()
        result = acc.finalize(plan)[0]
        assert result["coverage_status"] == "not_applicable"
        assert result["fill_rate"] == ""

    def test_error_rows_counted_but_not_actual_ok(self):
        cell = _cell("a", requested_target=2)
        plan = _FakePlan("sha256:run", [cell])
        acc = CoverageActualAccumulator()
        acc.add_row(_row("r1", cell.coverage_cell_key, status="ok"))
        acc.add_row(_row("r2", cell.coverage_cell_key, status="error"))
        result = acc.finalize(plan)[0]
        assert result["attempted"] == 2
        assert result["actual_ok"] == 1
        assert result["error_count"] == 1

    def test_max_seed_source_share(self):
        cell = _cell("a", requested_target=4)
        acc = CoverageActualAccumulator()
        acc.add_row(_row("r1", cell.coverage_cell_key, seed_candidate_id="cand-x"))
        acc.add_row(_row("r2", cell.coverage_cell_key, seed_candidate_id="cand-x"))
        acc.add_row(_row("r3", cell.coverage_cell_key, seed_candidate_id="cand-y"))
        assert acc.max_seed_source_share() == 2 / 3

    def test_max_seed_source_share_none_when_no_ok_rows(self):
        acc = CoverageActualAccumulator()
        assert acc.max_seed_source_share() is None
