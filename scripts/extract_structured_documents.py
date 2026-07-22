"""PDF/HWP/HWPX를 공통 구조의 JSON으로 추출해 ``data/structured/``에 저장한다.

이 스크립트는 표 추출을 조사할 때만 쓰는 선택적 legacy 도구다. 기본 파이프라인은
본문과 OCR/격리 상태를 canonical v2 ``data/extracted/*.json.gz``에 저장하므로
``structured`` 사본을 생성하지 않는다. 향후 표 결과를 운영 경로에 붙일 때는 본문을
복제하지 않는 ``extraction_id`` 기반 sidecar로 분리한다.

canonical v2 추출기는 후속 단계가 쓰는 physical line/bbox/style을 만든다. 이 도구의
JSON은 그 출력을 대체하지 않으며 본문까지 중복하므로 장기 보관용 sidecar 계약으로
간주하지 않는다.

사용 예::

    python scripts/extract_structured_documents.py --source all
    python scripts/extract_structured_documents.py --source moe --limit 10
    python scripts/extract_structured_documents.py --document data/moe/sample.hwp --force
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.extractors.hwp import extract_hwp  # noqa: E402
from rd2.extractors.pdf import ExtractedTable, extract_pdf  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_STRUCTURED_ROOT = _DATA_ROOT / "structured"
_LOG_PATH = _STRUCTURED_ROOT / "_extraction_log.jsonl"
_SUPPORTED_SUFFIXES = {".pdf", ".hwp", ".hwpx"}
_NON_SOURCE_DIRS = {"extracted", "structured", "annotated", "candidates", "augmented"}
_SCHEMA_VERSION = 1


def _discover_sources() -> list[str]:
    if not _DATA_ROOT.exists():
        return []
    return sorted(
        path.name
        for path in _DATA_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name not in _NON_SOURCE_DIRS
    )


def _iter_documents(source: str):
    source_root = _DATA_ROOT / source
    if not source_root.exists():
        return
    for path in sorted(source_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES:
            yield path


def _output_path(document_path: Path) -> Path:
    rel = document_path.relative_to(_DATA_ROOT)
    # 확장자를 보존해 같은 폴더의 sample.pdf와 sample.hwp가 충돌하지 않게 한다.
    return _STRUCTURED_ROOT / rel.parent / f"{rel.name}.json"


def _relative_source_path(path: Path) -> str:
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _table_payload(table: ExtractedTable) -> dict:
    return {"page_number": table.page_number, "rows": table.rows}


def _base_payload(document_path: Path) -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "source_path": _relative_source_path(document_path),
        "document_format": document_path.suffix.lower().lstrip("."),
    }


def _extract_pdf_payload(document_path: Path) -> dict:
    result = extract_pdf(document_path)
    pages = [
        {
            "page_number": page.page_number,
            "text": page.text,
            "tables": [_table_payload(table) for table in page.tables],
            "needs_ocr": page.needs_ocr,
        }
        for page in result.pages
    ]
    tables = [table for page in pages for table in page["tables"]]
    return {
        **_base_payload(document_path),
        "status": "needs_ocr" if result.needs_ocr else "ok",
        "text": result.full_text,
        "pages": pages,
        "tables": tables,
        "needs_ocr": result.needs_ocr,
        "needs_quarantine": False,
        "tables_truncated": result.tables_truncated,
        "is_encrypted": False,
        "is_valid": True,
        "error": None,
    }


def _extract_hwp_payload(document_path: Path) -> dict:
    result = extract_hwp(document_path)
    return {
        **_base_payload(document_path),
        "status": "quarantine" if result.needs_quarantine else "ok",
        "text": result.text,
        "pages": [],
        "tables": [_table_payload(table) for table in result.tables],
        "needs_ocr": False,
        "needs_quarantine": result.needs_quarantine,
        "tables_truncated": False,
        "is_encrypted": result.is_encrypted,
        "is_valid": result.is_valid,
        "error": None,
    }


def _error_payload(document_path: Path, exc: Exception) -> dict:
    return {
        **_base_payload(document_path),
        "status": "error",
        "text": "",
        "pages": [],
        "tables": [],
        "needs_ocr": False,
        "needs_quarantine": True,
        "tables_truncated": False,
        "is_encrypted": False,
        "is_valid": False,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _append_log(payload: dict) -> None:
    _STRUCTURED_ROOT.mkdir(parents=True, exist_ok=True)
    entry = {
        "source_path": payload["source_path"],
        "document_format": payload["document_format"],
        "status": payload["status"],
        "needs_ocr": payload["needs_ocr"],
        "needs_quarantine": payload["needs_quarantine"],
        "error": payload["error"],
    }
    with _LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _process_one(document_path: Path, *, force: bool) -> str:
    out_path = _output_path(document_path)
    if out_path.exists() and not force:
        return "skipped"

    try:
        if document_path.suffix.lower() == ".pdf":
            payload = _extract_pdf_payload(document_path)
        else:
            payload = _extract_hwp_payload(document_path)
    except Exception as exc:
        payload = _error_payload(document_path, exc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_log(payload)
    return payload["status"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="all",
        help='문서 출처 폴더명(data/{source}/...). "all"이면 출처 폴더를 자동 탐색한다.',
    )
    parser.add_argument("--limit", type=int, default=None, help="소스당 처리할 최대 파일 수")
    parser.add_argument("--document", type=Path, default=None, help="단일 문서만 처리")
    parser.add_argument("--force", action="store_true", help="이미 추출된 문서도 다시 처리")
    args = parser.parse_args()

    if args.document:
        document_path = args.document.resolve()
        if document_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            parser.error("--document는 PDF, HWP 또는 HWPX 파일이어야 합니다")
        status = _process_one(document_path, force=True)
        print(f"{args.document}: {status}")
        return

    sources = _discover_sources() if args.source == "all" else [args.source]
    counts = {"skipped": 0, "ok": 0, "needs_ocr": 0, "quarantine": 0, "error": 0}

    for source in sources:
        for index, document_path in enumerate(_iter_documents(source)):
            if args.limit is not None and index >= args.limit:
                break
            status = _process_one(document_path, force=args.force)
            counts[status] += 1
            print(f"[{source}] {document_path.name}: {status}")

    print(f"\n완료: {counts}")


if __name__ == "__main__":
    main()
