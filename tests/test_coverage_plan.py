"""coverage_plan.py 회귀 테스트.

설계 문서 docs/design-coverage-matrix-diversity-audit-20260723.md §12.1(셀 상태와
열거), §12.2(할당 공식과 tie-break), §14.1(candidate capacity와 span/fallback 분할)의
계약을 고정한다.
"""

import pytest

from rd2.generators.agency_categories import AGENCY_CATEGORIES
from rd2.generators.candidate_profile import ProfileRow
from rd2.generators.coverage_plan import (
    CellRecord,
    CoverageCell,
    CoverageKey,
    GenerationPlanValidationError,
    PlanAllocationError,
    allocate_grade,
    classification_for_clause,
    compute_candidate_profile_counts,
    coverage_cell_key,
    enumerate_valid_cells,
    largest_remainder_allocate,
)
from rd2.generators.template_matrix import STATUS_AWARE_TARGETS


class TestClassificationForClause:
    def test_clauses_1_to_4_are_c(self):
        for clause_no in ("1", "2", "3", "4"):
            assert classification_for_clause(clause_no) == "C"

    def test_clauses_5_to_8_are_s(self):
        for clause_no in ("5", "6", "7", "8"):
            assert classification_for_clause(clause_no) == "S"

    def test_unknown_clause_raises(self):
        with pytest.raises(ValueError):
            classification_for_clause("99")


class TestCoverageCellKeyFormat:
    def test_admin_status_empty_uses_dash(self):
        key = CoverageKey("C", "1", "legal_secret", "report", "central_ministry", "")
        assert coverage_cell_key(key) == "C|1|legal_secret|report|central_ministry|-"

    def test_admin_status_present(self):
        key = CoverageKey("S", "5", "bid_contract", "bid_notice", "public_corporation", "공고 전")
        assert coverage_cell_key(key) == "S|5|bid_contract|bid_notice|public_corporation|공고 전"


class TestEnumerateValidCells:
    """설계 문서 §12.1: STATUS_AWARE_TARGETS × 기관군 전수 열거."""

    def test_total_cell_count_matches_status_aware_targets_times_agencies(self):
        records = enumerate_valid_cells()
        assert len(records) == len(STATUS_AWARE_TARGETS) * len(AGENCY_CATEGORIES)

    def test_no_excluded_cells_by_default(self):
        records = enumerate_valid_cells()
        assert all(r.cell_state != "excluded" for r in records)

    def test_defaults_to_fallback_only_without_counts(self):
        records = enumerate_valid_cells()
        assert all(r.cell_state == "fallback_only" and r.real_candidate_count == 0 for r in records)

    def test_matrix_valid_when_count_present(self):
        target = STATUS_AWARE_TARGETS[0]
        classification = classification_for_clause(target.clause_no)
        agency = sorted(AGENCY_CATEGORIES)[0]
        count_key = (classification, target.clause_no, target.subclause_key, target.doc_type, agency)
        records = enumerate_valid_cells(candidate_profile_counts={count_key: 3})
        matched = [
            r
            for r in records
            if r.key.clause_no == target.clause_no
            and r.key.subclause_key == target.subclause_key
            and r.key.doc_type == target.doc_type
            and r.key.admin_status == (target.admin_status or "")
            and r.key.agency_category == agency
        ]
        assert len(matched) == 1
        assert matched[0].cell_state == "matrix_valid"
        assert matched[0].real_candidate_count == 3

    def test_explicit_exclusion_wins_over_candidate_count(self):
        target = STATUS_AWARE_TARGETS[0]
        classification = classification_for_clause(target.clause_no)
        agency = sorted(AGENCY_CATEGORIES)[0]
        key = CoverageKey(
            classification, target.clause_no, target.subclause_key, target.doc_type,
            agency, target.admin_status or "",
        )
        count_key = (classification, target.clause_no, target.subclause_key, target.doc_type, agency)
        records = enumerate_valid_cells(
            candidate_profile_counts={count_key: 5},
            exclusions={key: "domain-verified-impossible"},
        )
        matched = [r for r in records if r.key == key]
        assert len(matched) == 1
        assert matched[0].cell_state == "excluded"


