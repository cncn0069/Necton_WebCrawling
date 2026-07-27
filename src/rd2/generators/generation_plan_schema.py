"""GenerationPlan JSON/CSV 계약과 v1a 최상위 오케스트레이션.

``coverage_plan.py``(순수 배분 로직)와 ``candidate_profile.py``(DB 프로파일링)
결과를 묶어 설계 문서 §4, §4.1, §14.5의 typed JSON 계약과 atomic 게시를
구현한다. plan JSON만 canonical 입력이고 CSV는 사람이 읽는 파생 view다(§13).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from rd2.canonical import NORMALIZATION_VERSION, compute_matrix_hash, compute_plan_run_id
from rd2.generators.coverage_plan import (
    AGENCY_CATEGORIES,
    CellRecord,
    CoverageCell,
    GenerationPlanValidationError,
    allocate_grade,
    compute_candidate_profile_counts,
    coverage_cell_key,
    enumerate_valid_cells,
)

SCHEMA_VERSION = 1

CSV_FIELDNAMES = [
    "coverage_cell_key",
    "classification",
    "clause_no",
    "subclause_key",
    "doc_type",
    "agency_category",
    "admin_status",
    "cell_state",
    "requested_target",
    "real_candidate_count",
    "planned_span_seeded",
    "planned_fallback",
    "candidate_shortage",
    "evidence_supported",
    "fallback_ratio",
    "allocation_weight",
    "allocation_reason",
]


@dataclass(frozen=True)
class GenerationPlan:
    schema_version: int
    run_id: str
    created_at: str
    matrix_hash: str
    candidate_manifest: dict[str, Any]
    candidate_profile_digest: str
    normalization_version: str
    allocation: dict[str, Any]
    cells: tuple[CoverageCell, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source_path: str) -> "GenerationPlan":
        def fail(msg: str) -> None:
            raise GenerationPlanValidationError(f"{source_path}: {msg}")

        if not isinstance(data, Mapping):
            fail("plan은 JSON object여야 합니다")
        if data.get("schema_version") != SCHEMA_VERSION:
            fail(f"지원하지 않는 schema_version: {data.get('schema_version')!r}")
        for field in (
            "run_id",
            "created_at",
            "matrix_hash",
            "candidate_profile_digest",
            "normalization_version",
        ):
            value = data.get(field)
            if not isinstance(value, str) or not value:
                fail(f"필수 필드 {field!r}가 없거나 빈 문자열입니다")

        candidate_manifest = data.get("candidate_manifest")
        if not isinstance(candidate_manifest, dict):
            fail("candidate_manifest는 object여야 합니다")

        allocation = data.get("allocation")
        if not isinstance(allocation, dict):
            fail("allocation은 object여야 합니다")

        raw_cells = data.get("cells")
        if not isinstance(raw_cells, list) or not raw_cells:
            fail("cells는 비어있지 않은 배열이어야 합니다")

        cells: list[CoverageCell] = []
        seen_keys: set[str] = set()
        for index, raw_cell in enumerate(raw_cells):
            cell = CoverageCell.from_dict(raw_cell, context=f"{source_path}#cells[{index}]")
            if cell.coverage_cell_key in seen_keys:
                fail(f"coverage_cell_key 중복: {cell.coverage_cell_key!r}")
            seen_keys.add(cell.coverage_cell_key)
            cells.append(cell)

        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            created_at=data["created_at"],
            matrix_hash=data["matrix_hash"],
            candidate_manifest=candidate_manifest,
            candidate_profile_digest=data["candidate_profile_digest"],
            normalization_version=data["normalization_version"],
            allocation=allocation,
            cells=tuple(cells),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "matrix_hash": self.matrix_hash,
            "candidate_manifest": self.candidate_manifest,
            "candidate_profile_digest": self.candidate_profile_digest,
            "normalization_version": self.normalization_version,
            "allocation": self.allocation,
            "cells": [cell.to_dict() for cell in self.cells],
        }


def generation_plan_csv_rows(plan: GenerationPlan) -> list[dict[str, Any]]:
    rows = []
    for cell in plan.cells:
        row = cell.to_dict()
        row["evidence_supported"] = cell.evidence_supported
        row["fallback_ratio"] = "" if cell.fallback_ratio is None else f"{cell.fallback_ratio:.6f}"
        rows.append({field: row[field] for field in CSV_FIELDNAMES})
    return rows


def build_generation_plan(
    *,
    c_target: int,
    s_target: int,
    minimum_per_valid_cell: int,
    agency_weights: Mapping[str, float],
    allocation_seed: int,
    max_rows_per_candidate: int,
    candidate_profile_rows: Sequence[Any],
    candidate_manifest: Mapping[str, Any],
    candidate_profile_digest: str,
    created_at: str,
    agency_categories: Sequence[str] = AGENCY_CATEGORIES,
) -> GenerationPlan:
    """설계 문서 "Target User & Narrowest Wedge" 절의 plan-only 진입점.

    LLM 호출도, RDS 반영도 하지 않는다 — candidate profile row와 설정만으로
    결정적인 GenerationPlan을 만든다.
    """
    counts = compute_candidate_profile_counts(candidate_profile_rows)
    records = enumerate_valid_cells(candidate_profile_counts=counts, agency_categories=agency_categories)

    records_by_grade: dict[str, list[CellRecord]] = {}
    for record in records:
        records_by_grade.setdefault(record.key.classification, []).append(record)

    grade_targets = {"C": c_target, "S": s_target}
    all_cells: list[CoverageCell] = []
    for grade in sorted(grade_targets):
        all_cells.extend(
            allocate_grade(
                grade,
                records_by_grade.get(grade, []),
                grade_target=grade_targets[grade],
                minimum_per_valid_cell=minimum_per_valid_cell,
                agency_weights=agency_weights,
                max_rows_per_candidate=max_rows_per_candidate,
            )
        )
    all_cells.sort(key=lambda cell: cell.coverage_cell_key)

    sorted_agency_weights = dict(sorted(agency_weights.items()))
    config = {
        "targets": {"C": c_target, "S": s_target},
        "minimum_per_valid_cell": minimum_per_valid_cell,
        "agency_weights": sorted_agency_weights,
        "max_rows_per_candidate": max_rows_per_candidate,
    }
    valid_cell_keys = sorted(
        {coverage_cell_key(record.key) for record in records if record.cell_state != "excluded"}
    )
    matrix_hash = compute_matrix_hash(
        valid_cell_keys=valid_cell_keys,
        agency_categories=sorted(agency_categories),
        config=config,
        normalization_version=NORMALIZATION_VERSION,
    )

    allocation = {
        "seed": allocation_seed,
        "c_target": c_target,
        "s_target": s_target,
        "minimum_per_valid_cell": minimum_per_valid_cell,
        "agency_weights": sorted_agency_weights,
        "max_rows_per_candidate": max_rows_per_candidate,
    }
    candidate_manifest_dict = dict(candidate_manifest)

    plan_without_run_id_and_created_at = {
        "schema_version": SCHEMA_VERSION,
        "matrix_hash": matrix_hash,
        "candidate_manifest": candidate_manifest_dict,
        "candidate_profile_digest": candidate_profile_digest,
        "normalization_version": NORMALIZATION_VERSION,
        "allocation": allocation,
        "cells": [cell.to_dict() for cell in all_cells],
    }
    run_id = compute_plan_run_id(
        plan_without_run_id_and_created_at, normalization_version=NORMALIZATION_VERSION
    )

    return GenerationPlan(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        created_at=created_at,
        matrix_hash=matrix_hash,
        candidate_manifest=candidate_manifest_dict,
        candidate_profile_digest=candidate_profile_digest,
        normalization_version=NORMALIZATION_VERSION,
        allocation=allocation,
        cells=tuple(all_cells),
    )


def _flush_and_sync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def write_generation_plan_atomic(output_dir: Path, plan: GenerationPlan) -> dict[str, Any]:
    """generation_plan.json/.csv를 검증 후 atomic 게시하고 completion manifest를 남긴다.

    설계 문서 §14.5: 개별 artifact는 temp → ``os.replace``로 먼저 게시하고,
    ``_manifest.json``을 마지막에 completion marker로 게시한다. 이미 완료된
    run 디렉터리(``_manifest.json`` 존재)는 거부한다 — 새 run ID 디렉터리에만
    게시한다.
    """
    output_dir = Path(output_dir)
    manifest_path = output_dir / "_manifest.json"
    if manifest_path.exists():
        raise RuntimeError(
            f"{output_dir}는 이미 완료된 run 산출물을 가지고 있습니다 — 새 --output-dir을 쓰세요"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    # run_id는 "sha256:..." 형태라 ":"가 Windows 파일명에 쓸 수 없다 — temp 파일명에는
    # 파일시스템 안전 토큰만 쓴다(게시되는 JSON/CSV 내용의 run_id 값 자체는 그대로 둔다).
    run_id_token = plan.run_id.replace(":", "-")
    json_path = output_dir / "generation_plan.json"
    csv_path = output_dir / "generation_plan.csv"
    temp_json_path = output_dir / f".generation_plan.json.{run_id_token}.tmp"
    temp_csv_path = output_dir / f".generation_plan.csv.{run_id_token}.tmp"

    plan_dict = plan.to_dict()
    with temp_json_path.open("w", encoding="utf-8") as handle:
        json.dump(plan_dict, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _flush_and_sync(handle)

    csv_rows = generation_plan_csv_rows(plan)
    with temp_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(csv_rows)
        _flush_and_sync(handle)

    # 게시 전 재검증: 다시 파싱한 결과가 원본 계약을 그대로 만족하는지 확인.
    reparsed = json.loads(temp_json_path.read_text(encoding="utf-8"))
    GenerationPlan.from_dict(reparsed, source_path=str(temp_json_path))
    with temp_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reread_row_count = sum(1 for _ in csv.DictReader(handle))
    if reread_row_count != len(plan.cells):
        raise RuntimeError(
            f"generation_plan.csv 행 수 불일치: {reread_row_count} != {len(plan.cells)}"
        )

    os.replace(temp_json_path, json_path)
    os.replace(temp_csv_path, csv_path)

    artifact_manifest = {
        "schema_version": 1,
        "artifact": "rd2-generation-plan",
        "run_id": plan.run_id,
        "created_at": plan.created_at,
        "files": {
            "generation_plan.json": {
                "sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
                "row_count": len(plan.cells),
            },
            "generation_plan.csv": {
                "sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                "row_count": len(plan.cells),
            },
        },
    }
    temp_manifest_path = output_dir / f"._manifest.json.{run_id_token}.tmp"
    with temp_manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(artifact_manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _flush_and_sync(handle)
    os.replace(temp_manifest_path, manifest_path)
    return artifact_manifest
