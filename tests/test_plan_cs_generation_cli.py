"""scripts/plan_cs_generation.py end-to-end 테스트.

설계 문서 "Target User & Narrowest Wedge": LLM 호출이나 RDS 반영 없이
generation_plan만 만든다. 작은 fixture candidate manifest로 CLI 전체 흐름
(로드 → 프로파일링 → 배분 → atomic 게시)을 검증한다.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import plan_cs_generation as plan_cli  # noqa: E402


class _FakeConn:
    """빈 documents 테이블 — 모든 후보가 unresolved로 남아도 plan 자체는 완성돼야 한다."""

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._result = []

    def fetchall(self):
        return []

    def close(self):
        pass


def _write_candidate_fixture(candidates_dir: Path) -> None:
    candidates_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 2,
        "artifact": "rd2-candidates",
        "run_id": "cand-run-1",
        "extraction_run_id": "ext-run-1",
        "rule_version": "candidate-rules-v2-20260722",
        "created_at": "2026-07-23T00:00:00+00:00",
        "status": "complete",
        "counts": {"5": 1, "6": 0, "7": 0, "8": 0},
        "failures": [],
    }
    (candidates_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    candidate_record = {
        "candidate_id": "c1",
        "extraction_id": "e1",
        "source_path": "alio/bid_notice/bucket/1_file.json.gz",
        "source": "alio",
        "doc_type": "bid_notice",
        "doc_id": "1",
        "line_ids": [1],
        "text": "입찰 공고 관련 내용",
        "text_sha256": "0" * 64,
        "page": 1,
        "candidate_kind": "span",
        "run_id": "cand-run-1",
        "rule_version": "candidate-rules-v2-20260722",
    }
    (candidates_dir / "clause_5.jsonl").write_text(
        json.dumps(candidate_record, ensure_ascii=False) + "\n", encoding="utf-8"
    )


class TestPlanCsGenerationCli:
    def test_end_to_end_publishes_plan(self, tmp_path, monkeypatch):
        candidates_dir = tmp_path / "candidates"
        output_dir = tmp_path / "audit" / "run-1"
        _write_candidate_fixture(candidates_dir)
        monkeypatch.setattr(plan_cli, "connect_mariadb", lambda: _FakeConn())

        exit_code = plan_cli.main(
            [
                "--c-target", "500",
                "--s-target", "650",
                "--minimum-per-valid-cell", "1",
                "--output-dir", str(output_dir),
                "--candidates-dir", str(candidates_dir),
            ]
        )
        assert exit_code == 0

        plan_json = json.loads((output_dir / "generation_plan.json").read_text(encoding="utf-8"))
        c_sum = sum(c["requested_target"] for c in plan_json["cells"] if c["classification"] == "C")
        s_sum = sum(c["requested_target"] for c in plan_json["cells"] if c["classification"] == "S")
        assert c_sum == 500
        assert s_sum == 650
        assert (output_dir / "generation_plan.csv").exists()
        assert (output_dir / "_manifest.json").exists()
        assert (candidates_dir / "candidate_profiles.jsonl").exists()
        assert (candidates_dir / "_profile_manifest.json").exists()

    def test_rejects_incomplete_candidate_manifest(self, tmp_path, monkeypatch):
        candidates_dir = tmp_path / "candidates"
        _write_candidate_fixture(candidates_dir)
        manifest_path = candidates_dir / "_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "partial"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(plan_cli, "connect_mariadb", lambda: _FakeConn())

        with pytest.raises(RuntimeError):
            plan_cli.main(
                [
                    "--c-target", "500",
                    "--s-target", "650",
                    "--minimum-per-valid-cell", "1",
                    "--output-dir", str(tmp_path / "audit" / "run-1"),
                    "--candidates-dir", str(candidates_dir),
                ]
            )

    def test_agency_weights_validation_rejects_unknown_category(self, tmp_path):
        with pytest.raises(ValueError):
            plan_cli.parse_agency_weights(json.dumps({"not_a_real_category": 1.0}))