class TestBroadcastCountsAcrossAdminStatus:
    """§12.1 "admin_status는 이 키에 없다" — 같은 (등급,조항,세부조항,문서유형,기관군)
    그룹의 여러 admin_status 셀은 동일 count를 조회해야 한다(broadcast)."""

    def test_same_count_across_admin_status_variants(self):
        def group_key(t):
            return (t.clause_no, t.subclause_key, t.doc_type)

        counts_per_group: dict = {}
        for t in STATUS_AWARE_TARGETS:
            counts_per_group.setdefault(group_key(t), []).append(t)
        targets_with_status = [
            targets[0] for targets in counts_per_group.values() if len(targets) > 1
        ]
        assert targets_with_status, "행정상태가 2개 이상인 (조항,세부조항,문서유형) fixture가 있어야 함"
        target = targets_with_status[0]
        classification = classification_for_clause(target.clause_no)
        agency = sorted(AGENCY_CATEGORIES)[0]
        count_key = (classification, target.clause_no, target.subclause_key, target.doc_type, agency)

        records = enumerate_valid_cells(candidate_profile_counts={count_key: 7})
        matched = [
            r
            for r in records
            if r.key.clause_no == target.clause_no
            and r.key.subclause_key == target.subclause_key
            and r.key.doc_type == target.doc_type
            and r.key.agency_category == agency
        ]
        assert len(matched) >= 2
        assert all(r.real_candidate_count == 7 for r in matched)
        assert all(r.cell_state == "matrix_valid" for r in matched)


class TestComputeCandidateProfileCounts:
    def _row(self, **overrides):
        defaults = dict(
            candidate_id="cand-1", extraction_id="ext-1", source="alio",
            doc_type="bid_notice", doc_id="1", clause_no="5",
            subclause_key="bid_contract", agency_category="central_ministry",
            profile_status="resolved", unresolved_reason="",
        )
        defaults.update(overrides)
        return ProfileRow(**defaults)

    def test_counts_resolved_rows_only(self):
        rows = [
            self._row(),
            self._row(candidate_id="cand-2"),
            self._row(
                candidate_id="cand-3", profile_status="unresolved",
                agency_category="", unresolved_reason="not_found_in_documents_table",
            ),
        ]
        counts = compute_candidate_profile_counts(rows)
        key = ("S", "5", "bid_contract", "bid_notice", "central_ministry")
        assert counts[key] == 2
        assert len(counts) == 1

    def test_no_admin_status_axis_in_key(self):
        counts = compute_candidate_profile_counts([self._row()])
        key = next(iter(counts))
        assert len(key) == 5


