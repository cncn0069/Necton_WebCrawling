"""Scan canonical extraction-v2 artifacts once and publish candidate JSONL.

Each artifact allowlisted by the current extraction run manifest is
decompressed once, annotated in memory once, and traversed once for clauses
5/6/7/8 plus administrative status candidates. The five JSONL files are staged
under one ``run_id`` and replaced before ``_manifest.json`` is atomically
published last.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


from rd2.augmentation.annotate import annotate_document_in_place  # noqa: E402
from rd2.augmentation.candidates import (  # noqa: E402
    ADMIN_STATUS_RULES_BY_DOC_TYPE,
    CANDIDATE_RULE_VERSION,
    find_all_candidates,
)
from rd2.extraction.pipeline import iter_source_documents  # noqa: E402
from rd2.extraction.storage import read_json_gz  # noqa: E402
from rd2.generators.template_matrix import _GROUPS, infer_subclause_key  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"

_CLAUSES = ("5", "6", "7", "8")
_ADMINISTRATIVE = "administrative"
_TARGETS = (*_CLAUSES, _ADMINISTRATIVE)
_OUTPUT_FILENAMES = {
    **{clause_no: f"clause_{clause_no}.jsonl" for clause_no in _CLAUSES},
    _ADMINISTRATIVE: "administrative.jsonl",
}

NO_RULE_DEFINED = "no_rule_defined"
RULE_DEFINED_ZERO_MATCHES = "rule_defined_zero_matches"
COUNTED = "counted"
_READABLE_EXTRACTION_STATUSES = {"ok", "needs_ocr"}


def _resolve_manifest_artifact_path(extracted_root: Path, output_path: Any) -> Path:
    """Resolve one manifest-relative artifact without allowing root escape."""

    if not isinstance(output_path, str) or not output_path.strip():
        raise ValueError("manifest artifact output_path must be a non-empty string")
    relative_path = Path(output_path)
    if relative_path.is_absolute() or relative_path.drive or relative_path.root:
        raise ValueError(f"manifest artifact output_path must be relative: {output_path!r}")
    if relative_path == Path(".") or ".." in relative_path.parts:
        raise ValueError(f"manifest artifact output_path escapes extracted_root: {output_path!r}")

    resolved_root = Path(extracted_root).resolve()
    resolved_path = (resolved_root / relative_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"manifest artifact output_path escapes extracted_root: {output_path!r}"
        ) from exc
    return resolved_path


def _artifact_display_path(entry: Any, index: int) -> str:
    if isinstance(entry, dict) and isinstance(entry.get("output_path"), str):
        return entry["output_path"]
    return f"artifacts[{index}]"


def _validate_manifest_identity(document: dict[str, Any], entry: dict[str, Any]) -> None:
    """Bind the decompressed document to the exact extraction manifest entry."""

    for field in ("source_path", "extraction_id", "status"):
        expected = entry.get(field)
        if not isinstance(expected, str) or not expected:
            raise ValueError(f"manifest artifact {field} must be a non-empty string")
        actual = document.get(field)
        if actual != expected:
            raise ValueError(
                f"manifest/document {field} mismatch: expected {expected!r}, got {actual!r}"
            )

    status = entry["status"]
    if status not in _READABLE_EXTRACTION_STATUSES:
        raise ValueError(f"upstream extraction artifact status={status!r}")


def _manifest_source_path(source_path: Path, data_root: Path) -> str:
    """Return the source identity used by extraction manifests."""

    return source_path.resolve().relative_to(data_root.resolve().parent).as_posix()


def _validate_full_corpus_manifest(
    manifest: dict[str, Any] | None,
    *,
    extracted_root: Path,
) -> None:
    """Block global candidate publication from a subset extraction run.

    Candidate JSONL files are global, not source-namespaced. The latest
    extraction manifest must therefore account for every source document that
    currently exists, including documents recorded as failures.
    """

    if not isinstance(manifest, dict):
        raise RuntimeError(
            "global candidate publish blocked: a readable full-corpus extraction "
            "manifest is required"
        )

    data_root = Path(extracted_root).parent
    expected = {
        _manifest_source_path(path, data_root)
        for path in iter_source_documents(data_root)
    }
    covered: set[str] = set()
    for section in ("artifacts", "failures"):
        entries = manifest.get(section, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                source_path = entry.get("source_path")
                if isinstance(source_path, str) and source_path:
                    covered.add(source_path)

    if expected == covered:
        return

    missing = sorted(expected - covered)
    extra = sorted(covered - expected)
    details = [
        f"current_sources={len(expected)}",
        f"manifest_sources={len(covered)}",
    ]
    if missing:
        details.append(f"missing={missing[:3]!r}")
    if extra:
        details.append(f"extra={extra[:3]!r}")
    raise RuntimeError(
        "global candidate publish blocked: extraction manifest is not full-corpus "
        f"({', '.join(details)}). Run extract_documents.py --source all first; "
        "--allow-partial does not bypass corpus scope validation."
    )


def _temporary_path(candidates_root: Path, filename: str, run_id: str) -> Path:
    return candidates_root / f".{filename}.{run_id}.tmp"


def _flush_and_sync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _atomic_write_manifest(candidates_root: Path, manifest: dict[str, Any], run_id: str) -> None:
    final_path = candidates_root / "_manifest.json"
    temp_path = _temporary_path(candidates_root, "_manifest.json", run_id)
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        _flush_and_sync(handle)
    os.replace(temp_path, final_path)


def run_candidate_scan(
    *,
    extracted_root: Path = _EXTRACTED_ROOT,
    candidates_root: Path = _CANDIDATES_ROOT,
    reader: Callable[[Path], Any] = read_json_gz,
    run_id: str | None = None,
    allow_partial: bool = False,
    require_full_corpus: bool = False,
) -> dict[str, Any]:
    """Run one corpus pass, publish all candidate artifacts, and return its manifest."""
    actual_run_id = run_id or uuid.uuid4().hex
    temp_paths = {
        target: _temporary_path(candidates_root, filename, actual_run_id)
        for target, filename in _OUTPUT_FILENAMES.items()
    }
    handles: dict[str, Any] = {}
    counts = {target: 0 for target in _TARGETS}
    failures: list[dict[str, str]] = []
    documents_seen = 0
    documents_succeeded = 0
    extraction_run_id: str | None = None
    upstream_artifacts: list[Any] = []
    upstream_manifest: dict[str, Any] | None = None

    upstream_manifest_path = extracted_root / "_run_manifest.json.gz"
    if not upstream_manifest_path.exists():
        upstream_error = "upstream extraction manifest is missing"
        if not allow_partial:
            raise RuntimeError(
                f"candidate scan blocked: {upstream_error}; "
                "run extract_documents.py first or pass --allow-partial to inspect"
            )
        failures.append(
            {
                "path": "_run_manifest.json.gz",
                "error": upstream_error,
            }
        )
    else:
        upstream_issues: list[str] = []
        try:
            upstream_manifest = reader(upstream_manifest_path)
        except Exception as exc:  # noqa: BLE001 - a broken upstream manifest is itself partial
            upstream_issues.append(
                f"upstream extraction manifest unreadable: {type(exc).__name__}: {exc}"
            )
        else:
            if not isinstance(upstream_manifest, dict):
                upstream_issues.append("upstream extraction manifest must be a JSON object")
            else:
                upstream_status = upstream_manifest.get("status")
                if upstream_status != "complete":
                    upstream_issues.append(f"upstream extraction status={upstream_status!r}")

                run_value = upstream_manifest.get("run_id")
                if isinstance(run_value, str) and run_value:
                    extraction_run_id = run_value
                else:
                    upstream_issues.append("upstream extraction manifest run_id is missing")

                if "artifacts" not in upstream_manifest:
                    upstream_issues.append("upstream extraction manifest artifacts are missing")
                elif not isinstance(upstream_manifest["artifacts"], list):
                    upstream_issues.append("upstream extraction manifest artifacts must be a list")
                else:
                    upstream_artifacts = list(upstream_manifest["artifacts"])

        if upstream_issues:
            if not allow_partial:
                raise RuntimeError(
                    "candidate scan blocked: "
                    + "; ".join(upstream_issues)
                    + "; pass --allow-partial to proceed"
                )
            failures.extend(
                {
                    "path": "_run_manifest.json.gz",
                    "error": issue,
                }
                for issue in upstream_issues
            )

    if require_full_corpus:
        _validate_full_corpus_manifest(
            upstream_manifest,
            extracted_root=extracted_root,
        )

    candidates_root.mkdir(parents=True, exist_ok=True)

    try:
        handles = {
            target: path.open("w", encoding="utf-8", newline="\n")
            for target, path in temp_paths.items()
        }
        seen_paths: set[Path] = set()
        for index, artifact_entry in enumerate(upstream_artifacts):
            documents_seen += 1
            display_path = _artifact_display_path(artifact_entry, index)
            try:
                if not isinstance(artifact_entry, dict):
                    raise ValueError("manifest artifact entry must be a JSON object")
                path = _resolve_manifest_artifact_path(
                    extracted_root,
                    artifact_entry.get("output_path"),
                )
                if path in seen_paths:
                    raise ValueError(f"duplicate manifest artifact output_path: {display_path!r}")
                seen_paths.add(path)
                if not path.is_file():
                    raise FileNotFoundError(f"manifest artifact does not exist: {display_path}")
                document = reader(path)
                if not isinstance(document, dict):
                    raise ValueError("extraction artifact root must be a JSON object")
                _validate_manifest_identity(document, artifact_entry)
                annotate_document_in_place(document)
                found = find_all_candidates(document)
                serialized: dict[str, list[str]] = {}
                for target in _TARGETS:
                    records = []
                    for candidate in found[target]:
                        record = {
                            **candidate,
                            "run_id": actual_run_id,
                            "rule_version": CANDIDATE_RULE_VERSION,
                        }
                        records.append(
                            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                        )
                    serialized[target] = records
            except Exception as exc:  # noqa: BLE001 - report one corrupt source and continue
                failures.append(
                    {
                        "path": display_path,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            for target, lines in serialized.items():
                handles[target].writelines(lines)
                counts[target] += len(lines)
            documents_succeeded += 1

        for handle in handles.values():
            _flush_and_sync(handle)
            handle.close()
        handles.clear()

        status = "partial" if failures else "complete"
        manifest = {
            "schema_version": 2,
            "artifact": "rd2-candidates",
            "run_id": actual_run_id,
            "extraction_run_id": extraction_run_id,
            "rule_version": CANDIDATE_RULE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": status,
            "counts": {
                "documents_seen": documents_seen,
                "documents_succeeded": documents_succeeded,
                "documents_failed": documents_seen - documents_succeeded,
                "failure_events": len(failures),
                **counts,
            },
            "failures": failures,
        }

        # There is no multi-file rename transaction. Publishing the manifest
        # last makes a torn run detectable: consumers trust only records whose
        # run_id matches the current manifest.
        for target in _TARGETS:
            os.replace(temp_paths[target], candidates_root / _OUTPUT_FILENAMES[target])
        _atomic_write_manifest(candidates_root, manifest, actual_run_id)
        return manifest
    finally:
        for handle in handles.values():
            handle.close()
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)
        _temporary_path(candidates_root, "_manifest.json", actual_run_id).unlink(missing_ok=True)


def _doc_key(record: dict[str, Any]) -> tuple[str, str]:
    """Use the extraction identity shared by clause/admin candidate records."""
    extraction_id = record.get("extraction_id")
    if not extraction_id:
        raise ValueError("candidate record is missing extraction_id")
    source_path = record.get("source_path")
    if not source_path:
        raise ValueError("candidate record is missing source_path")
    return str(extraction_id), str(source_path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_clause_candidates(
    clause_no: str, candidates_root: Path = _CANDIDATES_ROOT
) -> list[dict[str, Any]]:
    return _load_jsonl(candidates_root / f"clause_{clause_no}.jsonl")


def _load_administrative_candidates(
    candidates_root: Path = _CANDIDATES_ROOT,
) -> list[dict[str, Any]]:
    return _load_jsonl(candidates_root / "administrative.jsonl")


def measure_cells(candidates_root: Path = _CANDIDATES_ROOT) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Measure existing clause/admin candidate artifacts by target matrix cell."""
    admin_candidates = _load_administrative_candidates(candidates_root)
    admin_status_by_doc: dict[tuple[str, str], set[str]] = defaultdict(set)
    for candidate in admin_candidates:
        admin_status_by_doc[_doc_key(candidate)].add(candidate["document_status"])

    clause_candidates_cache = {
        clause_no: _load_clause_candidates(clause_no, candidates_root)
        for clause_no in _CLAUSES
    }
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for clause_no, groups in _GROUPS.items():
        if clause_no not in _CLAUSES:
            continue
        clause_candidates = clause_candidates_cache[clause_no]
        for subclause_key, _label, doc_types in groups:
            for doc_type in doc_types:
                cell_key = (clause_no, subclause_key, doc_type)
                matching_docs: set[tuple[str, str]] = set()
                span_candidates = 0
                for candidate in clause_candidates:
                    if candidate.get("doc_type") != doc_type:
                        continue
                    inferred = infer_subclause_key(
                        clause_no,
                        doc_type,
                        keyword_text=candidate.get("text", ""),
                    )
                    if inferred != subclause_key:
                        continue
                    span_candidates += 1
                    matching_docs.add(_doc_key(candidate))

                rules = ADMIN_STATUS_RULES_BY_DOC_TYPE.get(doc_type)
                if not rules:
                    cells[cell_key] = {
                        "state": NO_RULE_DEFINED,
                        "span_candidates": span_candidates,
                        "matching_docs": len(matching_docs),
                    }
                    continue

                status_counts: Counter[str] = Counter()
                for doc_key in matching_docs:
                    for status in admin_status_by_doc.get(doc_key, ()):
                        status_counts[status] += 1

                if not status_counts:
                    cells[cell_key] = {
                        "state": RULE_DEFINED_ZERO_MATCHES,
                        "span_candidates": span_candidates,
                        "matching_docs": len(matching_docs),
                    }
                else:
                    cells[cell_key] = {
                        "state": COUNTED,
                        "span_candidates": span_candidates,
                        "matching_docs": len(matching_docs),
                        "by_admin_status": dict(status_counts),
                    }
    return cells


