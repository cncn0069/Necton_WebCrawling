"""PDF에서 위치정보(좌표·길이·폰트)를 보존한 텍스트 span을 추출한다.

이후 단계(기밀도 상승 span 합성)가 원본 레이아웃을 깨지 않고 텍스트를
바꿔치기할 수 있으려면, 어떤 텍스트가 PDF의 어느 좌표(bbox)에 어떤
폰트/크기로 있었는지가 span 단위로 남아있어야 한다. 이 모듈은 그 추출만
담당하고, 무엇을 무엇으로 바꿀지 정하는 합성 로직은 다루지 않는다.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Iterator

import fitz  # PyMuPDF

from rd2.extraction.storage import (
    EXTRACTION_PROFILE,
    SCHEMA_VERSION,
    build_extraction_id,
    compute_source_sha256,
    source_metadata,
)

# 한 페이지의 추출 글자 수가 이 값 미만이면 텍스트가 거의 없는 sparse page로
# 본다. 문서 전체 OCR 여부는 sparse page 비율로 판정해 빈 표지나 구분 페이지 한
# 장 때문에 정상 문서 전체가 needs_ocr가 되는 일을 막는다.
_SPARSE_PAGE_CHAR_THRESHOLD = 5
_NEEDS_OCR_SPARSE_PAGE_RATIO = 0.5
_IMAGE_PDF_CANDIDATE_SPARSE_PAGE_RATIO = 0.9

# 같은 블록 안에서 "다음 줄이 현재 줄 바로 아래(세로로 이어짐)"인 경우만 한
# 문장/문단으로 합친다. 표는 같은 블록 안에 한 행의 여러 셀이 "같은 y좌표,
# 다른 x좌표"(가로 나열)로 들어오는 경우가 많아서(실사로 확인됨: 품목명·연도별
# 금액이 한 블록의 서로 다른 줄로 묶여 나옴) 이 조건이면 절대 합치지 않는다.
# 반대로 문장이 페이지 폭 때문에 줄바꿈된 경우(세로쓰기 표 제목도 포함, 한
# 글자씩 세로로 쌓인 경우)는 y좌표가 아래로 내려가며 간격도 좁아서 이 조건에
# 걸린다.
_MERGE_MAX_CHAIN = 40  # 한 문단이 가질 수 있는 최대 줄 수(폭주 방지 안전장치)
_MERGE_GAP_RATIO = 0.8  # 줄 높이 대비 허용되는 다음 줄까지의 세로 간격 비율

_LAYOUT_PRECISION = 1
_PDF_EXTRACTION_CONFIG = {
    "line_mode": "physical",
    "bbox_precision": _LAYOUT_PRECISION,
    "font_size_precision": _LAYOUT_PRECISION,
    "coalesce_adjacent_style_runs": True,
    "has_text_layer_mode": "any_non_sparse_page",
    "document_ocr_mode": "sparse_page_ratio",
    "sparse_page_char_threshold": _SPARSE_PAGE_CHAR_THRESHOLD,
    "needs_ocr_sparse_page_ratio": _NEEDS_OCR_SPARSE_PAGE_RATIO,
    "image_pdf_candidate_sparse_page_ratio": _IMAGE_PDF_CANDIDATE_SPARSE_PAGE_RATIO,
}
_FONT_BOLD_FLAG = int(getattr(fitz, "TEXT_FONT_BOLD", 16))
_FONT_ITALIC_FLAG = int(getattr(fitz, "TEXT_FONT_ITALIC", 2))


def _merge_wrapped_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 블록 안에서 세로로 이어지는 줄바꿈된 문장/문단을 하나의 span으로 합친다."""
    merged: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        cur = dict(lines[i])
        chain_len = 1
        j = i + 1
        while j < len(lines) and chain_len < _MERGE_MAX_CHAIN:
            nxt = lines[j]
            x0a, y0a, x1a, y1a = cur["bbox"]
            x0b, y0b, x1b, y1b = nxt["bbox"]
            line_height = max(y1a - y0a, 1.0)
            gap = y0b - y1a
            # gap >= 0(같은 행에서 겹치지 않음, 즉 표의 옆 셀이 아니라 아래 줄)
            # 이면서 그 간격이 줄 높이 대비 너무 크지 않을 때만 이어진 문장으로 본다.
            is_below_and_close = -0.5 <= gap <= line_height * _MERGE_GAP_RATIO
            # x좌표가 하나도 안 겹치면 서로 다른 컬럼(예: 좌우 2단 비교표의 왼쪽/
            # 오른쪽 열)일 가능성이 높다 — 세로로는 가까워 보여도 절대 합치지 않는다.
            x_overlaps = min(x1a, x1b) - max(x0a, x0b) > 0
            if is_below_and_close and x_overlaps:
                # 줄바꿈 지점의 공백은 PDF 텍스트 스트림에 보존되지 않는 경우가
                # 많다 — 그대로 이어붙이면 단어 경계 정보가 사라져서, 나중에
                # 합성 단계가 이 자리에 새 텍스트를 채워 넣을 때 줄바꿈 위치를
                # 잘못 계산하게 된다. 같은 줄 안 span 병합(위에서 이미 끝남,
                # 진짜로 붙어있는 글자들)과 달리, 서로 다른 줄을 잇는 지점에는
                # 공백 1개를 넣어 단어 경계를 보존한다(이미 공백이면 중복 방지).
                needs_space = not cur["text"].endswith(" ") and not nxt["text"].startswith(" ")
                cur["text"] += (" " if needs_space else "") + nxt["text"]
                cur["bbox"] = [min(x0a, x0b), y0a, max(x1a, x1b), y1b]
                chain_len += 1
                j += 1
            else:
                break
        merged.append(cur)
        i = j if j > i else i + 1
    return merged


