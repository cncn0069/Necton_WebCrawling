"""PDF에서 문단/줄 단위로 나뉜 텍스트 span을 추출한다.

2026-07-20: 원래는 위치(bbox)·폰트까지 보존해 나중에 레이아웃을 유지한 채
텍스트를 바꿔치기하는 합성 단계(span 치환 → PDF 재구성)에 쓸 계획이었으나,
그 트랙 자체가 범위에서 빠지면서(AUGMENTATION_STATUS.md 참고) 좌표는 더 이상
필요 없다. 다만 좌표 정보는 여전히 **내부적으로는** 쓴다 — `_merge_wrapped_lines`가
"페이지 폭 때문에 줄바꿈된 문장"과 "표의 서로 다른 셀"을 구분하는 데 bbox가
필요하기 때문. 최종적으로 저장하는 span에는 그 bbox를 남기지 않고 텍스트만 남긴다.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Iterator

import fitz  # PyMuPDF

# 문서 평균 페이지당 추출 글자 수가 이 값 미만이면 텍스트 레이어가 없는
# 스캔본(이미지 PDF) 후보로 표시한다. OCR 적용 여부는 이번 범위 밖 — 표시만 한다.
_SCANNED_AVG_CHARS_PER_PAGE_THRESHOLD = 5

# 같은 블록 안에서 "다음 줄이 현재 줄 바로 아래(세로로 이어짐)"인 경우만 한
# 문장/문단으로 합친다. 표는 같은 블록 안에 한 행의 여러 셀이 "같은 y좌표,
# 다른 x좌표"(가로 나열)로 들어오는 경우가 많아서(실사로 확인됨: 품목명·연도별
# 금액이 한 블록의 서로 다른 줄로 묶여 나옴) 이 조건이면 절대 합치지 않는다.
# 반대로 문장이 페이지 폭 때문에 줄바꿈된 경우(세로쓰기 표 제목도 포함, 한
# 글자씩 세로로 쌓인 경우)는 y좌표가 아래로 내려가며 간격도 좁아서 이 조건에
# 걸린다.
_MERGE_MAX_CHAIN = 40  # 한 문단이 가질 수 있는 최대 줄 수(폭주 방지 안전장치)
_MERGE_GAP_RATIO = 0.8  # 줄 높이 대비 허용되는 다음 줄까지의 세로 간격 비율


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


def extract_pdf_spans(pdf_path: Path, *, data_root: Path) -> dict[str, Any]:
    """PDF 한 개를 열어 페이지별 텍스트 span(문단/줄 단위) 구조를 만든다.

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
                # 아니라 글자 단위로 흩어져서 용량도 커지고 문장으로도 못 쓴다.
                # line 안의 span들을 순서대로 이어 붙여 한 줄을 하나의 span으로
                # 취급한다. bbox는 _merge_wrapped_lines가 줄바꿈 병합 여부를
                # 판단하는 데만 쓰고 최종 결과에는 남기지 않는다.
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
                    block_lines.append({"text": text, "bbox": bbox})

                for entry in _merge_wrapped_lines(block_lines):
                    # bbox/font/size/flags/color는 merge 판단에만 쓰고 최종
                    # 저장 span에는 안 남긴다(재구성 단계가 없어져 필요 없음).
                    page_spans.append(
                        {
                            "span_id": span_id,
                            "text": entry["text"],
                            "length": len(entry["text"]),
                        }
                    )
                    span_id += 1
                    total_chars += len(entry["text"])

            pages.append({"page_no": page_no, "spans": page_spans})

        result["num_pages"] = len(pages)
        avg_chars_per_page = total_chars / len(pages) if pages else 0
        result["has_text_layer"] = avg_chars_per_page >= _SCANNED_AVG_CHARS_PER_PAGE_THRESHOLD
        result["pages"] = pages
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"extraction failed: {exc}"
        return result
    finally:
        doc.close()