def _print_cell_report(cells: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    by_state: Counter[str] = Counter(cell["state"] for cell in cells.values())
    print(f"셀(조항,세부조항,문서유형) 총 {len(cells)}개")
    print(f"  no_rule_defined          : {by_state[NO_RULE_DEFINED]}개")
    print(f"  rule_defined_zero_matches: {by_state[RULE_DEFINED_ZERO_MATCHES]}개")
    print(f"  counted                  : {by_state[COUNTED]}개")
    for (clause_no, subclause_key, doc_type), cell in sorted(cells.items()):
        print(f"  [{clause_no}호/{subclause_key}/{doc_type}] {cell}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clause",
        choices=[*_CLAUSES, _ADMINISTRATIVE, "all"],
        default="all",
        help=(
            "호환용 옵션. 원자적 run 일관성을 위해 지정값과 무관하게 모든 대상 파일을 "
            "한 번에 재생성한다."
        ),
    )
    parser.add_argument(
        "--cells",
        action="store_true",
        help="기존 candidate JSONL로 셀 측정 리포트만 출력",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="일부 추출 파일 실패를 manifest에 기록하되 프로세스 종료 코드는 0으로 허용",
    )
    args = parser.parse_args()

    if args.cells:
        _print_cell_report(measure_cells())
        return 0

    manifest = run_candidate_scan(
        allow_partial=args.allow_partial,
        require_full_corpus=True,
    )
    counts = manifest["counts"]
    print(
        f"후보 run {manifest['run_id']}: status={manifest['status']}, "
        f"documents={counts['documents_succeeded']}/{counts['documents_seen']}"
    )
    for target in _TARGETS:
        print(f"  {target}: {counts[target]}개")
    for failure in manifest["failures"]:
        print(f"  [실패] {failure['path']}: {failure['error']}")
    return 0 if manifest["status"] == "complete" or args.allow_partial else 1


if __name__ == "__main__":
    raise SystemExit(main())
