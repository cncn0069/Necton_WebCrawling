"""generation_plan_schema.py 회귀 테스트.

설계 문서 §4.1(typed contract boundary), §12.3(run_id 재현성), §14.5(atomic
publication)의 계약을 고정한다.
"""

import json

import pytest

from rd2.generators.coverage_plan import GenerationPlanValidationError
from rd2.generators.generation_plan_schema import (
    GenerationPlan,
    build_generation_plan,
    write_generation_plan_atomic,
)

_MINIMAL_ALLOCATION = dict(
    minimum_per_valid_cell=1,
    agency_weights={
        "central_ministry": 0.2,
        "education_office": 0.2,
        "metro_local_government": 0.2,
        "public_corporation": 0.2,
        "research_institute": 0.2,
    },
    allocation_seed=42,
    max_rows_per_candidate=1,
)


def _build_small_plan(*, c_target=500, s_target=650, created_at="2026-07-23T00:00:00+00:00"):
    return build_generation_plan(
        c_target=c_target,
        s_target=s_target,
        candidate_profile_rows=[],
        candidate_manifest={"run_id": "cand-run-1", "rule_version": "candidate-rules-v2-20260722"},
        candidate_profile_digest="sha256:" + "0" * 64,
        created_at=created_at,
        **_MINIMAL_ALLOCATION,
    )


class TestBuildGenerationPlan:
    def test_cell_targets_sum_to_grade_targets(self):
        plan = _build_small_plan(c_target=500, s_target=650)
        c_sum = sum(c.requested_target for c in plan.cells if c.classification == "C")
        s_sum = sum(c.requested_target for c in plan.cells if c.classification == "S")
        assert c_sum == 500
        assert s_sum == 650

    def test_admin_status_rows_use_low_default_ratio(self):
        plan = _build_small_plan(c_target=500, s_target=650)

        c_admin = sum(
            c.requested_target
            for c in plan.cells
            if c.classification == "C" and c.admin_status
        )
        s_admin = sum(
            c.requested_target
            for c in plan.cells
            if c.classification == "S" and c.admin_status
        )
        assert c_admin == 50
        assert s_admin == 65
        assert plan.allocation["admin_status_ratio"] == 0.10

    def test_admin_status_ratio_can_be_disabled(self):
        plan = build_generation_plan(
            c_target=500,
            s_target=650,
            candidate_profile_rows=[],
            candidate_manifest={
                "run_id": "cand-run-1",
                "rule_version": "candidate-rules-v2-20260722",
            },
            candidate_profile_digest="sha256:" + "0" * 64,
            created_at="2026-07-23T00:00:00+00:00",
            admin_status_ratio=0.0,
            **_MINIMAL_ALLOCATION,
        )

        assert sum(c.requested_target for c in plan.cells if c.admin_status) == 0
        assert sum(c.requested_target for c in plan.cells) == 1150

    def test_admin_status_ratio_accepts_full_boundary(self):
        plan = build_generation_plan(
            c_target=500,
            s_target=650,
            candidate_profile_rows=[],
            candidate_manifest={
                "run_id": "cand-run-1",
                "rule_version": "candidate-rules-v2-20260722",
            },
            candidate_profile_digest="sha256:" + "0" * 64,
            created_at="2026-07-23T00:00:00+00:00",
            admin_status_ratio=1.0,
            **_MINIMAL_ALLOCATION,
        )

        assert sum(
            c.requested_target for c in plan.cells if not c.admin_status
        ) == 0
        assert sum(c.requested_target for c in plan.cells if c.admin_status) == 1150

    @pytest.mark.parametrize("ratio", (-0.01, 1.01))
    def test_invalid_admin_status_ratio_is_rejected(self, ratio):
        with pytest.raises(ValueError, match="admin_status_ratio"):
            build_generation_plan(
                c_target=500,
                s_target=650,
                candidate_profile_rows=[],
                candidate_manifest={
                    "run_id": "cand-run-1",
                    "rule_version": "candidate-rules-v2-20260722",
                },
                candidate_profile_digest="sha256:" + "0" * 64,
                created_at="2026-07-23T00:00:00+00:00",
                admin_status_ratio=ratio,
                **_MINIMAL_ALLOCATION,
            )

    def test_run_id_stable_across_created_at(self):
        plan_a = _build_small_plan(created_at="2026-07-23T00:00:00+00:00")
        plan_b = _build_small_plan(created_at="2026-07-24T12:00:00+00:00")
        assert plan_a.run_id == plan_b.run_id
        assert plan_a.matrix_hash == plan_b.matrix_hash

    def test_run_id_changes_with_targets(self):
        plan_a = _build_small_plan(c_target=500, s_target=650)
        plan_b = _build_small_plan(c_target=501, s_target=650)
        assert plan_a.run_id != plan_b.run_id

    def test_matrix_hash_unaffected_by_candidate_counts(self):
        """설계 문서 §4.2: matrix_hash는 candidate 수를 포함하지 않는다 — 후보
        프로파일이 채워져도 매트릭스 정체성(matrix_hash)은 그대로여야 한다."""
        from rd2.generators.candidate_profile import ProfileRow

        plan_without_counts = _build_small_plan()
        profile_row = ProfileRow(
            candidate_id="c1", extraction_id="e1", source="alio", doc_type="bid_notice",
            doc_id="1", clause_no="5", subclause_key="bid_contract",
            agency_category="central_ministry", profile_status="resolved", unresolved_reason="",
        )
        plan_with_counts = build_generation_plan(
            c_target=500,
            s_target=650,
            candidate_profile_rows=[profile_row],
            candidate_manifest={"run_id": "cand-run-1", "rule_version": "candidate-rules-v2-20260722"},
            candidate_profile_digest="sha256:" + "0" * 64,
            created_at="2026-07-23T00:00:00+00:00",
            **_MINIMAL_ALLOCATION,
        )
        assert plan_without_counts.matrix_hash == plan_with_counts.matrix_hash

        def _matching(plan):
            return [
                c for c in plan.cells
                if c.clause_no == "5" and c.subclause_key == "bid_contract"
                and c.doc_type == "bid_notice" and c.agency_category == "central_ministry"
            ]

        without_cells = _matching(plan_without_counts)
        with_cells = _matching(plan_with_counts)
        assert without_cells and all(c.real_candidate_count == 0 for c in without_cells)
        assert with_cells and all(c.real_candidate_count == 1 for c in with_cells)


