"""계획 대비 실제 생성 분포 — 설계 문서 §7.1 + §12.4 ``coverage_actual.csv``.

스트리밍 중 셀 개수(최대 1,025개, v1a)만큼만 메모리에 들고 원문은 절대
누적하지 않는다(§14.9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rd2.audit.row_contract import AuditRow

if TYPE_CHECKING:
    from rd2.generators.generation_plan_schema import GenerationPlan

COVERAGE_ACTUAL_FIELDNAMES = [
    "plan_run_id",
    "coverage_cell_key",
    "cell_state",
    "planned_target",
    "attempted",
    "actual_ok",
    "error_count",
    "shortage",
    "overfill",
    "fill_rate",
    "coverage_status",
]


class CoverageActualAccumulator:
    def __init__(self) -> None:
        self._attempted: dict[str, int] = {}
        self._actual_ok: dict[str, int] = {}
        self._seed_source_ok_counts: dict[str, int] = {}
        self._total_ok = 0

    def add_row(self, row: AuditRow) -> None:
        key = row.coverage_cell_key
        if key:
            self._attempted[key] = self._attempted.get(key, 0) + 1
            if row.is_ok:
                self._actual_ok[key] = self._actual_ok.get(key, 0) + 1

        if row.is_ok:
            self._total_ok += 1
            if row.seed_candidate_id:
                self._seed_source_ok_counts[row.seed_candidate_id] = (
                    self._seed_source_ok_counts.get(row.seed_candidate_id, 0) + 1
                )

    def max_seed_source_share(self) -> float | None:
        """§7.1 "동일 seed source의 최대 점유율" — actual_ok 행 전체 대비 비율."""
        if self._total_ok == 0 or not self._seed_source_ok_counts:
            return None
        return max(self._seed_source_ok_counts.values()) / self._total_ok

    def finalize(self, plan: "GenerationPlan") -> list[dict]:
        rows: list[dict] = []
        for cell in plan.cells:
            key = cell.coverage_cell_key
            planned_target = cell.requested_target
            attempted = self._attempted.get(key, 0)
            actual_ok = self._actual_ok.get(key, 0)
            error_count = attempted - actual_ok
            shortage = max(0, planned_target - actual_ok)
            overfill = max(0, actual_ok - planned_target)
            fill_rate = (actual_ok / planned_target) if planned_target > 0 else None

            if planned_target == 0:
                coverage_status = "not_applicable"
            elif overfill > 0:
                coverage_status = "overfill"
            elif shortage > 0:
                coverage_status = "shortage"
            else:
                coverage_status = "filled"

            rows.append(
                {
                    "plan_run_id": plan.run_id,
                    "coverage_cell_key": key,
                    "cell_state": cell.cell_state,
                    "planned_target": planned_target,
                    "attempted": attempted,
                    "actual_ok": actual_ok,
                    "error_count": error_count,
                    "shortage": shortage,
                    "overfill": overfill,
                    "fill_rate": "" if fill_rate is None else f"{fill_rate:.6f}",
                    "coverage_status": coverage_status,
                }
            )
        return rows
