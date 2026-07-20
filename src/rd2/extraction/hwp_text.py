"""HWP/HWPX에서 문단 단위 텍스트 span을 직접 추출한다(LibreOffice 없이).

`.hwpx`(zip+XML, OWPML 포맷)는 표준 라이브러리 zipfile+ElementTree로 직접
파싱한다 — 실제 샘플 파일로 구조를 확인함(`Contents/section{N}.xml`, 네임스페이스
`http://www.hancom.co.kr/hwpml/2011/paragraph`의 `<hp:p>` 문단 안에 `<hp:t>`
텍스트 런들이 있음).

`.hwp`(바이너리 v5)는 직접 파싱하는 순수 파이썬 파서가 마땅치 않아 `pyhwp`
패키지가 제공하는 CLI를 서브프로세스로 부른다. 두 CLI를 실사로 비교한 결과
(2026-07-20):
- `hwp5odt`(HWP→ODT 변환 후 파싱): 표 안 내용까지 제대로 뽑히지만, 표가
  극단적으로 많은 문서 일부에서 응답없음(2분 이상)에 빠지거나 내부 검증
  오류(RelaxNG)로 실패하는 사례를 실측함.
- `hwp5txt`(HWP→평문 변환): 항상 빠르지만 표 안 내용을 통째로 누락시킨다
  (표 자리에 "<표>" placeholder만 남음) — 이 코퍼스는 표 위주 문서가 많아
  단독으로는 데이터 손실이 큼.

그래서 `hwp5odt`를 주력으로 쓰고, 타임아웃/실패 시에만 `hwp5txt`로 폴백한다
— 최선의 품질을 기본으로 하되, 소수의 병적인 파일 때문에 그 파일이 통째로
빠지는 것보다는 "표 내용은 없어도 나머지 텍스트는 건진다"는 차선을 택한다.
`pdf_text.extract_pdf_spans`와 동일한 관례 — 예외를 던지지 않고 실패 시
"error" 필드가 있는 dict를 반환한다.
"""

from __future__ import annotations

import datetime
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Iterator

import hwp5

_HWP_SUFFIXES = (".hwp", ".hwpx")
_SCANNED_AVG_CHARS_THRESHOLD = 5  # pdf_text.py의 스캔본 판정과 동일한 기준

_HWPX_PARAGRAPH_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HWPX_PARAGRAPH_TAG = f"{{{_HWPX_PARAGRAPH_NS}}}p"
_HWPX_TEXT_TAG = f"{{{_HWPX_PARAGRAPH_NS}}}t"

_ODT_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODT_PARAGRAPH_TAG = f"{{{_ODT_TEXT_NS}}}p"

_ODT_TIMEOUT = 60
_TXT_TIMEOUT = 30


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


