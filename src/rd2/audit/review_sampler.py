"""reason-coded 검수 샘플 선정 — 설계 문서 §8 + §14.2.

v1b는 semantic(§7.3)과 agency proxy(§7.5)가 ``not_run``이므로 그 두 reason은
항상 건너뛰고 나머지 reason과 representative quota로 rollover한다(§14.2
마지막 문단). embedding이 없어 대표 샘플 선정은 항상 "body length median에
가장 가까운 row"(§14.2 4단계) 경로만 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from rd2.administrative_status import AdminStatus

REVIEW_SAMPLES_FIELDNAMES = [
    "sample_rank",
    "row_id",
    "sample_kind",
    "reason_code",
    "secondary_reasons",
    "reason_detail",
    "coverage_cell_key",
    "metric_name",
    "metric_value",
    "related_row_id",
    "seed_candidate_id",
    "seed_extraction_id",
    "pdf_path",
]

CONTENT_SAMPLES_FIELDNAMES = [
    "sample_rank",
    "sample_axis",
    "sample_value",
    "sample_status",
    "row_id",
    "cso_classification",
    "clause_no",
    "cso_subclause_key",
    "doc_type",
    "document_status",
    "title",
    "body_text",
    "coverage_cell_key",
    "pdf_path",
    "pdf_status",
]

# 우선순위 순서(§8): raw exact -> normalized -> semantic(not_run) -> coverage
# shortage -> format concentration -> shared source -> agency proxy(not_run).
ANOMALY_REASON_ORDER = (
    "raw_exact_duplicate",
    "normalized_duplicate",
    "semantic_neighbor",
    "coverage_shortage",
    "format_concentration",
    "shared_source",
    "agency_proxy_mismatch",
)
NOT_RUN_REASONS = frozenset({"semantic_neighbor", "agency_proxy_mismatch"})


@dataclass(frozen=True)
class RowSummary:
    """감사 1-pass 스트리밍 중 sampler를 위해 남겨두는, body 원문 없는 요약(§14.9)."""

    row_id: str
    coverage_cell_key: str
    seed_candidate_id: str
    seed_extraction_id: str
    body_length: int
    normalized_hash: str
    structure_fingerprint: str
    clause_no: str = ""
    document_status: str = ""


@dataclass(frozen=True)
class ContentSampleSelection:
    sample_rank: int
    sample_axis: str  # "clause" | "admin_status"
    sample_value: str
    sample_status: str  # "ok" | "missing"
    row_id: str


def select_content_samples(
    row_summaries: Mapping[str, RowSummary],
) -> list[ContentSampleSelection]:
    """조항 1~8과 행정상태별로 생성 내용 대표 1건을 결정적으로 고른다.

    그룹 안에서는 본문 길이 중앙값에 가장 가까운 행을 택한다. 생성 결과가 없는
    그룹도 ``missing`` 행으로 남겨 검수 파일만 보고 누락을 확인할 수 있게 한다.
    한 문서가 조항 대표와 행정상태 대표를 겸할 수 있지만 두 검수 목적은 별도
    행으로 보존한다.
    """

    def choose(members: list[RowSummary]) -> str:
        lengths = sorted(member.body_length for member in members)
        median_length = lengths[len(lengths) // 2]
        return min(
            members,
            key=lambda member: (
                abs(member.body_length - median_length),
                member.normalized_hash,
                member.row_id,
            ),
        ).row_id

    groups: list[tuple[str, str, list[RowSummary]]] = []
    summaries = list(row_summaries.values())
    for clause_no in tuple(str(number) for number in range(1, 9)):
        groups.append(
            (
                "clause",
                clause_no,
                [summary for summary in summaries if summary.clause_no == clause_no],
            )
        )
    for status in AdminStatus:
        groups.append(
            (
                "admin_status",
                status.value,
                [
                    summary
                    for summary in summaries
                    if summary.document_status == status.value
                ],
            )
        )

    return [
        ContentSampleSelection(
            sample_rank=rank,
            sample_axis=axis,
            sample_value=value,
            sample_status="ok" if members else "missing",
            row_id=choose(members) if members else "",
        )
        for rank, (axis, value, members) in enumerate(groups, start=1)
    ]


@dataclass(frozen=True)
class ReviewSample:
    sample_rank: int
    row_id: str
    sample_kind: str  # "anomaly" | "representative"
    reason_code: str
    secondary_reasons: str
    reason_detail: str
    coverage_cell_key: str
    metric_name: str
    metric_value: float | None
    related_row_id: str
    seed_candidate_id: str
    seed_extraction_id: str
    pdf_path: str


def _raw_exact_candidates(duplicate_groups: list[dict]):
    groups = [g for g in duplicate_groups if g["metric"] == "raw_exact"]
    groups.sort(key=lambda g: (-g["group_size"], g["representative_row_id"]))
    return [
        (g["representative_row_id"], float(g["group_size"]), f"raw exact duplicate group_size={g['group_size']}")
        for g in groups
    ]


def _normalized_candidates(duplicate_groups: list[dict]):
    groups = [g for g in duplicate_groups if g["metric"] == "normalized_exact"]
    groups.sort(key=lambda g: (-g["group_size"], g["representative_row_id"]))
    return [
        (g["representative_row_id"], float(g["group_size"]), f"normalized duplicate candidate group_size={g['group_size']}")
        for g in groups
    ]


def _coverage_shortage_candidates(coverage_actual_rows: list[dict], row_by_cell: dict):
    scored = []
    for cell_row in coverage_actual_rows:
        if cell_row["coverage_status"] != "shortage":
            continue
        planned = cell_row["planned_target"]
        if planned <= 0:
            continue
        cell_key = cell_row["coverage_cell_key"]
        members = row_by_cell.get(cell_key, [])
        if not members:
            continue
        shortage_ratio = cell_row["shortage"] / planned
        chosen = min(members, key=lambda m: m.row_id)
        scored.append((chosen.row_id, shortage_ratio, planned, cell_key))
    scored.sort(key=lambda c: (-c[1], -c[2], c[3]))
    return [
        (row_id, ratio, f"coverage shortage cell={cell_key} ratio={ratio:.3f}")
        for row_id, ratio, _planned, cell_key in scored
    ]


def _format_concentration_candidates(format_result: dict, row_by_fingerprint: dict):
    cluster = format_result.get("largest_fingerprint_cluster")
    if not cluster:
        return []
    fingerprint = cluster["fingerprint"]
    members = row_by_fingerprint.get(fingerprint, [])
    if not members:
        return []
    lengths = sorted(m.body_length for m in members)
    median_length = lengths[len(lengths) // 2]
    chosen = min(members, key=lambda m: (abs(m.body_length - median_length), m.normalized_hash, m.row_id))
    detail = f"format cluster size={cluster['count']} fingerprint={fingerprint}"
    return [(chosen.row_id, float(cluster["count"]), detail)]


def _shared_source_candidates(row_summaries: dict):
    counts: dict[str, list[str]] = {}
    for summary in row_summaries.values():
        if summary.seed_candidate_id:
            counts.setdefault(summary.seed_candidate_id, []).append(summary.row_id)
    scored = []
    for candidate_id, row_ids in counts.items():
        if len(row_ids) < 2:
            continue
        row_ids_sorted = sorted(row_ids)
        scored.append((row_ids_sorted[0], len(row_ids), candidate_id))
    scored.sort(key=lambda c: (-c[1], c[0]))
    return [
        (row_id, float(count), f"shared seed source={candidate_id} count={count}")
        for row_id, count, candidate_id in scored
    ]


def select_anomaly_samples(
    *,
    quota: int,
    row_summaries: dict[str, RowSummary],
    duplicate_groups: list[dict],
    coverage_actual_rows: list[dict],
    format_result: dict,
) -> list[ReviewSample]:
    row_by_cell: dict[str, list[RowSummary]] = {}
    row_by_fingerprint: dict[str, list[RowSummary]] = {}
    for summary in row_summaries.values():
        row_by_cell.setdefault(summary.coverage_cell_key, []).append(summary)
        row_by_fingerprint.setdefault(summary.structure_fingerprint, []).append(summary)

    reason_candidates = {
        "raw_exact_duplicate": _raw_exact_candidates(duplicate_groups),
        "normalized_duplicate": _normalized_candidates(duplicate_groups),
        "coverage_shortage": _coverage_shortage_candidates(coverage_actual_rows, row_by_cell),
        "format_concentration": _format_concentration_candidates(format_result, row_by_fingerprint),
        "shared_source": _shared_source_candidates(row_summaries),
    }

    selected: list[ReviewSample] = []
    reason_by_row: dict[str, str] = {}
    for reason in ANOMALY_REASON_ORDER:
        if len(selected) >= quota:
            break
        if reason in NOT_RUN_REASONS:
            continue
        for row_id, metric_value, detail in reason_candidates.get(reason, []):
            if len(selected) >= quota:
                break
            if row_id in reason_by_row:
                continue
            summary = row_summaries.get(row_id)
            if summary is None:
                continue
            selected.append(
                ReviewSample(
                    sample_rank=0,
                    row_id=row_id,
                    sample_kind="anomaly",
                    reason_code=reason,
                    secondary_reasons="",
                    reason_detail=detail,
                    coverage_cell_key=summary.coverage_cell_key,
                    metric_name=reason,
                    metric_value=metric_value,
                    related_row_id="",
                    seed_candidate_id=summary.seed_candidate_id,
                    seed_extraction_id=summary.seed_extraction_id,
                    pdf_path="",
                )
            )
            reason_by_row[row_id] = reason

    # secondary reasons: 이미 선택된 row가 다른 reason의 상위 후보이기도 했다면 보존한다(§8).
    secondary_map: dict[str, list[str]] = {}
    for reason in ANOMALY_REASON_ORDER:
        if reason in NOT_RUN_REASONS:
            continue
        for row_id, _metric_value, _detail in reason_candidates.get(reason, []):
            primary = reason_by_row.get(row_id)
            if primary is not None and reason != primary:
                secondary_map.setdefault(row_id, [])
                if reason not in secondary_map[row_id]:
                    secondary_map[row_id].append(reason)

    return [
        replace(sample, secondary_reasons=",".join(secondary_map.get(sample.row_id, [])))
        for sample in selected
    ]


def select_representative_samples(
    *,
    quota: int,
    row_summaries: dict[str, RowSummary],
    coverage_actual_rows: list[dict],
    chosen_row_ids: set[str],
) -> list[ReviewSample]:
    if quota <= 0:
        return []

    def shortage_ratio(cell_row: dict) -> float:
        return (cell_row["shortage"] / cell_row["planned_target"]) if cell_row["planned_target"] > 0 else 0.0

    eligible_cells = [row for row in coverage_actual_rows if row["actual_ok"] > 0]
    eligible_cells.sort(key=lambda r: (-r["planned_target"], -shortage_ratio(r), r["coverage_cell_key"]))
    if not eligible_cells:
        return []

    row_by_cell: dict[str, list[RowSummary]] = {}
    for summary in row_summaries.values():
        row_by_cell.setdefault(summary.coverage_cell_key, []).append(summary)

    selected: list[ReviewSample] = []
    cell_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    chosen = set(chosen_row_ids)

    def run_pass(cell_cap: int, source_cap: int) -> None:
        for _round in range(2):  # §14.2: cell당 1건, cell 수가 R보다 적으면 2회차
            for cell_row in eligible_cells:
                if len(selected) >= quota:
                    return
                cell_key = cell_row["coverage_cell_key"]
                if cell_counts.get(cell_key, 0) >= cell_cap:
                    continue
                members = [m for m in row_by_cell.get(cell_key, []) if m.row_id not in chosen]
                if not members:
                    continue
                lengths = sorted(m.body_length for m in members)
                median_length = lengths[len(lengths) // 2]
                candidates = sorted(
                    members,
                    key=lambda m: (abs(m.body_length - median_length), m.normalized_hash, m.row_id),
                )
                picked = None
                for candidate in candidates:
                    if candidate.seed_candidate_id and source_counts.get(candidate.seed_candidate_id, 0) >= source_cap:
                        continue
                    picked = candidate
                    break
                if picked is None:
                    continue
                selected.append(
                    ReviewSample(
                        sample_rank=0,
                        row_id=picked.row_id,
                        sample_kind="representative",
                        reason_code="representative_cell",
                        secondary_reasons="",
                        reason_detail=f"cell={cell_key} body-length-median centroid",
                        coverage_cell_key=cell_key,
                        metric_name="body_length_distance",
                        metric_value=float(abs(picked.body_length - median_length)),
                        related_row_id="",
                        seed_candidate_id=picked.seed_candidate_id,
                        seed_extraction_id=picked.seed_extraction_id,
                        pdf_path="",
                    )
                )
                chosen.add(picked.row_id)
                cell_counts[cell_key] = cell_counts.get(cell_key, 0) + 1
                if picked.seed_candidate_id:
                    source_counts[picked.seed_candidate_id] = source_counts.get(picked.seed_candidate_id, 0) + 1

    cell_cap, source_cap = 2, 2
    run_pass(cell_cap, source_cap)
    extra = 0
    while len(selected) < quota and extra < 5:
        extra += 1
        run_pass(cell_cap + extra, source_cap + extra)

    return selected[:quota]


def pdf_status(row_id: str, pdf_dir: Path | None) -> tuple[str, str]:
    """(pdf_path, status) — status는 "ok" 또는 "pdf_unavailable".

    §5 이전이라 CSV에 실제 PDF 파일명 컬럼이 없다 — ``{pdf_dir}/{row_id}.pdf``
    관례로 best-effort 조회한다(실제 render_document_pdf() 파일명 규칙과 다를 수
    있음, 계획 문서의 알려진 제약).
    """
    if pdf_dir is None:
        return "", "pdf_unavailable"
    path = Path(pdf_dir) / f"{row_id}.pdf"
    if not path.exists():
        return str(path), "pdf_unavailable"
    try:
        import fitz  # pymupdf

        doc = fitz.open(path)
        try:
            if doc.page_count <= 0:
                return str(path), "pdf_unavailable"
        finally:
            doc.close()
    except Exception:
        return str(path), "pdf_unavailable"
    return str(path), "ok"


def select_review_samples(
    *,
    sample_count: int,
    row_summaries: dict[str, RowSummary],
    duplicate_groups: list[dict],
    coverage_actual_rows: list[dict],
    format_result: dict,
    pdf_dir: Path | None,
    forced_review_reasons: Mapping[str, str] | None = None,
) -> list[ReviewSample]:
    forced_reasons = dict(forced_review_reasons or {})
    forced_samples: list[ReviewSample] = []
    for row_id in sorted(forced_reasons):
        summary = row_summaries.get(row_id)
        if summary is None:
            continue
        forced_samples.append(
            ReviewSample(
                sample_rank=0,
                row_id=row_id,
                sample_kind="mandatory",
                reason_code="classification_disagreement",
                secondary_reasons="",
                reason_detail=forced_reasons[row_id],
                coverage_cell_key=summary.coverage_cell_key,
                metric_name="classification_consistency",
                metric_value=0.0,
                related_row_id="",
                seed_candidate_id=summary.seed_candidate_id,
                seed_extraction_id=summary.seed_extraction_id,
                pdf_path="",
            )
        )

    remaining_quota = max(0, sample_count - len(forced_samples))
    anomaly_quota = min(10, math.ceil(remaining_quota * 2 / 3))
    anomaly_samples = select_anomaly_samples(
        quota=anomaly_quota,
        row_summaries=row_summaries,
        duplicate_groups=duplicate_groups,
        coverage_actual_rows=coverage_actual_rows,
        format_result=format_result,
    )
    forced_row_ids = {sample.row_id for sample in forced_samples}
    anomaly_samples = [
        sample for sample in anomaly_samples if sample.row_id not in forced_row_ids
    ]
    chosen_row_ids = forced_row_ids | {
        sample.row_id for sample in anomaly_samples
    }
    representative_quota = max(
        0,
        sample_count - len(forced_samples) - len(anomaly_samples),
    )
    representative_samples = select_representative_samples(
        quota=representative_quota,
        row_summaries=row_summaries,
        coverage_actual_rows=coverage_actual_rows,
        chosen_row_ids=chosen_row_ids,
    )

    final_samples: list[ReviewSample] = []
    selected = forced_samples + anomaly_samples + representative_samples
    for rank, sample in enumerate(selected, start=1):
        pdf_path, status = pdf_status(sample.row_id, pdf_dir)
        secondary = sample.secondary_reasons
        if status != "ok":
            secondary = f"{secondary},pdf_unavailable" if secondary else "pdf_unavailable"
        final_samples.append(replace(sample, sample_rank=rank, pdf_path=pdf_path, secondary_reasons=secondary))
    return final_samples