class TestLargestRemainderAllocate:
    """설계 문서 §12.2 worked example: 목표 20, 셀 3개, 최소 2, weight 0.5/0.3/0.2
    -> 남은 14 -> floor 7/4/2, remainder 1건은 세 번째 셀로 -> 최종 9/6/5."""

    def test_worked_example_from_design_doc(self):
        result = largest_remainder_allocate(14, {"a": 0.5, "b": 0.3, "c": 0.2}, tie_break=lambda k: k)
        # floor: 7/4/2, 잔여 1건은 remainder 0.8인 "c"로 -> 7/4/3
        assert result == {"a": 7, "b": 4, "c": 3}
        with_minimum = {k: v + 2 for k, v in result.items()}
        assert with_minimum == {"a": 9, "b": 6, "c": 5}
        assert sum(with_minimum.values()) == 20

    def test_sum_always_equals_total(self):
        for total, weights in [
            (100, {"a": 1.0, "b": 1.0, "c": 1.0}),
            (7, {"a": 0.1, "b": 0.2, "c": 0.7}),
            (1, {"a": 0.5, "b": 0.5}),
            (0, {"a": 1.0}),
        ]:
            result = largest_remainder_allocate(total, weights, tie_break=lambda k: k)
            assert sum(result.values()) == total

    def test_tie_break_used_on_equal_remainder(self):
        # 두 key 모두 remainder 0.5 -> tie_break 오름차순인 "a"가 먼저 +1을 받는다.
        result = largest_remainder_allocate(1, {"b": 0.5, "a": 0.5}, tie_break=lambda k: k)
        assert result == {"a": 1, "b": 0}

    def test_negative_total_rejected(self):
        with pytest.raises(ValueError):
            largest_remainder_allocate(-1, {"a": 1.0}, tie_break=lambda k: k)

    def test_zero_total_with_no_weights(self):
        assert largest_remainder_allocate(0, {}, tie_break=lambda k: k) == {}

    def test_nonzero_total_with_no_weights_rejected(self):
        with pytest.raises(ValueError):
            largest_remainder_allocate(5, {}, tie_break=lambda k: k)


def _record(clause_no, agency, count, subclause_key="legal_secret", doc_type="official_document", admin_status=""):
    classification = classification_for_clause(clause_no)
    key = CoverageKey(classification, clause_no, subclause_key, doc_type, agency, admin_status)
    state = "fallback_only" if count == 0 else "matrix_valid"
    return CellRecord(key, state, count)


_EQUAL_WEIGHTS = {a: 1.0 / len(AGENCY_CATEGORIES) for a in AGENCY_CATEGORIES}


class TestAllocateGrade:
    def test_below_minimum_raises_plan_allocation_error(self):
        records = [_record("1", agency, 0) for agency in sorted(AGENCY_CATEGORIES)]
        with pytest.raises(PlanAllocationError):
            allocate_grade(
                "C", records,
                grade_target=1,  # 5개 기관군 x 최소 5건도 안 되는 목표
                minimum_per_valid_cell=5,
                agency_weights=_EQUAL_WEIGHTS,
                max_rows_per_candidate=1,
            )

    def test_zero_candidate_cell_is_fully_fallback(self):
        records = [_record("1", agency, 0) for agency in sorted(AGENCY_CATEGORIES)]
        cells = allocate_grade(
            "C", records,
            grade_target=25,
            minimum_per_valid_cell=1,
            agency_weights=_EQUAL_WEIGHTS,
            max_rows_per_candidate=1,
        )
        assert len(cells) == 5
        assert sum(c.requested_target for c in cells) == 25
        for cell in cells:
            assert cell.cell_state == "fallback_only"
            assert cell.planned_span_seeded == 0
            assert cell.planned_fallback == cell.requested_target
            assert cell.evidence_supported is False

    def test_requested_target_sums_to_grade_target(self):
        counts = [0, 3, 10, 1, 0]
        records = [
            _record("1", agency, count)
            for agency, count in zip(sorted(AGENCY_CATEGORIES), counts)
        ]
        cells = allocate_grade(
            "C", records,
            grade_target=100,
            minimum_per_valid_cell=5,
            agency_weights=_EQUAL_WEIGHTS,
            max_rows_per_candidate=1,
        )
        assert sum(c.requested_target for c in cells) == 100

    def test_excluded_cells_get_zero_target(self):
        records = [_record("1", agency, 5) for agency in sorted(AGENCY_CATEGORIES)]
        excluded_key = records[0].key
        records[0] = CellRecord(excluded_key, "excluded", 0)
        cells = allocate_grade(
            "C", records,
            grade_target=40,
            minimum_per_valid_cell=5,
            agency_weights=_EQUAL_WEIGHTS,
            max_rows_per_candidate=1,
        )
        excluded_cell = next(c for c in cells if c.key == excluded_key)
        assert excluded_cell.requested_target == 0
        assert excluded_cell.cell_state == "excluded"
        assert excluded_cell.allocation_reason == "excluded"

    def test_candidate_shortage_when_capacity_below_target(self):
        records = [_record("1", agency, 1) for agency in sorted(AGENCY_CATEGORIES)]
        cells = allocate_grade(
            "C", records,
            grade_target=50,
            minimum_per_valid_cell=5,
            agency_weights=_EQUAL_WEIGHTS,
            max_rows_per_candidate=1,
        )
        for cell in cells:
            capacity = cell.real_candidate_count * 1
            assert cell.candidate_shortage == max(0, cell.requested_target - capacity)
            assert cell.planned_span_seeded == min(cell.requested_target, capacity)
            assert cell.planned_span_seeded + cell.planned_fallback == cell.requested_target