def _paragraphs_to_spans(texts: list[str]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for span_id, text in enumerate(t for t in texts if t.strip()):
        spans.append({"span_id": span_id, "text": text, "length": len(text)})
    return spans


def _extract_hwpx_paragraphs(hwpx_path: Path) -> list[str]:
    """zip 안 Contents/section*.xml을 순서대로 파싱해 문단 텍스트 리스트를 만든다."""
    texts: list[str] = []
    with zipfile.ZipFile(hwpx_path) as z:
        section_names = sorted(n for n in z.namelist() if n.startswith("Contents/section") and n.endswith(".xml"))
        for name in section_names:
            root = ET.fromstring(z.read(name))
            for para in root.iter(_HWPX_PARAGRAPH_TAG):
                para_text = "".join(t.text or "" for t in para.iter(_HWPX_TEXT_TAG))
                if para_text:
                    texts.append(para_text)
    return texts


def _run_hwp5_cli(
    entry_point: str, hwp_path: Path, out_path: Path, *, timeout: int, extra_args: list[str] | None = None
) -> dict[str, Any]:
    proc = subprocess.Popen(
        [entry_point, *(extra_args or []), "--output", str(out_path), str(hwp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        return {"ok": False, "error": f"{entry_point} timed out after {timeout}s"}
    if proc.returncode != 0 or not out_path.exists():
        return {"ok": False, "error": f"{entry_point} failed (returncode={proc.returncode}): {(stdout or '').strip()[:500]}"}
    return {"ok": True}


def _extract_via_odt(hwp_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rd2-hwp5odt-") as tmp_dir:
        out_path = Path(tmp_dir) / "content.xml"
        result = _run_hwp5_cli(
            "hwp5odt", hwp_path, out_path, timeout=_ODT_TIMEOUT, extra_args=["--content", "--no-embed-image"]
        )
        if not result["ok"]:
            return result
        try:
            root = ET.fromstring(out_path.read_bytes())
        except ET.ParseError as exc:
            return {"ok": False, "error": f"content.xml parse failed: {exc}"}
        texts = ["".join(p.itertext()) for p in root.iter(_ODT_PARAGRAPH_TAG)]
        return {"ok": True, "extractor": "pyhwp-odt", "texts": texts}


def _extract_via_txt(hwp_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rd2-hwp5txt-") as tmp_dir:
        out_path = Path(tmp_dir) / "out.txt"
        result = _run_hwp5_cli("hwp5txt", hwp_path, out_path, timeout=_TXT_TIMEOUT)
        if not result["ok"]:
            return result
        texts = out_path.read_text(encoding="utf-8").splitlines()
        return {"ok": True, "extractor": "pyhwp-txt", "texts": texts}


def extract_hwp_spans(hwp_path: Path, *, data_root: Path) -> dict[str, Any]:
    """HWP/HWPX 1개를 열어 문단 단위 텍스트 span 구조를 만든다.

    실패해도 예외를 던지지 않고 "error" 필드가 있는 dict를 반환한다
    (`pdf_text.extract_pdf_spans`와 동일한 관례). HWP는 렌더링 전 포맷이라
    PDF처럼 실제 "페이지" 개념이 없다 — 문서 전체를 `page_no=1` 가짜 페이지
    하나에 담는다(annotate.py의 반복-헤더 탐지는 최소 3페이지가 필요해서 HWP
    문서에는 자연히 적용되지 않는다 — 버그가 아니라 이 포맷의 한계).
    """
    source, doc_type = _source_and_doc_type(hwp_path, data_root)
    doc_id, title = _parse_doc_id_and_title(hwp_path)
    result: dict[str, Any] = {
        "source": source,
        "doc_type": doc_type,
        "doc_id": doc_id,
        "file_name": hwp_path.name,
        "source_pdf_path": str(hwp_path.relative_to(data_root.parent)),
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "extractor_version": f"pyhwp-{hwp5.__version__}",
    }

    suffix = hwp_path.suffix.lower()
    if suffix == ".hwpx":
        try:
            texts = _extract_hwpx_paragraphs(hwp_path)
        except (zipfile.BadZipFile, ET.ParseError) as exc:
            result["error"] = f"hwpx parse failed: {exc}"
            return result
        result["extractor"] = "hwpx-xml"
    elif suffix == ".hwp":
        odt_result = _extract_via_odt(hwp_path)
        if odt_result["ok"]:
            texts = odt_result["texts"]
            result["extractor"] = odt_result["extractor"]
        else:
            txt_result = _extract_via_txt(hwp_path)
            if not txt_result["ok"]:
                result["error"] = f"both hwp5odt and hwp5txt failed — odt: {odt_result['error']} / txt: {txt_result['error']}"
                return result
            texts = txt_result["texts"]
            result["extractor"] = txt_result["extractor"]
            result["fallback_reason"] = odt_result["error"]
    else:
        result["error"] = f"unsupported extension: {suffix}"
        return result

    spans = _paragraphs_to_spans(texts)
    total_chars = sum(s["length"] for s in spans)
    result["num_pages"] = 1
    result["has_text_layer"] = total_chars >= _SCANNED_AVG_CHARS_THRESHOLD
    result["pages"] = [{"page_no": 1, "spans": spans}]
    return result
