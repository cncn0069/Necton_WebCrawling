"""XLSX/XLSM을 canonical v2 논리 줄로 변환한다.

`hwp_text.py`와 같은 위치의 모듈로, `rd2.extractors.excel.extract_excel`을
감싸서 pdf/hwp와 동일한 canonical v2 스키마를 만든다. 엑셀도 HWP처럼 렌더링
전 포맷이라 좌표(bbox)·스타일 개념이 없으므로 `bbox_pt`는 None, `style_runs`는
빈 목록으로 남긴다.

페이지 대응은 **워크시트 하나 = 페이지 하나**다 — 워크북에서 유일하게 자연스러운
문서 분할 단위이고, 시트 이름("2024년 4차" 등)이 실제 내용의 일부인 경우가
많아 각 페이지의 첫 줄로 넣는다. 행은 HWP 표 추출과 같은 관례로 마크다운 행
(`| 셀 | 셀 |`)으로 렌더링해서, 다운스트림이 표를 별도 경로로 다루지 않아도
된다.

예외를 던지지 않고 실패 시 status가 "error"/"quarantine"인 dict를 반환한다
(`pdf_text.py`·`hwp_text.py`와 동일 관례).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from rd2.extraction.storage import (
    EXTRACTION_PROFILE,
    SCHEMA_VERSION,
    build_extraction_id,
    compute_source_sha256,
    source_metadata,
)
from rd2.extractors.excel import extract_excel

_LITTLE_TEXT_THRESHOLD = 5  # pdf_text.py/hwp_text.py의 "텍스트 거의 없음" 기준과 동일
_MAX_LOGICAL_LINE_CHARS = 4_096
_ROW_CELL_OVERHEAD = len("|  ") + 1  # "| " + " " + 닫는 "|"
_MAX_CELL_CHARS = _MAX_LOGICAL_LINE_CHARS - _ROW_CELL_OVERHEAD

_EXCEL_EXTRACTION_CONFIG = {
    "line_mode": "sheet_row",
    "page_mode": "one_page_per_sheet",
    "sheet_name": "first_line_of_page",
    "row_format": "markdown_pipe_row",
    "cell_values": "cached_formula_results",
    "empty_rows": "dropped",
    "trailing_empty_cells": "dropped",
    "cell_newlines": "folded_to_space",
    "merged_cells": "top_left_only",
    "max_logical_line_chars": _MAX_LOGICAL_LINE_CHARS,
    "structured_tables": "not_materialized",
    "unsupported_or_unparseable": "quarantine",
    "geometry": "unavailable",
    "style_runs": "unavailable",
    "little_or_no_text": "needs_ocr",
}


def excel_extraction_metadata() -> dict[str, Any]:
    """Return the extractor identity/configuration used by canonical v2."""

    try:
        extractor_version = version("openpyxl")
    except PackageNotFoundError:
        extractor_version = "unknown"
    return {
        "profile": EXTRACTION_PROFILE,
        "extractor": "openpyxl",
        "extractor_version": extractor_version,
        "config": dict(_EXCEL_EXTRACTION_CONFIG),
    }


def _canonical_excel_base(
    excel_path: Path,
    *,
    data_root: Path,
    source_sha256: str,
) -> dict[str, Any]:
    extraction = excel_extraction_metadata()
    return {
        "schema_version": SCHEMA_VERSION,
        "extraction_id": build_extraction_id(source_sha256, extraction),
        "source_sha256": source_sha256,
        **source_metadata(excel_path, data_root),
        "extraction": extraction,
        "status": "error",
        "error": None,
        "quality": {
            "has_text_layer": False,
            "needs_ocr": False,
            "needs_quarantine": False,
            "pages_needing_ocr": [],
            "avg_chars_per_page": 0.0,
            "warnings": [],
        },
        "pages": [],
    }


def _split_cell_text(text: str) -> list[str]:
    """셀 하나가 항상 한 줄 안에 들어가도록 상한 길이로 자른다(내용은 보존)."""

    if len(text) <= _MAX_CELL_CHARS:
        return [text]
    return [text[start : start + _MAX_CELL_CHARS] for start in range(0, len(text), _MAX_CELL_CHARS)]


def _row_lines(cells: list[str]) -> tuple[list[str], bool]:
    """행 하나를 마크다운 표 행으로 렌더링하되 최대 줄 길이를 넘으면 이어서 나눈다."""

    lines: list[str] = []
    parts: list[str] = []
    length = 1  # 닫는 "|"
    split_oversized_cell = False

    def flush() -> None:
        nonlocal parts, length
        if parts:
            lines.append("".join(parts) + "|")
            parts = []
            length = 1

    for cell in cells:
        for index, chunk in enumerate(_split_cell_text(cell)):
            if index:
                split_oversized_cell = True
            piece = f"| {chunk} "
            if parts and length + len(piece) > _MAX_LOGICAL_LINE_CHARS:
                flush()
            parts.append(piece)
            length += len(piece)
    flush()
    return lines, split_oversized_cell


def _line(*, line_id: int, block_id: int | None, order: int, text: str) -> dict[str, Any]:
    return {
        "line_id": line_id,
        "block_id": block_id,
        "order": order,
        "text": text,
        "bbox_pt": None,
        "style_runs": [],
    }


def extract_excel_document(
    excel_path: Path,
    *,
    data_root: Path,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Extract canonical v2 worksheet rows without fake geometry."""

    excel_path = Path(excel_path)
    digest = source_sha256 or compute_source_sha256(excel_path)
    result = _canonical_excel_base(excel_path, data_root=data_root, source_sha256=digest)

    try:
        document = extract_excel(excel_path)
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop a batch
        result["error"] = f"extraction failed: {exc}"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["extraction_failed"]
        return result

    if document.is_encrypted or not document.is_valid:
        result["status"] = "quarantine"
        result["error"] = document.error or "invalid/corrupt or oversized"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = [
            "encrypted" if document.is_encrypted else "invalid_document"
        ]
        return result

    pages: list[dict[str, Any]] = []
    page_char_counts: list[int] = []
    next_line_id = 0
    row_character_count = 0
    split_oversized_cell = False

    for sheet_number, sheet in enumerate(document.sheets, start=1):
        lines: list[dict[str, Any]] = []
        if sheet.name.strip():
            lines.append(
                _line(line_id=next_line_id, block_id=None, order=0, text=sheet.name.strip())
            )
            next_line_id += 1
        for block_id, cells in enumerate(sheet.rows):
            row_lines, row_split_cell = _row_lines(cells)
            split_oversized_cell = split_oversized_cell or row_split_cell
            for text in row_lines:
                lines.append(
                    _line(
                        line_id=next_line_id,
                        block_id=block_id,
                        order=len(lines),
                        text=text,
                    )
                )
                next_line_id += 1
                row_character_count += len(text)

        page_char_counts.append(sum(len(line["text"]) for line in lines))
        pages.append(
            {
                "page": sheet_number,
                "width_pt": None,
                "height_pt": None,
                "rotation": None,
                "lines": lines,
            }
        )

    # 워크시트는 비어 있어도 항상 이름("Sheet1")을 갖는다 — 내용 유무는 시트
    # 이름 줄을 뺀 행 텍스트로만 판정해야 빈 워크북이 "텍스트 있음"으로 새지 않는다.
    has_text_layer = row_character_count >= _LITTLE_TEXT_THRESHOLD
    average_chars = sum(page_char_counts) / len(pages) if pages else 0.0

    warnings: list[str] = []
    if document.truncated:
        warnings.append("cell_budget_truncated")
    if split_oversized_cell:
        warnings.append("oversized_cell_split")
    if not has_text_layer:
        warnings.append("little_or_no_text")

    result["pages"] = pages
    result["status"] = "ok" if has_text_layer else "needs_ocr"
    result["quality"] = {
        "has_text_layer": has_text_layer,
        "needs_ocr": not has_text_layer,
        "needs_quarantine": False,
        "pages_needing_ocr": [] if has_text_layer else [page["page"] for page in pages],
        "avg_chars_per_page": round(average_chars, 1),
        "warnings": warnings,
    }
    return result
