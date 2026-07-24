"""Canonical extraction identity and atomic gzip JSON storage helpers.

The source document remains the lossless artifact.  Extraction JSON is a
compact, reproducible cache that can be replaced safely after a complete run.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 2
EXTRACTION_PROFILE = "layout-lite-v1"
_GZIP_COMPRESSLEVEL = 6


def compute_source_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the lowercase SHA-256 digest of *path* without loading it at once."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source_file:
        while chunk := source_file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_extraction_id(source_sha256: str, extraction: Mapping[str, Any]) -> str:
    """Build a deterministic cache identity from source and extractor config."""

    identity = {
        "schema_version": SCHEMA_VERSION,
        "source_sha256": source_sha256,
        "profile": extraction.get("profile"),
        "extractor": extraction.get("extractor"),
        "extractor_version": extraction.get("extractor_version"),
        "config": extraction.get("config", {}),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def source_metadata(source_path: Path, data_root: Path) -> dict[str, Any]:
    """Return stable, portable metadata inferred from a source under data_root."""

    source_path = Path(source_path).resolve()
    data_root = Path(data_root).resolve()
    try:
        relative_to_data = source_path.relative_to(data_root)
    except ValueError as exc:
        raise ValueError(f"source path must be under data root: {source_path}") from exc

    parts = relative_to_data.parts
    source = parts[0] if parts else "_unclassified"
    doc_type = parts[1] if len(parts) > 2 else "_unclassified"
    stem = source_path.stem
    doc_id = stem.split("_", 1)[0] if "_" in stem and stem.split("_", 1)[0] else None

    return {
        "source_path": source_path.relative_to(data_root.parent).as_posix(),
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "source_format": source_path.suffix.lower().lstrip("."),
    }


def read_json_gz(path: Path) -> Any:
    """Read one UTF-8 JSON value from a gzip file."""

    with gzip.open(Path(path), "rt", encoding="utf-8") as input_file:
        return json.load(input_file)


def write_json_gz_atomic(path: Path, payload: Any) -> None:
    """Atomically replace *path* with deterministic compact gzip JSON.

    The temporary file lives beside the target so ``os.replace`` never crosses
    filesystems.  Any serialization, compression, flush, or replace failure
    removes the temporary file and leaves an existing target untouched.
    """

    path = Path(path)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    descriptor_is_open = True
    try:
        with os.fdopen(descriptor, "wb") as raw_file:
            descriptor_is_open = False
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_file,
                compresslevel=_GZIP_COMPRESSLEVEL,
                mtime=0,
            ) as compressed_file:
                compressed_file.write(serialized)
            raw_file.flush()
            os.fsync(raw_file.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if descriptor_is_open:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def extraction_output_path(source_path: Path, data_root: Path, extracted_root: Path) -> Path:
    """Map a source to ``<original-name>.<ext>.json.gz`` under extracted_root."""

    source_path = Path(source_path).resolve()
    data_root = Path(data_root).resolve()
    try:
        relative = source_path.relative_to(data_root)
    except ValueError as exc:
        raise ValueError(f"source path must be under data root: {source_path}") from exc
    return Path(extracted_root) / relative.parent / f"{relative.name}.json.gz"


def is_current_extraction(
    path: Path,
    source_sha256: str,
    *,
    expected_extraction_id: str | None = None,
) -> bool:
    """Return whether *path* matches the source and optional current config ID."""

    try:
        payload = read_json_gz(path)
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError, UnicodeDecodeError):
        return False
    is_current = bool(
        isinstance(payload, dict)
        and payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("source_sha256") == source_sha256
        and isinstance(payload.get("extraction_id"), str)
        and payload["extraction_id"]
    )
    if not is_current:
        return False
    return expected_extraction_id is None or payload["extraction_id"] == expected_extraction_id
