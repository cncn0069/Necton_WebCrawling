"""Coverage planner용 최소 후보 프로파일링(v1a).

설계 문서 §13은 v1a에 "candidate profile"을 명시적으로 포함시킨다 — 실측
후보 수를 all-zero로 미루지 않는다. 이 모듈은 span 후보(조항 5~8)마다
(세부조항, 문서유형, 기관군)을 한 번만 해석해 ``coverage_plan.py``가
쓸 카운트의 원천 데이터를 만든다.

기관 해석은 ``agency_resolver.resolve_agency_for_candidate()``와 같은 규칙을
쓰지만, 후보별 ``LIKE`` 쿼리 대신 실제 존재하는 ``(source, doc_type)`` 조합
단위로 배치 조회한다(설계 문서 §5) — DB 쿼리 수는 후보 수가 아니라 고유
source/doc-type 조합 수 이하다.

admin_status는 이 프로파일에 없다 — span 후보에는 문서별 행정상태가 붙어있지
않다(scripts/generate_cs_pilot.py의 ``_bucket_span_candidates_by_subclause_doc_type``
독스트링이 이미 명시한 갭, "Phase A가 다뤄야 할 갭"). 따라서 카운트는
coverage_plan.compute_candidate_profile_counts()에서 admin_status 축 없이 집계된
뒤, 같은 그룹의 여러 admin_status 셀에 동일 값으로 broadcast된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.disclosure.agency_categories import get_agency_category
from rd2.generators.agency_resolver import FIXED_AGENCY_BY_SOURCE, PER_DOC_AGENCY_SOURCES
from rd2.generators.doc_type_inference import infer_doc_type
from rd2.generators.template_matrix import infer_subclause_key

RESOLVER_VERSION = "agency-resolver-v1"
AGENCY_CATEGORY_RULE_VERSION = "agency-category-rules-v1"

_PROFILES_FILENAME = "candidate_profiles.jsonl"
_PROFILE_MANIFEST_FILENAME = "_profile_manifest.json"


@dataclass(frozen=True)
class ProfileRow:
    candidate_id: str
    extraction_id: str
    source: str
    doc_type: str  # target-matrix doc_type(예: report, bid_notice) — 원본 source doc_type 아님
    doc_id: str
    clause_no: str
    subclause_key: str
    agency_category: str
    profile_status: str  # "resolved" | "unresolved"
    unresolved_reason: str  # profile_status == "resolved"면 빈 문자열


def preload_agency_index(conn, candidates: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str], str | None]:
    """(source, doc_type, doc_id) -> ordering_agency 배치 조회 인덱스.

    후보에 실제로 등장하는 distinct (source, doc_type) 조합당 쿼리 1회만
    실행한다 — 이전 ``_fetch_agency_by_body_file_path``의 후보 1건당 LIKE 쿼리
    패턴(설계 문서 §5가 지적한 N+1)을 대체한다. body_file_path 포맷은
    ``"{source}/{doc_type}/{bucket}/{doc_id}_{filename}"``(agency_resolver.py
    모듈 독스트링) — bucket 세그먼트를 건너뛰고 doc_id를 파싱한다.
    """
    doc_ids_by_group: dict[tuple[str, str], set[str]] = {}
    for candidate in candidates:
        source = candidate.get("source")
        doc_type = candidate.get("doc_type")
        doc_id = candidate.get("doc_id")
        if not source or not doc_type or doc_id is None:
            continue
        if source not in PER_DOC_AGENCY_SOURCES:
            continue
        doc_ids_by_group.setdefault((source, doc_type), set()).add(str(doc_id))

    index: dict[tuple[str, str, str], str | None] = {}
    for (source, doc_type), doc_ids in doc_ids_by_group.items():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT body_file_path, ordering_agency FROM documents WHERE source = %s AND doc_type = %s",
                (source, doc_type),
            )
            rows = cur.fetchall()
        prefix = f"{source}/{doc_type}/"
        for row in rows:
            body_file_path = row["body_file_path"] if isinstance(row, dict) else row[0]
            ordering_agency = row["ordering_agency"] if isinstance(row, dict) else row[1]
            if not body_file_path or not body_file_path.startswith(prefix):
                continue
            remainder = body_file_path[len(prefix):]
            parts = remainder.split("/", 1)
            if len(parts) != 2:
                continue
            doc_id = parts[1].split("_", 1)[0]
            if doc_id in doc_ids:
                index.setdefault((source, doc_type, doc_id), ordering_agency or None)
    return index


def _resolve_agency(
    candidate: Mapping[str, Any], agency_index: Mapping[tuple[str, str, str], str | None]
) -> tuple[str | None, str]:
    source = candidate.get("source")
    if not source:
        return None, "missing_source"

    fixed = FIXED_AGENCY_BY_SOURCE.get(source)
    if fixed is not None:
        return fixed, ""

    if source not in PER_DOC_AGENCY_SOURCES:
        return None, "unsupported_source"

    doc_type = candidate.get("doc_type")
    doc_id = candidate.get("doc_id")
    if not doc_type or doc_id is None:
        return None, "missing_doc_identity"

    agency = agency_index.get((source, doc_type, str(doc_id)))
    if agency is None:
        return None, "not_found_in_documents_table"
    return agency, ""


def profile_candidates(candidates_by_clause: Mapping[str, list[dict]], *, conn) -> list[ProfileRow]:
    """clause_N.jsonl 버킷을 받아 셀 카운트에 쓸 ProfileRow 목록을 만든다."""
    all_candidates = [c for candidates in candidates_by_clause.values() for c in candidates]
    agency_index = preload_agency_index(conn, all_candidates)

    rows: list[ProfileRow] = []
    for clause_no, candidates in candidates_by_clause.items():
        for candidate in candidates:
            text = candidate.get("text") or ""
            matrix_doc_type = infer_doc_type(clause_no, keyword_text=text)
            subclause_key = infer_subclause_key(clause_no, matrix_doc_type, keyword_text=text) or ""
            agency, unresolved_reason = _resolve_agency(candidate, agency_index)
            profile_status = "resolved" if agency is not None else "unresolved"
            agency_category = get_agency_category(agency) if agency is not None else ""
            rows.append(
                ProfileRow(
                    candidate_id=candidate.get("candidate_id", ""),
                    extraction_id=candidate.get("extraction_id", ""),
                    source=candidate.get("source") or "",
                    doc_type=matrix_doc_type,
                    doc_id=str(candidate.get("doc_id", "")),
                    clause_no=clause_no,
                    subclause_key=subclause_key,
                    agency_category=agency_category,
                    profile_status=profile_status,
                    unresolved_reason=unresolved_reason,
                )
            )
    return rows


def _flush_and_sync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def write_candidate_profiles_atomic(
    candidates_dir: Path,
    rows: list[ProfileRow],
    *,
    candidate_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """candidate_profiles.jsonl + _profile_manifest.json을 atomic 게시한다.

    scripts/find_candidates.py의 ``_atomic_write_manifest``(temp 파일 →
    ``os.replace`` → manifest 마지막)와 동일 패턴이다. 정렬은
    ``(clause_no, subclause_key, doc_type, candidate_id)``로 고정해 같은 입력이면
    항상 같은 바이트가 나오게 한다.
    """
    candidates_dir = Path(candidates_dir)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    run_id = candidate_manifest["run_id"]

    sorted_rows = sorted(rows, key=lambda r: (r.clause_no, r.subclause_key, r.doc_type, r.candidate_id))
    row_dicts = [asdict(row) for row in sorted_rows]

    profiles_path = candidates_dir / _PROFILES_FILENAME
    manifest_path = candidates_dir / _PROFILE_MANIFEST_FILENAME
    temp_profiles_path = candidates_dir / f".{_PROFILES_FILENAME}.{run_id}.tmp"
    temp_manifest_path = candidates_dir / f".{_PROFILE_MANIFEST_FILENAME}.{run_id}.tmp"

    with temp_profiles_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row_dict in row_dicts:
            handle.write(json.dumps(row_dict, ensure_ascii=False, separators=(",", ":")) + "\n")
        _flush_and_sync(handle)

    manifest = {
        "schema_version": 1,
        "artifact": "rd2-candidate-profiles",
        "candidate_manifest_run_id": run_id,
        "candidate_manifest_rule_version": candidate_manifest["rule_version"],
        "resolver_version": RESOLVER_VERSION,
        "agency_category_rule_version": AGENCY_CATEGORY_RULE_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "row_count": len(row_dicts),
        "rows_sha256": canonical_sha256(row_dicts, normalization_version=NORMALIZATION_VERSION),
    }
    with temp_manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _flush_and_sync(handle)

    os.replace(temp_profiles_path, profiles_path)
    os.replace(temp_manifest_path, manifest_path)
    return manifest
