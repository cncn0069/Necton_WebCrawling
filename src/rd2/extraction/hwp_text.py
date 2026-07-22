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
from pathlib import Path
from typing import Any, Iterator

from rd2.extractors.hwp import extract_hwp

_HWP_SUFFIXES = (".hwp", ".hwpx")
_SCANNED_AVG_CHARS_THRESHOLD = 5  # pdf_text.py의 스캔본 판정과 동일한 기준


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
        doc = extract_hwp(hwp_path)
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
