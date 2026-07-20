"""data/ 아래 HWP/HWPX에서 문단 단위 텍스트 span을 직접 추출해
data/extracted/ 아래 파일 1개당 JSON 1개로 저장한다.

`scripts/extract_pdf_text.py`와 거의 동일한 출력 스키마(source_pdf_path/
pages/spans)를 쓴다 — 다만 HWP는 좌표(bbox) 개념이 없어 span에 bbox 필드가
없다. annotate.py/find_candidates.py는 bbox 유무를 보고 알아서 처리 방식을
분기하므로 소스가 PDF인지 HWP인지 이 스크립트가 신경 쓸 필요는 없다.

사용 예:
    python scripts/extract_hwp_text.py --source molit --limit 8   # 파일럿
    python scripts/extract_hwp_text.py --source all                # data/ 아래 출처 폴더 전부
    python scripts/extract_hwp_text.py --hwp data/moe/.../1.hwp --force  # 단일 파일 디버그
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.extraction.hwp_text import extract_hwp_spans, iter_hwp_files  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_LOG_PATH = _EXTRACTED_ROOT / "_hwp_extraction_log.jsonl"

_NON_SOURCE_DIRS = {"extracted", "annotated", "candidates", "augmented"}


def _discover_sources() -> list[str]:
    if not _DATA_ROOT.exists():
        return []
    return sorted(
        p.name
        for p in _DATA_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _NON_SOURCE_DIRS
    )


def _output_path(hwp_path: Path) -> Path:
    rel = hwp_path.relative_to(_DATA_ROOT)  # {source}/{doc_type}/{filename}.hwp(x)
    return _EXTRACTED_ROOT / rel.with_suffix(".json")


def _append_log(entry: dict) -> None:
    _EXTRACTED_ROOT.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _process_one(hwp_path: Path, *, force: bool) -> str:
    """반환값: 'skipped' | 'ok' | 'scanned' | 'error'"""
    out_path = _output_path(hwp_path)
    if out_path.exists() and not force:
        return "skipped"

    result = extract_hwp_spans(hwp_path, data_root=_DATA_ROOT)
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
            "hwp": str(hwp_path.relative_to(_REPO_ROOT)),
            "status": status,
            "error": result.get("error"),
            "num_spans": sum(len(p["spans"]) for p in result.get("pages", [])) if "pages" in result else None,
        }
    )
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="all",
        help='HWP 출처 폴더명(data/{source}/...). "all"이면 data/ 아래 실제 존재하는 '
        "출처 폴더 전부를 자동으로 찾아 처리한다.",
    )
    parser.add_argument("--limit", type=int, default=None, help="소스당 처리할 최대 파일 수 (파일럿용)")
    parser.add_argument("--hwp", type=Path, default=None, help="단일 hwp/hwpx 파일만 처리 (디버그용)")
    parser.add_argument("--force", action="store_true", help="이미 추출된 파일도 재실행")
    args = parser.parse_args()

    if args.hwp:
        status = _process_one(args.hwp.resolve(), force=True)
        print(f"{args.hwp}: {status}")
        return

    sources = _discover_sources() if args.source == "all" else [args.source]
    counts: dict[str, int] = {"skipped": 0, "ok": 0, "scanned": 0, "error": 0}

    for source in sources:
        n = 0
        for hwp_path in iter_hwp_files(_DATA_ROOT, source):
            if args.limit is not None and n >= args.limit:
                break
            status = _process_one(hwp_path, force=args.force)
            counts[status] = counts.get(status, 0) + 1
            n += 1
            print(f"[{source}] {hwp_path.name}: {status}")

    print(f"\n완료: {counts}")


if __name__ == "__main__":
    main()
