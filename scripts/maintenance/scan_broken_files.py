"""data/ 전체(모든 확장자)를 순회하며 "깨진 스텁 파일"을 찾아 리포트한다.

일부 첨부파일은 실제로는 다운로드 실패로 남은 `<html>...파일이 존재하지
않습니다...</html>` 에러 페이지인데 원래 확장자(.pdf/.hwp/.doc/.zip/.jpg 등)
그대로 저장되어 있다 (molit.go.kr 서버에서 오래된 첨부파일 실물이 유실된
경우로 확인됨 — 재크롤링으로 복구 불가). 이 스크립트는 그 목록을 뽑아낸다.

사용 예:
    python scripts/scan_broken_files.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_REPORT_PATH = _DATA_ROOT / "extracted" / "_broken_files_report.jsonl"

_SKIP_NAMES = {".DS_Store"}
_SKIP_SUFFIXES = {".json", ".jsonl"}


def _is_broken_stub(path: Path) -> bool:
    with path.open("rb") as f:
        head = f.read(256).lower()
    return b"<html" in head or b"<!doctype html" in head


def main() -> None:
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    broken_counts: Counter[tuple[str, str]] = Counter()
    total_counts: Counter[tuple[str, str]] = Counter()

    with _REPORT_PATH.open("w", encoding="utf-8") as report:
        for path in sorted(_DATA_ROOT.rglob("*")):
            if not path.is_file() or path.name in _SKIP_NAMES or path.suffix.lower() in _SKIP_SUFFIXES:
                continue
            if _DATA_ROOT / "extracted" in path.parents:
                continue  # 우리가 만든 산출물은 스캔 대상 아님

            rel = path.relative_to(_DATA_ROOT)
            source = rel.parts[0] if rel.parts else "?"
            ext = path.suffix.lower().lstrip(".") or "(no-ext)"
            total_counts[(source, ext)] += 1

            if _is_broken_stub(path):
                broken_counts[(source, ext)] += 1
                report.write(
                    json.dumps(
                        {
                            "path": str(rel),
                            "source": source,
                            "ext": ext,
                            "size_bytes": path.stat().st_size,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    print(f"{'source':8} {'ext':10} {'broken':>8} / {'total':>8}")
    for key in sorted(total_counts, key=lambda k: -broken_counts[k]):
        b = broken_counts[key]
        t = total_counts[key]
        if b > 0:
            print(f"{key[0]:8} {key[1]:10} {b:8} / {t:8}")

    print(f"\n총 깨진 스텁 파일: {sum(broken_counts.values())} / 전체 {sum(total_counts.values())}")
    print(f"리포트 저장: {_REPORT_PATH.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    main()