def iter_pdf_files(data_root: Path, source: str | None = None) -> Iterator[Path]:
    """data_root 아래 PDF 파일을 순회한다 (확장자 대소문자 무관, source 지정 시 해당 폴더만)."""
    base = data_root / source if source else data_root
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".pdf":
            yield path


def _parse_doc_id_and_title(pdf_path: Path) -> tuple[str | None, str]:
    """'{id}_{제목}.pdf' 컨벤션에서 id와 제목을 분리한다. id가 없으면 (None, 전체 stem)."""
    stem = pdf_path.stem
    if "_" in stem:
        doc_id, title = stem.split("_", 1)
        if doc_id:
            return doc_id, title
    return None, stem


def _source_and_doc_type(pdf_path: Path, data_root: Path) -> tuple[str, str]:
    """data_root/{source}/{doc_type}/... 경로에서 source/doc_type을 읽어낸다."""
    rel_parts = pdf_path.relative_to(data_root).parts
    source = rel_parts[0] if len(rel_parts) > 0 else "_unclassified"
    doc_type = rel_parts[1] if len(rel_parts) > 2 else "_unclassified"
    return source, doc_type


def pdf_extraction_metadata() -> dict[str, Any]:
    """Return the extractor identity/configuration used by canonical v2."""

    return {
        "profile": EXTRACTION_PROFILE,
        "extractor": "pymupdf",
        "extractor_version": fitz.pymupdf_version,
        "config": dict(_PDF_EXTRACTION_CONFIG),
    }


def _rounded_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [round(float(coordinate), _LAYOUT_PRECISION) for coordinate in value]
    except (TypeError, ValueError):
        return None


def _style_payload(span: dict[str, Any]) -> dict[str, Any]:
    raw_size = span.get("size")
    try:
        size_pt = round(float(raw_size), _LAYOUT_PRECISION) if raw_size is not None else None
    except (TypeError, ValueError):
        size_pt = None
    try:
        flags = int(span.get("flags") or 0)
    except (TypeError, ValueError):
        flags = 0
    raw_color = span.get("color")
    try:
        color = int(raw_color) if raw_color is not None else None
    except (TypeError, ValueError):
        color = None
    return {
        "font": str(span.get("font") or ""),
        "size_pt": size_pt,
        "bold": bool(flags & _FONT_BOLD_FLAG),
        "italic": bool(flags & _FONT_ITALIC_FLAG),
        "color": color,
    }


def _physical_line_payload(
    line: dict[str, Any],
    *,
    line_id: int,
    block_id: int,
    order: int,
) -> dict[str, Any] | None:
    """Convert one PyMuPDF physical line and coalesce identical style runs."""

    spans = line.get("spans", [])
    text_parts: list[str] = []
    style_runs: list[dict[str, Any]] = []
    cursor = 0
    span_bboxes: list[list[float]] = []

    for raw_span in spans:
        text = str(raw_span.get("text") or "")
        if not text:
            continue
        text_parts.append(text)
        end = cursor + len(text)
        style = _style_payload(raw_span)
        if (
            style_runs
            and style_runs[-1]["end"] == cursor
            and all(style_runs[-1][key] == value for key, value in style.items())
        ):
            style_runs[-1]["end"] = end
        else:
            style_runs.append({"start": cursor, "end": end, **style})
        cursor = end
        bbox = _rounded_bbox(raw_span.get("bbox"))
        if bbox is not None:
            span_bboxes.append(bbox)

    text = "".join(text_parts)
    if not text.strip():
        return None

    if span_bboxes:
        bbox_pt = [
            round(min(bbox[0] for bbox in span_bboxes), _LAYOUT_PRECISION),
            round(min(bbox[1] for bbox in span_bboxes), _LAYOUT_PRECISION),
            round(max(bbox[2] for bbox in span_bboxes), _LAYOUT_PRECISION),
            round(max(bbox[3] for bbox in span_bboxes), _LAYOUT_PRECISION),
        ]
    else:
        bbox_pt = _rounded_bbox(line.get("bbox"))

    return {
        "line_id": line_id,
        "block_id": block_id,
        "order": order,
        "text": text,
        "bbox_pt": bbox_pt,
        "style_runs": style_runs,
    }


