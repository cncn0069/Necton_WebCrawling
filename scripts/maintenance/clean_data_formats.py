"""data/ 를 pdf/hwp/hwpx 정상 파일만 남도록 정리한다.

삭제 대상:
1. pdf/hwp/hwpx가 아닌 모든 포맷 (xls/xlsx/zip/jpg/doc/pptx/mp4/txt/alz/avi/
   rtf/docx/ppt/htm/확장자없음 등)
2. pdf/hwp/hwpx 중 다운로드 실패로 남은 "깨진 스텁 파일"
   (`<html>...파일이 존재하지 않습니다...</html>` — molit.go.kr 서버에서
   원본이 유실된 것으로 확인됨, 재크롤링으로 복구 불가. scripts/scan_broken_files.py
   참고)

기본은 --dry-run 이다. 실제 삭제하려면 --execute를 명시해야 한다 — data/는
.gitignore 대상이라 삭제하면 git으로 복구할 수 없다.

사용 예:
    python scripts/clean_data_formats.py             # 무엇이 지워질지만 미리보기
    python scripts/clean_data_formats.py --execute    # 실제 삭제
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_KEEP_SUFFIXES = {".pdf", ".hwp", ".hwpx"}
_SKIP_NAMES = {".DS_Store"}
_SKIP_SUFFIXES = {".json", ".jsonl"}


def _is_broken_stub(path: Path) -> bool:
    with path.open("rb") as f:
        head = f.read(256).lower()
    return b"<html" in head or b"<!doctype html" in head


def _iter_deletion_targets() -> list[Path]:
    targets = []
    for path in sorted(_DATA_ROOT.rglob("*")):
        if not path.is_file() or path.name in _SKIP_NAMES or path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if _DATA_ROOT / "extracted" in path.parents:
            continue  # 우리가 만든 추출 산출물은 절대 건드리지 않음

        suffix = path.suffix.lower()
        if suffix not in _KEEP_SUFFIXES:
            targets.append(path)
        elif _is_broken_stub(path):
            targets.append(path)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="실제로 삭제 실행 (기본은 미리보기만)")
    args = parser.parse_args()

    targets = _iter_deletion_targets()

    by_ext: Counter[str] = Counter()
    total_size = 0
    for p in targets:
        by_ext[p.suffix.lower() or "(no-ext)"] += 1
        total_size += p.stat().st_size

    print(f"삭제 대상: {len(targets)}개, 총 {total_size / (1024**3):.2f}GB")
    for ext, count in by_ext.most_common():
        print(f"  {ext:10} {count:5}")

    if not args.execute:
        print("\n(미리보기 모드 — 실제 삭제하려면 --execute를 붙여서 다시 실행)")
        return

    deleted = 0
    for p in targets:
        p.unlink()
        deleted += 1
    print(f"\n{deleted}개 파일 삭제 완료.")


if __name__ == "__main__":
    main()