class TestGenerationPlanFromDictValidation:
    def test_round_trip(self):
        plan = _build_small_plan()
        round_tripped = GenerationPlan.from_dict(plan.to_dict(), source_path="test")
        assert round_tripped == plan

    def test_missing_required_field_raises(self):
        data = _build_small_plan().to_dict()
        del data["matrix_hash"]
        with pytest.raises(GenerationPlanValidationError):
            GenerationPlan.from_dict(data, source_path="test")

    def test_wrong_schema_version_raises(self):
        data = _build_small_plan().to_dict()
        data["schema_version"] = 999
        with pytest.raises(GenerationPlanValidationError):
            GenerationPlan.from_dict(data, source_path="test")

    def test_empty_cells_raises(self):
        data = _build_small_plan().to_dict()
        data["cells"] = []
        with pytest.raises(GenerationPlanValidationError):
            GenerationPlan.from_dict(data, source_path="test")

    def test_duplicate_coverage_cell_key_raises(self):
        data = _build_small_plan().to_dict()
        data["cells"].append(dict(data["cells"][0]))
        with pytest.raises(GenerationPlanValidationError):
            GenerationPlan.from_dict(data, source_path="test")


class TestWriteGenerationPlanAtomic:
    def test_publishes_json_csv_and_manifest(self, tmp_path):
        plan = _build_small_plan()
        run_dir = tmp_path / "run-1"
        artifact_manifest = write_generation_plan_atomic(run_dir, plan)

        assert (run_dir / "generation_plan.json").exists()
        assert (run_dir / "generation_plan.csv").exists()
        assert (run_dir / "_manifest.json").exists()
        assert not list(run_dir.glob(".*.tmp"))

        published = json.loads((run_dir / "generation_plan.json").read_text(encoding="utf-8"))
        assert published["run_id"] == plan.run_id
        assert artifact_manifest["files"]["generation_plan.json"]["row_count"] == len(plan.cells)

    def test_refuses_to_overwrite_completed_run(self, tmp_path):
        plan = _build_small_plan()
        run_dir = tmp_path / "run-1"
        write_generation_plan_atomic(run_dir, plan)
        with pytest.raises(RuntimeError):
            write_generation_plan_atomic(run_dir, plan)

    def test_write_failure_leaves_no_completion_manifest(self, tmp_path, monkeypatch):
        plan = _build_small_plan()
        run_dir = tmp_path / "run-1"

        import rd2.generators.generation_plan_schema as schema_module

        real_replace = schema_module.os.replace
        call_count = {"n": 0}

        def flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 2:  # generation_plan.csv 게시 단계에서 실패
                raise OSError("simulated disk failure")
            return real_replace(src, dst)

        monkeypatch.setattr(schema_module.os, "replace", flaky_replace)

        with pytest.raises(OSError):
            write_generation_plan_atomic(run_dir, plan)

        assert not (run_dir / "_manifest.json").exists()
