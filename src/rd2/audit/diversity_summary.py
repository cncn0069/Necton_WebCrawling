"""diversity_summary.json 조립 — 설계 문서 §9.

단일 "다양성 점수"를 만들지 않는다 — section별 status와 지표를 그대로 나열한다.
v1b는 semantic(§7.3)과 agency proxy(§7.5)를 다루지 않으므로 그 두 section은
항상 ``status="not_run"``이다(§14.7 phase 표).
"""

from __future__ import annotations

SCHEMA_VERSION = 1


def summarize_coverage(coverage_actual_rows: list[dict], *, max_seed_source_share: float | None) -> dict:
    status_counts = {"filled": 0, "shortage": 0, "overfill": 0, "not_applicable": 0}
    for row in coverage_actual_rows:
        status_counts[row["coverage_status"]] += 1
    return {
        "total_cells": len(coverage_actual_rows),
        "cell_status_counts": status_counts,
        "total_shortage": sum(row["shortage"] for row in coverage_actual_rows),
        "total_overfill": sum(row["overfill"] for row in coverage_actual_rows),
        "max_seed_source_share": max_seed_source_share,
    }


def summarize_exact_duplicates(duplicate_groups: list[dict]) -> dict:
    raw_groups = [g for g in duplicate_groups if g["metric"] == "raw_exact"]
    normalized_groups = [g for g in duplicate_groups if g["metric"] == "normalized_exact"]
    return {
        "raw_exact_group_count": len(raw_groups),
        "raw_exact_flagged_rows": sum(g["group_size"] for g in raw_groups),
        "normalized_exact_group_count": len(normalized_groups),
        "normalized_exact_flagged_rows": sum(g["group_size"] for g in normalized_groups),
    }


def build_diversity_summary(
    *,
    run: dict,
    coverage_metrics: dict,
    exact_metrics: dict,
    format_metrics: dict,
    review_count: int,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "run": run,
        "coverage": {"status": "ok", "metrics": coverage_metrics},
        "exact_duplicates": {"status": "ok", "metrics": exact_metrics},
        "semantic_similarity": {"status": "not_run", "metrics": {}},
        "format_repetition": {"status": "ok", "metrics": format_metrics},
        "agency_fit_proxy": {"status": "not_run", "metrics": {}},
        "review_selection": {"status": "ok", "count": review_count},
    }