class TestAllocateGradeIntegration:
    def test_full_c_grade_allocation_sums_to_target(self):
        records = enumerate_valid_cells()
        c_records = [r for r in records if r.key.classification == "C"]
        minimum_per_valid_cell = 1
        grade_target = len(c_records) * minimum_per_valid_cell + 500
        cells = allocate_grade(
            "C", c_records,
            grade_target=grade_target,
            minimum_per_valid_cell=minimum_per_valid_cell,
            agency_weights=_EQUAL_WEIGHTS,
            max_rows_per_candidate=1,
        )
        assert sum(c.requested_target for c in cells) == grade_target
        assert len(cells) == len(c_records)


class TestCoverageCellRoundTrip:
    def test_to_dict_from_dict_round_trip(self):
        cell = CoverageCell(
            classification="C", clause_no="1", subclause_key="legal_secret",
            doc_type="official_document", agency_category="central_ministry",
            admin_status="", cell_state="matrix_valid", requested_target=10,
            real_candidate_count=3, planned_span_seeded=3, planned_fallback=7,
            candidate_shortage=7, allocation_weight=0.5, allocation_reason="test",
        )
        round_tripped = CoverageCell.from_dict(cell.to_dict(), context="test")
        assert round_tripped == cell

    def test_from_dict_rejects_span_fallback_mismatch(self):
        data = {
            "classification": "C", "clause_no": "1", "subclause_key": "legal_secret",
            "doc_type": "official_document", "agency_category": "central_ministry",
            "admin_status": "", "cell_state": "matrix_valid", "requested_target": 10,
            "real_candidate_count": 3, "planned_span_seeded": 3, "planned_fallback": 6,
            "candidate_shortage": 7, "allocation_weight": 0.5, "allocation_reason": "test",
        }
        with pytest.raises(GenerationPlanValidationError):
            CoverageCell.from_dict(data, context="test")

    def test_from_dict_rejects_negative_target(self):
        data = {
            "classification": "C", "clause_no": "1", "subclause_key": "legal_secret",
            "doc_type": "official_document", "agency_category": "central_ministry",
            "admin_status": "", "cell_state": "matrix_valid", "requested_target": -1,
            "real_candidate_count": 3, "planned_span_seeded": 0, "planned_fallback": 0,
            "candidate_shortage": 0, "allocation_weight": 0.5, "allocation_reason": "test",
        }
        with pytest.raises(GenerationPlanValidationError):
            CoverageCell.from_dict(data, context="test")

    def test_from_dict_rejects_unknown_cell_state(self):
        data = {
            "classification": "C", "clause_no": "1", "subclause_key": "legal_secret",
            "doc_type": "official_document", "agency_category": "central_ministry",
            "admin_status": "", "cell_state": "not_a_real_state", "requested_target": 0,
            "real_candidate_count": 0, "planned_span_seeded": 0, "planned_fallback": 0,
            "candidate_shortage": 0, "allocation_weight": 0.0, "allocation_reason": "test",
        }
        with pytest.raises(GenerationPlanValidationError):
            CoverageCell.from_dict(data, context="test")