def _canonical_pdf_base(
    pdf_path: Path,
    *,
    data_root: Path,
    source_sha256: str,
) -> dict[str, Any]:
    extraction = pdf_extraction_metadata()
    return {
        "schema_version": SCHEMA_VERSION,
        "extraction_id": build_extraction_id(source_sha256, extraction),
        "source_sha256": source_sha256,
        **source_metadata(pdf_path, data_root),
        "extraction": extraction,
        "status": "error",
        "error": None,
        "quality": {
            "has_text_layer": False,
            "needs_ocr": False,
            "needs_quarantine": False,
            "pages_needing_ocr": [],
            "avg_chars_per_page": 0.0,
            "sparse_page_count": None,
            "sparse_page_ratio": None,
            "ocr_severity": "unknown",
            "warnings": [],
        },
        "pages": [],
    }


def extract_pdf_document(
    pdf_path: Path,
    *,
    data_root: Path,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Extract canonical v2 physical PDF lines without wrapped-line merging."""

    pdf_path = Path(pdf_path)
    digest = source_sha256 or compute_source_sha256(pdf_path)
    result = _canonical_pdf_base(pdf_path, data_root=data_root, source_sha256=digest)

    try:
        with pdf_path.open("rb") as source_file:
            header = source_file.read(1024)
    except OSError as exc:
        result["error"] = f"input read failed: {exc}"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["input_read_failed"]
        return result
    if b"%PDF-" not in header:
        result["error"] = "invalid file: no %PDF header"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["invalid_pdf_header"]
        return result

    try:
        document = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop a batch
        result["error"] = f"open failed: {exc}"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["open_failed"]
        return result

    try:
        if document.is_encrypted and not document.authenticate(""):
            result["error"] = "encrypted (no password)"
            result["quality"]["needs_quarantine"] = True
            result["quality"]["warnings"] = ["encrypted"]
            return result

        pages: list[dict[str, Any]] = []
        page_char_counts: list[int] = []
        next_line_id = 0
        for page_number, page in enumerate(document, start=1):
            raw = page.get_text("dict")
            lines: list[dict[str, Any]] = []
            page_order = 0
            for block_id, block in enumerate(raw.get("blocks", [])):
                for raw_line in block.get("lines", []):
                    line_payload = _physical_line_payload(
                        raw_line,
                        line_id=next_line_id,
                        block_id=block_id,
                        order=page_order,
                    )
                    if line_payload is None:
                        continue
                    lines.append(line_payload)
                    next_line_id += 1
                    page_order += 1

            page_char_counts.append(sum(len(line["text"]) for line in lines))
            pages.append(
                {
                    "page": page_number,
                    "width_pt": round(float(page.rect.width), _LAYOUT_PRECISION),
                    "height_pt": round(float(page.rect.height), _LAYOUT_PRECISION),
                    "rotation": int(page.rotation),
                    "lines": lines,
                }
            )

        average_chars = sum(page_char_counts) / len(pages) if pages else 0.0
        sparse_pages = [
            page_number
            for page_number, count in enumerate(page_char_counts, start=1)
            if count < _SPARSE_PAGE_CHAR_THRESHOLD
        ]
        sparse_page_ratio = len(sparse_pages) / len(pages) if pages else 1.0
        has_text_layer = any(
            count >= _SPARSE_PAGE_CHAR_THRESHOLD for count in page_char_counts
        )
        needs_ocr = sparse_page_ratio >= _NEEDS_OCR_SPARSE_PAGE_RATIO

        if sparse_page_ratio >= _IMAGE_PDF_CANDIDATE_SPARSE_PAGE_RATIO:
            ocr_severity = "image_pdf_candidate"
            warnings = ["image_pdf_candidate"]
        elif needs_ocr:
            ocr_severity = "needs_ocr"
            warnings = ["majority_sparse_pages"]
        elif sparse_pages:
            ocr_severity = "partial_text"
            warnings = ["partial_text_layer"]
        else:
            ocr_severity = "clean"
            warnings = []

        result["pages"] = pages
        result["status"] = "needs_ocr" if needs_ocr else "ok"
        result["quality"] = {
            "has_text_layer": has_text_layer,
            "needs_ocr": needs_ocr,
            "needs_quarantine": False,
            "pages_needing_ocr": sparse_pages,
            "avg_chars_per_page": round(average_chars, 1),
            "sparse_page_count": len(sparse_pages),
            "sparse_page_ratio": round(sparse_page_ratio, 4),
            "ocr_severity": ocr_severity,
            "warnings": warnings,
        }
        return result
    except Exception as exc:  # noqa: BLE001 - preserve a serializable failed result
        result["error"] = f"extraction failed: {exc}"
        result["quality"]["needs_quarantine"] = True
        result["quality"]["warnings"] = ["extraction_failed"]
        return result
    finally:
        document.close()


def extract_pdf_spans(pdf_path: Path, *, data_root: Path) -> dict[str, Any]:
    """PDF 한 개를 열어 페이지별 텍스트 span(위치·폰트 포함) 구조를 만든다.

    실패(암호화/손상 등)해도 예외를 던지지 않고 "error" 필드가 있는 dict를
    반환한다 — 대량 배치 처리 중 한 파일 때문에 전체가 멈추지 않게 하기 위함.
    """
    source, doc_type = _source_and_doc_type(pdf_path, data_root)
    doc_id, title = _parse_doc_id_and_title(pdf_path)
    result: dict[str, Any] = {
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "file_name": pdf_path.name,
        "source_pdf_path": str(pdf_path.relative_to(data_root.parent)),
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "extractor": "pymupdf",
        "extractor_version": fitz.pymupdf_version,
    }

    # PyMuPDF는 PDF가 아닌 파일(예: 다운로드 실패로 저장된 "파일이 존재하지
    # 않습니다" HTML 에러 페이지가 .pdf 확장자로 남은 경우)도 예외 없이 열어
    # "빈 1페이지 문서"로 취급해버린다 — 실제 스캔본(텍스트 레이어 없는 이미지
    # PDF)과 뒤섞이지 않도록 매직 바이트로 먼저 걸러낸다.
    with pdf_path.open("rb") as f:
        header = f.read(1024)
    if b"%PDF-" not in header:
        result["error"] = "invalid file: no %PDF header (다운로드 실패 스텁일 가능성)"
        return result

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 — 배치 처리 중 파일 하나 실패로 전체를 죽이지 않음
        result["error"] = f"open failed: {exc}"
        return result

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            result["error"] = "encrypted (no password)"
            return result

        pages: list[dict[str, Any]] = []
        span_id = 0
        total_chars = 0

        for page_no, page in enumerate(doc, start=1):
            page_spans: list[dict[str, Any]] = []
            raw = page.get_text("dict")
            for block in raw.get("blocks", []):
                # PyMuPDF는 한 줄(line) 안에서도 폰트 스타일이 바뀌는 지점마다
                # (자간 보정 등으로 한글은 글자 하나씩 나뉘는 경우가 흔함) span을
                # 쪼갠다 — 그대로 저장하면 "총 사업비 5억원" 같은 자연스러운 구가
                # 아니라 글자 단위로 흩어져서 용량도 커지고 나중에 문구를 바꿔치기할
                # 단위로도 못 쓴다. line 안의 span들을 순서대로 이어 붙여 한 줄을
                # 하나의 span으로 취급한다(대표 폰트/크기는 첫 span 기준).
                block_lines: list[dict[str, Any]] = []
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(s.get("text", "") for s in spans)
                    if not text.strip():
                        continue
                    xs0 = [s["bbox"][0] for s in spans if s.get("bbox")]
                    ys0 = [s["bbox"][1] for s in spans if s.get("bbox")]
                    xs1 = [s["bbox"][2] for s in spans if s.get("bbox")]
                    ys1 = [s["bbox"][3] for s in spans if s.get("bbox")]
                    bbox = [min(xs0), min(ys0), max(xs1), max(ys1)] if spans else list(line.get("bbox", []))
                    first = spans[0] if spans else {}
                    block_lines.append(
                        {
                            "text": text,
                            "bbox": bbox,
                            "font": first.get("font"),
                            "size": first.get("size"),
                            "flags": first.get("flags"),
                            "color": first.get("color"),
                        }
                    )

                for entry in _merge_wrapped_lines(block_lines):
                    entry["span_id"] = span_id
                    entry["length"] = len(entry["text"])
                    page_spans.append(entry)
                    span_id += 1
                    total_chars += entry["length"]

            pages.append(
                {
                    "page_no": page_no,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "spans": page_spans,
                }
            )

        result["num_pages"] = len(pages)
        avg_chars_per_page = total_chars / len(pages) if pages else 0
        result["has_text_layer"] = avg_chars_per_page >= _SPARSE_PAGE_CHAR_THRESHOLD
        result["pages"] = pages
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"extraction failed: {exc}"
        return result
    finally:
        doc.close()
