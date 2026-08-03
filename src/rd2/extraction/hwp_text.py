"""HWP/HWPX에서 문단 단위 텍스트 span을 직접 추출한다.

`rd2.extractors.hwp.extract_hwp`(`hwp-hwpx-parser` 기반, Apache-2.0/순수
파이썬)를 감싸서 `pdf_text.extract_pdf_spans`와 같은 관례의 출력 스키마로
변환한다 — 다만 HWP는 렌더링 전 포맷이라 좌표(bbox) 개념이 없으므로 span에
bbox 필드를 넣지 않는다. `annotate.py`는 span에 bbox가 있는지 여부로
PDF(좌표 슬롯 기반)/HWP(텍스트 반복 기반) 판정 방식을 자동으로 분기한다.

표는 `extract_hwp`가 반환하는 `text`에 이미 마크다운 행(`| 셀 | 셀 |`)으로
인라인돼 있어(실사 확인됨), 별도 표 처리 없이 문단 span만으로 표 내용도
함께 건진다.

예외를 던지지 않고 실패 시 "error" 필드가 있는 dict를 반환한다 — 대량 배치
처리 중 파일 하나 때문에 전체가 멈추지 않게 하기 위함(`pdf_text.py`와 동일
관례).
"""

from __future__ import annotations

import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterator

from rd2.extractors.hwp import extract_hwp
from rd2.extraction.storage import (
    EXTRACTION_PROFILE,
    SCHEMA_VERSION,
    build_extraction_id,
    compute_source_sha256,
    source_metadata,
)

_HWP_SUFFIXES = (".hwp", ".hwpx")
_SCANNED_AVG_CHARS_THRESHOLD = 5  # pdf_text.py의 스캔본 판정과 동일한 기준
_MAX_LOGICAL_LINE_CHARS = 4_096
_PREFERRED_LINE_BREAK_CHARS = (
    " ",
    "\t",
    "|",
    ".",
    "。",
    "!",
    "?",
    "！",
    "？",
    ";",
    "；",
)
_HWP_EXTRACTION_CONFIG = {
    "line_mode": "logical_newline",
    "hwpx_table_cell_paragraphs": "preserved",
    "max_logical_line_chars": _MAX_LOGICAL_LINE_CHARS,
    "structured_tables": "not_materialized",
    "external_converter_fallback": "disabled",
    "unsupported_or_unparseable": "quarantine",
    "geometry": "unavailable",
    "style_runs": "unavailable",
    "little_or_no_text": "needs_ocr",
}


def iter_hwp_files(data_root: Path, source: str | None = None) -> Iterator[Path]:
    """data_root 아래 hwp/hwpx 파일을 순회한다 (확장자 대소문자 무관, source 지정 시 해당 폴더만)."""
    base = data_root / source if source else data_root
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in _HWP_SUFFIXES:
            yield path


def _parse_doc_id_and_title(path: Path) -> tuple[str | None, str]:
    """'{id}_{제목}.hwp' 컨벤션에서 id와 제목을 분리한다. id가 없으면 (None, 전체 stem)."""
    stem = path.stem
    if "_" in stem:
        doc_id, title = stem.split("_", 1)
        if doc_id:
            return doc_id, title
    return None, stem


def _source_and_doc_type(path: Path, data_root: Path) -> tuple[str, str]:
    rel_parts = path.relative_to(data_root).parts
    source = rel_parts[0] if len(rel_parts) > 0 else "_unclassified"
    doc_type = rel_parts[1] if len(rel_parts) > 2 else "_unclassified"
    return source, doc_type


def hwp_extraction_metadata() -> dict[str, Any]:
    """Return the extractor identity/configuration used by canonical v2."""

    try:
        extractor_version = version("hwp-hwpx-parser")
    except PackageNotFoundError:
        extractor_version = "unknown"
    return {
        "profile": EXTRACTION_PROFILE,
        "extractor": "hwp-hwpx-parser",
        "extractor_version": extractor_version,
        "config": dict(_HWP_EXTRACTION_CONFIG),
    }


def _canonical_hwp_base(
    hwp_path: Path,
    *,
    data_root: Path,
    source_sha256: str,
) -> dict[str, Any]:
    extraction = hwp_extraction_metadata()
    return {
        "schema_version": SCHEMA_VERSION,
        "extraction_id": build_extraction_id(source_sha256, extraction),
        "source_sha256": source_sha256,
        **source_metadata(hwp_path, data_root),
        "extraction": extraction,
        "status": "error",
        "error": None,
        "quality": {
            "has_text_layer": False,
            "needs_ocr": False,
            "needs_quarantine": False,
            "pages_needing_ocr": [],
            "avg_chars_per_page": None,
            "sparse_page_count": None,
            "sparse_page_ratio": None,
            "ocr_severity": "unknown",
            "warnings": [],
        },
        "pages": [],
    }


