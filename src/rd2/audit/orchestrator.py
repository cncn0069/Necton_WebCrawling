"""1-pass 스트리밍 오케스트레이션 — 설계 문서 §14.9 + §14.5(atomic 게시).

generated CSV를 한 번만 순회하며 coverage/exact-dup/format 누적기에 각각
먹이고, sampler가 필요로 하는 bounded 요약(RowSummary — body 원문 없음)만
별도로 남긴다. 순회가 끝난 뒤 각 누적기의 finalize() 결과와 sampler 결과를
묶어 diversity_summary.json과 나머지 artifact를 atomic 게시한다.

주의: planner(v1a)가 같은 ``--output-dir``에 이미 ``_manifest.json``(자신의
completion marker)을 게시해뒀을 수 있다 — 이 모듈은 그 파일을 건드리지 않고
별도 ``_audit_manifest.json``을 자신의 completion marker로 쓴다(계획 문서
§9의 디렉터리 트리는 plan과 audit 산출물을 한 디렉터리에 같이 두지만, 단일
manifest 파일을 공유하면 v1a가 이미 완료 표시한 run을 v1b가 다시 여는 꼴이
되어 두 모듈의 atomic-publish 불변식이 충돌한다 — 이를 피하기 위한 의도적
조정이다).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from rd2.audit.content_normalize import normalized_content_hash
from rd2.audit.coverage_actual import COVERAGE_ACTUAL_FIELDNAMES, CoverageActualAccumulator
from rd2.audit.diversity_summary import build_diversity_summary, summarize_coverage, summarize_exact_duplicates
from rd2.audit.duplicates import DIVERSITY_PAIRS_FIELDNAMES, DUPLICATE_GROUPS_FIELDNAMES, ExactDuplicateAccumulator
from rd2.audit.format_concentration import FormatConcentrationAccumulator
from rd2.audit.review_sampler import (
    CONTENT_SAMPLES_FIELDNAMES,
    REVIEW_SAMPLES_FIELDNAMES,
    RowSummary,
    pdf_status,
    select_content_samples,
    select_review_samples,
)
from rd2.audit.row_contract import AuditContractError, RowError, parse_row, validate_header
from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.extraction.storage import compute_source_sha256

if TYPE_CHECKING:
    from rd2.generators.generation_plan_schema import GenerationPlan

ROW_ERRORS_FIELDNAMES = ["source_row_number", "row_id", "error_code", "field", "detail"]


def _row_error_to_dict(error: RowError) -> dict:
    return {
        "source_row_number": error.source_row_number,
        "row_id": error.row_id,
        "error_code": error.error_code,
        "field": error.field,
        "detail": error.detail,
    }


def determine_run_status(*, row_errors: list[RowError], coverage_actual_rows: list[dict], total_ok: int) -> str:
    """설계 문서 §12.5: complete | degraded | failed.

    ``failed``의 나머지 조건(계약 무결성 fail-fast, 필수 artifact 생성 실패)은
    예외로 먼저 걸리므로 여기 도달했다는 건 그 두 조건은 이미 통과했다는 뜻이다
    — 여기서는 "성공 0건" 조건만 판단한다.
    """
    if total_ok == 0:
        return "failed"
    total_shortage = sum(row["shortage"] for row in coverage_actual_rows)
    total_overfill = sum(row["overfill"] for row in coverage_actual_rows)
    if row_errors or total_shortage > 0 or total_overfill > 0:
        return "degraded"
    return "complete"


def _materialize_content_samples(
    *,
    input_csv: Path,
    selections: list,
    pdf_dir: Path | None,
) -> list[dict]:
    """선정된 소수 행만 두 번째 CSV pass에서 생성 내용과 함께 물질화한다."""
    selected_ids = {selection.row_id for selection in selections if selection.row_id}
    raw_by_row_id: dict[str, dict[str, str]] = {}
    if selected_ids:
        with Path(input_csv).open("r", encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                row_id = raw.get("row_id") or ""
                if row_id in selected_ids:
                    raw_by_row_id[row_id] = raw

    rows: list[dict] = []
    for selection in selections:
        raw = raw_by_row_id.get(selection.row_id, {})
        path, resolved_pdf_status = (
            pdf_status(selection.row_id, pdf_dir)
            if selection.row_id
            else ("", "pdf_unavailable")
        )
        rows.append(
            {
                "sample_rank": selection.sample_rank,
                "sample_axis": selection.sample_axis,
                "sample_value": selection.sample_value,
                "sample_status": selection.sample_status,
                "row_id": selection.row_id,
                "cso_classification": raw.get("cso_classification", ""),
                "clause_no": raw.get("clause_no", ""),
                "cso_subclause_key": raw.get("cso_subclause_key", ""),
                "doc_type": raw.get("doc_type", ""),
                "document_status": raw.get("document_status", ""),
                "title": raw.get("title", ""),
                "body_text": raw.get("body_text", ""),
                "coverage_cell_key": raw.get("coverage_cell_key", ""),
                "pdf_path": path,
                "pdf_status": resolved_pdf_status,
            }
        )
    return rows


def run_audit(
    *,
    plan: "GenerationPlan",
    input_csv: Path,
    pdf_dir: Path | None,
    sample_count: int,
    output_dir: Path,
    forced_review_reasons: Mapping[str, str] | None = None,
    classification_metrics: Mapping[str, Any] | None = None,
    audit_context_sha256: str | None = None,
    classification_expectations: Mapping[
        str,
        tuple[str, str, str],
    ]
    | None = None,
) -> dict[str, Any]:
    input_csv = Path(input_csv)
    if audit_context_sha256 is not None and (
        len(audit_context_sha256) != 64
        or any(char not in "0123456789abcdef" for char in audit_context_sha256)
    ):
        raise AuditContractError(
            "audit_context_sha256는 lowercase SHA-256 hex여야 합니다"
        )
    plan_cell_keys = frozenset(cell.coverage_cell_key for cell in plan.cells)

    coverage_acc = CoverageActualAccumulator()
    dup_acc = ExactDuplicateAccumulator()
    format_acc = FormatConcentrationAccumulator()
    row_summaries: dict[str, RowSummary] = {}
    row_errors: list[RowError] = []
    seen_row_ids: set[str] = set()
    seen_slots: set[str] = set()
    total_ok = 0
    expected_labels = dict(classification_expectations or {})

    with input_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_header(reader.fieldnames)

        for row_number, raw in enumerate(reader, start=1):
            row, error = parse_row(raw, row_number=row_number, plan_cell_keys=plan_cell_keys)
            if error is not None:
                row_errors.append(error)
                continue

            if row.row_id in seen_row_ids:
                raise AuditContractError(f"중복 row_id: {row.row_id!r} (source_row_number={row_number})")
            seen_row_ids.add(row.row_id)

            if row.is_ok:
                if expected_labels:
                    expected = expected_labels.get(row.row_id)
                    if expected is None:
                        raise AuditContractError(
                            "audit input row is missing from classification sidecar: "
                            f"{row.row_id!r}"
                        )
                    actual = (
                        row.classification,
                        row.clause_no,
                        row.subclause_key,
                    )
                    if actual != expected:
                        raise AuditContractError(
                            "audit target label does not match classification sidecar "
                            f"generation target: row_id={row.row_id!r}"
                        )
                if row.coverage_slot:
                    slot_key = f"{row.coverage_cell_key}|{row.coverage_slot}"
                    if slot_key in seen_slots:
                        raise AuditContractError(
                            f"중복 coverage slot: {slot_key!r} (source_row_number={row_number})"
                        )
                    seen_slots.add(slot_key)
                if row.coverage_plan_run_id and row.coverage_plan_run_id != plan.run_id:
                    raise AuditContractError(
                        f"plan run_id 불일치: row={row.coverage_plan_run_id!r}, "
                        f"plan={plan.run_id!r} (source_row_number={row_number})"
                    )

            coverage_acc.add_row(row)

            if row.is_ok:
                total_ok += 1
                dup_acc.add_row(row)
                format_acc.add_row(row)
                row_summaries[row.row_id] = RowSummary(
                    row_id=row.row_id,
                    coverage_cell_key=row.coverage_cell_key,
                    seed_candidate_id=row.seed_candidate_id,
                    seed_extraction_id=row.seed_extraction_id,
                    body_length=len(row.body_text),
                    normalized_hash=normalized_content_hash(
                        row.title, row.body_text, ordering_agency=row.ordering_agency
                    ),
                    structure_fingerprint=format_acc.fingerprint_of(row),
                    clause_no=row.clause_no,
                    document_status=row.document_status,
                )

    coverage_actual_rows = coverage_acc.finalize(plan)
    if expected_labels and set(row_summaries) != set(expected_labels):
        missing = sorted(set(expected_labels) - set(row_summaries))
        raise AuditContractError(
            "classification sidecar rows do not match valid audit input rows: "
            f"missing={missing}"
        )
    forced_reasons = dict(forced_review_reasons or {})
    unknown_forced_rows = sorted(set(forced_reasons) - set(row_summaries))
    if unknown_forced_rows:
        raise AuditContractError(
            "classification sidecar가 audit input에 없는 row_id를 참조합니다: "
            f"{unknown_forced_rows}"
        )
    input_csv_sha256 = compute_source_sha256(input_csv)
    audit_identity = {
        "plan_run_id": plan.run_id,
        "input_csv_sha256": input_csv_sha256,
        "sample_count": sample_count,
    }
    if audit_context_sha256 is not None:
        audit_identity["audit_context_sha256"] = audit_context_sha256
    audit_run_id = "sha256:" + canonical_sha256(
        audit_identity,
        normalization_version=NORMALIZATION_VERSION,
    )

    duplicate_groups, diversity_pairs = dup_acc.finalize(audit_run_id=audit_run_id)
    format_result = format_acc.finalize()

    review_samples = select_review_samples(
        sample_count=sample_count,
        row_summaries=row_summaries,
        duplicate_groups=duplicate_groups,
        coverage_actual_rows=coverage_actual_rows,
        format_result=format_result,
        pdf_dir=pdf_dir,
        forced_review_reasons=forced_reasons,
    )
    content_sample_selections = select_content_samples(row_summaries)
    content_samples = _materialize_content_samples(
        input_csv=input_csv,
        selections=content_sample_selections,
        pdf_dir=pdf_dir,
    )

    status = determine_run_status(
        row_errors=row_errors, coverage_actual_rows=coverage_actual_rows, total_ok=total_ok
    )

    run_info = {
        "schema_version": 1,
        "audit_run_id": audit_run_id,
        "plan_run_id": plan.run_id,
        "input_csv_sha256": input_csv_sha256,
        "status": status,
        "row_count_ok": total_ok,
        "row_error_count": len(row_errors),
        "sample_count_requested": sample_count,
    }
    if audit_context_sha256 is not None:
        run_info["audit_context_sha256"] = audit_context_sha256
    summary = build_diversity_summary(
        run=run_info,
        coverage_metrics=summarize_coverage(
            coverage_actual_rows, max_seed_source_share=coverage_acc.max_seed_source_share()
        ),
        exact_metrics=summarize_exact_duplicates(duplicate_groups),
        format_metrics=format_result,
        review_count=len(review_samples),
        classification_metrics=(
            dict(classification_metrics)
            if classification_metrics is not None
            else None
        ),
    )

    _publish_audit_artifacts(
        output_dir=Path(output_dir),
        audit_run_id=audit_run_id,
        summary=summary,
        coverage_actual_rows=coverage_actual_rows,
        duplicate_groups=duplicate_groups,
        diversity_pairs=diversity_pairs,
        review_samples=review_samples,
        content_samples=content_samples,
        row_errors=row_errors,
    )

    return summary


def _flush_and_sync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _write_csv_temp(temp_path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        _flush_and_sync(handle)


def _publish_audit_artifacts(
    *,
    output_dir: Path,
    audit_run_id: str,
    summary: dict,
    coverage_actual_rows: list[dict],
    duplicate_groups: list[dict],
    diversity_pairs: list[dict],
    review_samples: list,
    content_samples: list[dict],
    row_errors: list[RowError],
) -> dict:
    audit_manifest_path = output_dir / "_audit_manifest.json"
    if audit_manifest_path.exists():
        raise RuntimeError(
            f"{output_dir}는 이미 완료된 audit 산출물을 가지고 있습니다 — 새 --output-dir을 쓰세요"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id_token = audit_run_id.replace(":", "-")
    review_sample_dicts = [
        {
            "sample_rank": s.sample_rank,
            "row_id": s.row_id,
            "sample_kind": s.sample_kind,
            "reason_code": s.reason_code,
            "secondary_reasons": s.secondary_reasons,
            "reason_detail": s.reason_detail,
            "coverage_cell_key": s.coverage_cell_key,
            "metric_name": s.metric_name,
            "metric_value": "" if s.metric_value is None else s.metric_value,
            "related_row_id": s.related_row_id,
            "seed_candidate_id": s.seed_candidate_id,
            "seed_extraction_id": s.seed_extraction_id,
            "pdf_path": s.pdf_path,
        }
        for s in review_samples
    ]
    row_error_dicts = [_row_error_to_dict(e) for e in row_errors]

    artifacts: list[tuple[str, list[str], list[dict]]] = [
        ("coverage_actual.csv", COVERAGE_ACTUAL_FIELDNAMES, coverage_actual_rows),
        ("duplicate_groups.csv", DUPLICATE_GROUPS_FIELDNAMES, duplicate_groups),
        ("diversity_pairs.csv", DIVERSITY_PAIRS_FIELDNAMES, diversity_pairs),
        ("review_samples.csv", REVIEW_SAMPLES_FIELDNAMES, review_sample_dicts),
        ("content_samples.csv", CONTENT_SAMPLES_FIELDNAMES, content_samples),
        ("row_errors.csv", ROW_ERRORS_FIELDNAMES, row_error_dicts),
    ]

    final_paths: dict[str, Path] = {}
    for filename, fieldnames, rows in artifacts:
        final_path = output_dir / filename
        temp_path = output_dir / f".{filename}.{run_id_token}.tmp"
        _write_csv_temp(temp_path, fieldnames, rows)
        final_paths[filename] = (final_path, temp_path)

    summary_final = output_dir / "diversity_summary.json"
    summary_temp = output_dir / f".diversity_summary.json.{run_id_token}.tmp"
    with summary_temp.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _flush_and_sync(handle)

    # 게시: CSV들을 먼저, summary는 그다음, completion marker는 마지막(§14.5).
    for filename, (final_path, temp_path) in final_paths.items():
        os.replace(temp_path, final_path)
    os.replace(summary_temp, summary_final)

    files_manifest = {}
    for filename in [name for name, _, _ in artifacts] + ["diversity_summary.json"]:
        path = output_dir / filename
        files_manifest[filename] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    audit_manifest = {
        "schema_version": 1,
        "artifact": "rd2-diversity-audit",
        "audit_run_id": audit_run_id,
        "files": files_manifest,
    }
    audit_manifest_temp = output_dir / f"._audit_manifest.json.{run_id_token}.tmp"
    with audit_manifest_temp.open("w", encoding="utf-8") as handle:
        json.dump(audit_manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _flush_and_sync(handle)
    os.replace(audit_manifest_temp, audit_manifest_path)
    return audit_manifest
