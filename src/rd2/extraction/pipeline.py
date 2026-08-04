"""Unified PDF/HWP/HWPX/XLSX canonical extraction pipeline."""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from rd2.extraction.excel_text import excel_extraction_metadata, extract_excel_document
from rd2.extraction.hwp_text import extract_hwp_document, hwp_extraction_metadata
from rd2.extraction.pdf_text import extract_pdf_document, pdf_extraction_metadata
from rd2.extraction.storage import (
    build_extraction_id,
    compute_source_sha256,
    extraction_output_path,
    is_current_extraction,
    read_json_gz,
    write_json_gz_atomic,
)

SUPPORTED_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".xlsx", ".xlsm"}
NON_SOURCE_DIRS = {"extracted", "structured", "annotated", "candidates", "augmented"}
RUN_MANIFEST_NAME = "_run_manifest.json.gz"


def discover_sources(data_root: Path) -> list[str]:
    data_root = Path(data_root)
    if not data_root.exists():
        return []
    return sorted(
        path.name
        for path in data_root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name not in NON_SOURCE_DIRS
    )


def _allowed_suffixes(source_format: str) -> set[str]:
    if source_format == "all":
        return set(SUPPORTED_SUFFIXES)
    suffix = f".{source_format.lower().lstrip('.')}"
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported format: {source_format}")
    return {suffix}


def iter_source_documents(
    data_root: Path,
    *,
    source: str = "all",
    source_format: str = "all",
) -> Iterator[Path]:
    """Yield supported source documents in deterministic path order."""

    data_root = Path(data_root)
    suffixes = _allowed_suffixes(source_format)
    sources = discover_sources(data_root) if source == "all" else [source]
    resolved_data_root = data_root.resolve()
    for source_name in sources:
        source_root = (resolved_data_root / source_name).resolve()
        try:
            source_root.relative_to(resolved_data_root)
        except ValueError as exc:
            raise ValueError(f"source directory escapes data root: {source_name!r}") from exc
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*")):
            if path.is_file() and path.suffix.lower() in suffixes:
                yield path


def extraction_metadata_for(source_path: Path) -> dict[str, Any]:
    suffix = Path(source_path).suffix.lower()
    if suffix == ".pdf":
        return pdf_extraction_metadata()
    if suffix in {".hwp", ".hwpx"}:
        return hwp_extraction_metadata()
    if suffix in {".xlsx", ".xlsm"}:
        return excel_extraction_metadata()
    raise ValueError(f"unsupported document format: {source_path}")


def extract_document(
    source_path: Path,
    *,
    data_root: Path,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Route one source document to its canonical v2 extractor."""

    suffix = Path(source_path).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_document(
            source_path,
            data_root=data_root,
            source_sha256=source_sha256,
        )
    if suffix in {".hwp", ".hwpx"}:
        return extract_hwp_document(
            source_path,
            data_root=data_root,
            source_sha256=source_sha256,
        )
    if suffix in {".xlsx", ".xlsm"}:
        return extract_excel_document(
            source_path,
            data_root=data_root,
            source_sha256=source_sha256,
        )
    raise ValueError(f"unsupported document format: {source_path}")


def process_document(
    source_path: Path,
    *,
    data_root: Path,
    extracted_root: Path,
    force: bool = False,
) -> tuple[str, Path, dict[str, Any]]:
    """Extract and atomically store one source, or return its current cache."""

    source_path = Path(source_path)
    source_digest = compute_source_sha256(source_path)
    output_path = extraction_output_path(source_path, data_root, extracted_root)
    expected_id = build_extraction_id(source_digest, extraction_metadata_for(source_path))

    if not force and is_current_extraction(
        output_path,
        source_digest,
        expected_extraction_id=expected_id,
    ):
        existing = read_json_gz(output_path)
        return "skipped", output_path, existing

    payload = extract_document(
        source_path,
        data_root=data_root,
        source_sha256=source_digest,
    )
    write_json_gz_atomic(output_path, payload)
    return "processed", output_path, payload


def _manifest_source_path(source_path: Path, data_root: Path) -> str:
    try:
        return Path(source_path).resolve().relative_to(Path(data_root).resolve().parent).as_posix()
    except ValueError:
        return str(Path(source_path))


def _manifest_output_path(output_path: Path, extracted_root: Path) -> str:
    """Return a normalized path rooted under the extraction artifact directory."""

    resolved_root = Path(extracted_root).resolve()
    resolved_output = Path(output_path).resolve()
    try:
        return resolved_output.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"extraction output is outside extracted_root: {resolved_output}"
        ) from exc


def _manifest_artifact(
    payload: dict[str, Any],
    *,
    output_path: Path,
    extracted_root: Path,
) -> dict[str, str]:
    """Build the downstream allowlist entry for one readable artifact."""

    required = ("source_path", "extraction_id", "status")
    missing = [field for field in required if not payload.get(field)]
    if missing:
        raise ValueError(f"extraction artifact is missing manifest fields: {', '.join(missing)}")
    return {
        "source_path": str(payload["source_path"]),
        "output_path": _manifest_output_path(output_path, extracted_root),
        "extraction_id": str(payload["extraction_id"]),
        "status": str(payload["status"]),
    }


def run_extraction(
    documents: Iterable[Path],
    *,
    data_root: Path,
    extracted_root: Path,
    force: bool = False,
) -> dict[str, Any]:
    """Run a batch, persist its latest manifest atomically, and return it."""

    data_root = Path(data_root)
    extracted_root = Path(extracted_root)
    started_at = dt.datetime.now(dt.timezone.utc)
    document_paths = list(documents)
    counts = {
        "total": len(document_paths),
        "processed": 0,
        "skipped": 0,
        "succeeded": 0,
        "failed": 0,
    }
    failures: list[dict[str, Any]] = []
    artifacts: list[dict[str, str]] = []

    for source_path in document_paths:
        source_label = _manifest_source_path(source_path, data_root)
        try:
            action, output_path, payload = process_document(
                source_path,
                data_root=data_root,
                extracted_root=extracted_root,
                force=force,
            )
            counts[action] += 1
            if payload.get("status") in {"error", "quarantine"}:
                counts["failed"] += 1
                failures.append(
                    {
                        "source_path": source_label,
                        "output_path": _manifest_output_path(output_path, extracted_root),
                        "status": payload.get("status"),
                        "error": payload.get("error"),
                    }
                )
            else:
                artifacts.append(
                    _manifest_artifact(
                        payload,
                        output_path=output_path,
                        extracted_root=extracted_root,
                    )
                )
                counts["succeeded"] += 1
        except Exception as exc:  # noqa: BLE001 - record one failure and continue the batch
            counts["failed"] += 1
            failures.append(
                {
                    "source_path": source_label,
                    "output_path": None,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    finished_at = dt.datetime.now(dt.timezone.utc)
    manifest = {
        "manifest_version": 1,
        "run_id": uuid.uuid4().hex,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "status": "partial" if failures else "complete",
        "counts": counts,
        "failures": failures,
        "artifacts": sorted(
            artifacts,
            key=lambda artifact: (
                artifact["source_path"],
                artifact["output_path"],
                artifact["extraction_id"],
                artifact["status"],
            ),
        ),
    }
    write_json_gz_atomic(extracted_root / RUN_MANIFEST_NAME, manifest)
    return manifest
