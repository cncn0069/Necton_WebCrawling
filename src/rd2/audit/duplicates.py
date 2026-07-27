"""exact/normalized 중복 검사 — 설계 문서 §7.2 + §12.4.

"exact duplicate pair를 완전 조합으로 펼치지 않는다. group ID와 representative
pair만 쓰고 group size는 summary에 기록한다"(§14.8) — 그룹당 row_id 전체
목록이 아니라 최솟값 2개(대표 pair 구성용)만 O(그룹 수) 메모리로 들고 있는다.
"""

from __future__ import annotations

from rd2.audit.content_normalize import normalized_content_hash, raw_content_hash
from rd2.audit.row_contract import AuditRow

DUPLICATE_GROUPS_FIELDNAMES = [
    "audit_run_id",
    "metric",
    "group_id",
    "representative_row_id",
    "group_size",
    "emitted_pair_count",
    "pair_rows_truncated",
]

DIVERSITY_PAIRS_FIELDNAMES = [
    "audit_run_id",
    "comparison_scope_key",
    "row_id_a",
    "row_id_b",
    "metric",
    "metric_value",
    "scope_percentile",
    "reciprocal",
    "shared_candidate",
    "shared_extraction",
    "rank_a_to_b",
    "rank_b_to_a",
]

RAW_EXACT = "raw_exact"
NORMALIZED_EXACT = "normalized_exact"


class _HashGroup:
    __slots__ = (
        "count",
        "min1_row_id", "min1_candidate", "min1_extraction",
        "min2_row_id", "min2_candidate", "min2_extraction",
    )

    def __init__(self) -> None:
        self.count = 0
        self.min1_row_id: str | None = None
        self.min1_candidate = ""
        self.min1_extraction = ""
        self.min2_row_id: str | None = None
        self.min2_candidate = ""
        self.min2_extraction = ""

    def add(self, row_id: str, candidate_id: str, extraction_id: str) -> None:
        self.count += 1
        if self.min1_row_id is None or row_id < self.min1_row_id:
            self.min2_row_id, self.min2_candidate, self.min2_extraction = (
                self.min1_row_id, self.min1_candidate, self.min1_extraction,
            )
            self.min1_row_id, self.min1_candidate, self.min1_extraction = row_id, candidate_id, extraction_id
        elif self.min2_row_id is None or row_id < self.min2_row_id:
            self.min2_row_id, self.min2_candidate, self.min2_extraction = row_id, candidate_id, extraction_id


class ExactDuplicateAccumulator:
    """status=="ok" 행만 넣는다 — 호출자(orchestrator)가 그 필터링을 책임진다."""

    def __init__(self) -> None:
        self._raw_groups: dict[str, _HashGroup] = {}
        self._normalized_groups: dict[str, _HashGroup] = {}

    def add_row(self, row: AuditRow) -> None:
        raw_hash = raw_content_hash(row.title, row.body_text)
        normalized_hash = normalized_content_hash(
            row.title, row.body_text, ordering_agency=row.ordering_agency
        )
        self._raw_groups.setdefault(raw_hash, _HashGroup()).add(
            row.row_id, row.seed_candidate_id, row.seed_extraction_id
        )
        self._normalized_groups.setdefault(normalized_hash, _HashGroup()).add(
            row.row_id, row.seed_candidate_id, row.seed_extraction_id
        )

    def _finalize_metric(
        self, metric: str, groups: dict[str, _HashGroup], *, audit_run_id: str
    ) -> tuple[list[dict], list[dict]]:
        group_rows: list[dict] = []
        pair_rows: list[dict] = []
        for content_hash, group in groups.items():
            if group.count < 2:
                continue
            group_id = f"{metric}:{content_hash}"
            group_rows.append(
                {
                    "audit_run_id": audit_run_id,
                    "metric": metric,
                    "group_id": group_id,
                    "representative_row_id": group.min1_row_id,
                    "group_size": group.count,
                    "emitted_pair_count": 1,
                    "pair_rows_truncated": group.count > 2,
                }
            )
            row_id_a, row_id_b = sorted((group.min1_row_id, group.min2_row_id))
            candidate_a, extraction_a = (
                (group.min1_candidate, group.min1_extraction)
                if row_id_a == group.min1_row_id
                else (group.min2_candidate, group.min2_extraction)
            )
            candidate_b, extraction_b = (
                (group.min2_candidate, group.min2_extraction)
                if row_id_b == group.min2_row_id
                else (group.min1_candidate, group.min1_extraction)
            )
            pair_rows.append(
                {
                    "audit_run_id": audit_run_id,
                    "comparison_scope_key": "GLOBAL",
                    "row_id_a": row_id_a,
                    "row_id_b": row_id_b,
                    "metric": metric,
                    "metric_value": 1.0,
                    "scope_percentile": "",
                    "reciprocal": False,
                    "shared_candidate": bool(candidate_a) and candidate_a == candidate_b,
                    "shared_extraction": bool(extraction_a) and extraction_a == extraction_b,
                    "rank_a_to_b": "",
                    "rank_b_to_a": "",
                }
            )
        return group_rows, pair_rows

    def finalize(self, *, audit_run_id: str) -> tuple[list[dict], list[dict]]:
        raw_groups, raw_pairs = self._finalize_metric(RAW_EXACT, self._raw_groups, audit_run_id=audit_run_id)
        norm_groups, norm_pairs = self._finalize_metric(
            NORMALIZED_EXACT, self._normalized_groups, audit_run_id=audit_run_id
        )
        return raw_groups + norm_groups, raw_pairs + norm_pairs