def _split_oversized_logical_line(text: str) -> list[str]:
    """Bound pathological parser lines without dropping or rewriting text."""

    chunks: list[str] = []
    remainder = text
    while len(remainder) > _MAX_LOGICAL_LINE_CHARS:
        window = remainder[:_MAX_LOGICAL_LINE_CHARS]
        split_at = max(
            (window.rfind(char) + 1 for char in _PREFERRED_LINE_BREAK_CHARS),
            default=0,
        )
        if split_at < _MAX_LOGICAL_LINE_CHARS // 2:
            split_at = _MAX_LOGICAL_LINE_CHARS
        chunks.append(remainder[:split_at])
        remainder = remainder[split_at:]
    if remainder:
        chunks.append(remainder)
    return chunks


def _logical_hwp_lines(text: str) -> tuple[list[dict[str, Any]], bool]:
    lines: list[dict[str, Any]] = []
    split_oversized_line = False
    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = (
        candidate for candidate in normalized_text.split("\n") if candidate.strip()
    )
    for source_line in source_lines:
        chunks = _split_oversized_logical_line(source_line)
        split_oversized_line = split_oversized_line or len(chunks) > 1
        for chunk in chunks:
            order = len(lines)
            lines.append(
                {
                    "line_id": order,
                    "block_id": None,
                    "order": order,
                    "text": chunk,
                    "bbox_pt": None,
                    "style_runs": [],
                }
            )
    return lines, split_oversized_line


def extract_hwp_document(
    hwp_path: Path,
    *,
    data_root: Path,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Extract canonical v2 HWP/HWPX logical lines without fake geometry."""

    hwp_path = Path(hwp_path)
    digest = source_sha256 or compute_source_sha256(hwp_path)
    result = _canonical_hwp_base(hwp_path, data_root=data_root, source_sha256=digest)

    try:
        document = extract_hwp(hwp_path, include_tables=False)
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop a batch
        result["error"] = f"extraction failed: {exc}"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["extraction_failed"]
        return result

    if document.is_encrypted or not document.is_valid:
        result["status"] = "quarantine"
        result["error"] = document.error or (
            "encrypted (no password)" if document.is_encrypted else "invalid/corrupt or oversized"
        )
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["encrypted" if document.is_encrypted else "invalid_document"]
        return result

    lines, split_oversized_line = _logical_hwp_lines(document.text)
    character_count = sum(len(line["text"]) for line in lines)
    has_text_layer = character_count >= _SCANNED_AVG_CHARS_THRESHOLD
    warnings = ["oversized_logical_line_split"] if split_oversized_line else []
    if not has_text_layer:
        warnings.append("little_or_no_text")
    result["pages"] = [
        {
            "page": 1,
            "width_pt": None,
            "height_pt": None,
            "rotation": None,
            "lines": lines,
        }
    ]
    result["status"] = "ok" if has_text_layer else "needs_ocr"
    result["quality"] = {
        "has_text_layer": has_text_layer,
        "needs_ocr": not has_text_layer,
        "needs_quarantine": False,
        "pages_needing_ocr": [] if has_text_layer else [1],
        "avg_chars_per_page": None,
        "sparse_page_count": 0 if has_text_layer else 1,
        "sparse_page_ratio": 0.0 if has_text_layer else 1.0,
        "ocr_severity": "clean" if has_text_layer else "needs_ocr",
        "warnings": warnings,
    }
    return result


def _text_to_spans(text: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for span_id, line in enumerate(t for t in text.split("\n") if t.strip()):
        spans.append({"span_id": span_id, "text": line, "length": len(line)})
    return spans


def extract_hwp_spans(hwp_path: Path, *, data_root: Path) -> dict[str, Any]:
    """HWP/HWPX 1개를 열어 문단 단위 텍스트 span 구조를 만든다.

    실패해도 예외를 던지지 않고 "error" 필드가 있는 dict를 반환한다. HWP는
    렌더링 전 포맷이라 PDF처럼 실제 "페이지" 개념이 없다 — 문서 전체를
    `page_no=1` 가짜 페이지 하나에 담는다(annotate.py의 좌표 슬롯 기반
    반복탐지는 span에 bbox가 없으면 자동으로 텍스트 기반 방식으로 대체됨).
    """
    source, doc_type = _source_and_doc_type(hwp_path, data_root)
    doc_id, _title = _parse_doc_id_and_title(hwp_path)
    result: dict[str, Any] = {
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "file_name": hwp_path.name,
        "source_pdf_path": str(hwp_path.relative_to(data_root.parent)),
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "extractor": "hwp-hwpx-parser",
    }

    try:
        doc = extract_hwp(hwp_path, include_tables=False)
    except Exception as exc:  # noqa: BLE001 — 배치 처리 중 파일 하나 실패로 전체를 죽이지 않음
        result["error"] = f"extraction failed: {exc}"
        return result

    if doc.is_encrypted:
        result["error"] = doc.error or "encrypted (no password)"
        return result
    if not doc.is_valid:
        result["error"] = doc.error or "invalid/corrupt or oversized"
        return result

    spans = _text_to_spans(doc.text)
    total_chars = sum(s["length"] for s in spans)
    result["num_pages"] = 1
    result["has_text_layer"] = total_chars >= _SCANNED_AVG_CHARS_THRESHOLD
    result["pages"] = [{"page_no": 1, "spans": spans}]
    return result
