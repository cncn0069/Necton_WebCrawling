"""data/ 아래 PDF에서 위치정보(bbox·폰트·길이) 포함 텍스트 span을 추출해
data/extracted/ 아래 PDF 1개당 JSON 1개로 저장한다.

이후 기밀도 상승 span 합성 단계가 이 JSON을 입력으로 받아 "이 span을 어떤
문구로, 원본 레이아웃을 깨지 않고 바꿔치기할지"를 결정한다 — 이 스크립트는
그 준비 단계인 추출만 담당한다.

사용 예:
    python scripts/extract_pdf_text.py --source moe --limit 8   # 파일럿
    python scripts/extract_pdf_text.py --source all              # data/ 아래 출처 폴더 전부
    python scripts/extract_pdf_text.py --pdf data/moe/.../1.pdf --force  # 단일 파일 디버그

--source는 고정된 목록이 아니다 — data/{아무_폴더명}/ 아래 PDF를 넣고 그 폴더명을
그대로 --source에 주면 된다(예: 새 지자체 문서라면 data/new_agency/에 넣고
--source new_agency).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.extraction.pdf_text import extract_pdf_spans, iter_pdf_files  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_LOG_PATH = _EXTRACTED_ROOT / "_extraction_log.jsonl"

# data/ 바로 아래에 있지만 실제 PDF 출처 폴더가 아니라 파이프라인 산출물이 쌓이는
# 디렉터리 — "--source all"로 전체를 훑을 때 이런 폴더까지 "출처"로 오인해 도는 걸
# 막는다(실측 2026-07-16으로 지자체·타부처 등 출처가 4개로 고정될 수 없다는 게
# 확인돼, 하드코딩된 소스 목록 대신 data/ 아래 실제 존재하는 폴더를 동적으로 찾음).
_NON_SOURCE_DIRS = {"extracted", "structured", "annotated", "candidates", "augmented"}


def _discover_sources() -> list[str]:
    if not _DATA_ROOT.exists():
        return []
    return sorted(
        p.name
        for p in _DATA_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _NON_SOURCE_DIRS
    )


def _output_path(pdf_path: Path) -> Path:
    rel = pdf_path.relative_to(_DATA_ROOT)  # {source}/{doc_type}/{filename}.pdf
    return _EXTRACTED_ROOT / rel.with_suffix(".json")


def _append_log(entry: dict) -> None:
    _EXTRACTED_ROOT.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _process_one(pdf_path: Path, *, force: bool) -> str:
    """반환값: 'skipped' | 'ok' | 'scanned' | 'error'"""
    out_path = _output_path(pdf_path)
    if out_path.exists() and not force:
        return "skipped"

    result = extract_pdf_spans(pdf_path, data_root=_DATA_ROOT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if "error" in result:
        status = "error"
    elif not result.get("has_text_layer", True):
        status = "scanned"
    else:
        status = "ok"

    _append_log(
        {
            "pdf": str(pdf_path.relative_to(_REPO_ROOT)),
            "status": status,
            "error": result.get("error"),
            "num_pages": result.get("num_pages"),
            "num_spans": sum(len(p["spans"]) for p in result.get("pages", [])) if "pages" in result else None,
        }
    )
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="all",
        help='PDF 출처 폴더명(data/{source}/...). "all"이면 data/ 아래 실제 존재하는 '
        "출처 폴더 전부를 자동으로 찾아 처리한다(고정 목록 아님 — 새 부처/지자체 "
        "폴더를 추가해도 코드 수정 없이 바로 인식됨).",
    )
    parser.add_argument("--limit", type=int, default=None, help="소스당 처리할 최대 파일 수 (파일럿용)")
    parser.add_argument("--pdf", type=Path, default=None, help="단일 PDF 파일만 처리 (디버그용)")
    parser.add_argument("--force", action="store_true", help="이미 추출된 파일도 재실행")
    args = parser.parse_args()

    if args.pdf:
        status = _process_one(args.pdf.resolve(), force=True)
        print(f"{args.pdf}: {status}")
        return

    sources = _discover_sources() if args.source == "all" else [args.source]
    counts = {"skipped": 0, "ok": 0, "scanned": 0, "error": 0}

    for source in sources:
        n = 0
        for pdf_path in iter_pdf_files(_DATA_ROOT, source):
            if args.limit is not None and n >= args.limit:
                break
            status = _process_one(pdf_path, force=args.force)
            counts[status] += 1
            n += 1
            print(f"[{source}] {pdf_path.name}: {status}")

    print(f"\n완료: {counts}")


if __name__ == "__main__":
    main()
