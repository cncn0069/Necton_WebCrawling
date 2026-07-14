"""data/extracted/ 의 추출 결과에 `is_boilerplate`/`cleaned_text` 주석만
얹은 사본을 data/annotated/ 에 만든다. 원본 span은 지우거나 바꾸지 않는다.

사용 예:
    python scripts/annotate_documents.py --source moe --limit 8   # 파일럿
    python scripts/annotate_documents.py --source all              # 전체
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.annotate import annotate_document  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_ANNOTATED_ROOT = _DATA_ROOT / "annotated"

_SOURCES = ["molit", "mohw", "moe", "PRISM"]


def _iter_extracted_files(source: str | None):
    base = _EXTRACTED_ROOT / source if source else _EXTRACTED_ROOT
    if not base.exists():
        return
    for path in sorted(base.rglob("*.json")):
        yield path


def _output_path(extracted_path: Path) -> Path:
    rel = extracted_path.relative_to(_EXTRACTED_ROOT)
    return _ANNOTATED_ROOT / rel


def _process_one(extracted_path: Path, *, force: bool) -> str:
    """반환값: 'skipped' | 'ok'"""
    out_path = _output_path(extracted_path)
    if out_path.exists() and not force:
        return "skipped"

    extracted_doc = json.loads(extracted_path.read_text(encoding="utf-8"))
    annotated_doc = annotate_document(extracted_doc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(annotated_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return "ok"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=[*_SOURCES, "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="소스당 처리할 최대 파일 수 (파일럿용)")
    parser.add_argument("--force", action="store_true", help="이미 처리된 파일도 재실행")
    args = parser.parse_args()

    sources = _SOURCES if args.source == "all" else [args.source]
    counts = {"skipped": 0, "ok": 0}

    for source in sources:
        n = 0
        for extracted_path in _iter_extracted_files(source):
            if extracted_path.name.startswith("_"):  # _extraction_log.jsonl 등 제외
                continue
            if args.limit is not None and n >= args.limit:
                break
            status = _process_one(extracted_path, force=args.force)
            counts[status] += 1
            n += 1
            print(f"[{source}] {extracted_path.name}: {status}")

    print(f"\n완료: {counts}")


if __name__ == "__main__":
    main()
