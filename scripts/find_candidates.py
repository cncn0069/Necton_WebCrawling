"""data/annotated/ 전체를 순회하며 조항(5/6/7/8호) 또는 행정상태별
"기밀도 상승 후보" span을 모아 data/candidates/*.jsonl에 저장한다.

사용 예:
    python scripts/find_candidates.py --clause 8   # 조항 하나만
    python scripts/find_candidates.py --clause administrative  # 문서유형별 행정상태 후보
    python scripts/find_candidates.py --clause all # 5/6/7/8 + 행정상태 전부
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.candidates import (  # noqa: E402
    find_administrative_candidates,
    find_candidates,
)

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_ANNOTATED_ROOT = _DATA_ROOT / "annotated"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"

_CLAUSES = ["5", "6", "7", "8"]
_ADMINISTRATIVE = "administrative"


def _iter_annotated_docs():
    for path in sorted(_ANNOTATED_ROOT.rglob("*.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def _run_one_target(target: str) -> None:
    is_administrative = target == _ADMINISTRATIVE
    out_path = _CANDIDATES_ROOT / (
        "administrative.jsonl" if is_administrative else f"clause_{target}.jsonl"
    )
    _CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)

    total_candidates = 0
    docs_with_candidates = 0
    by_source: Counter[str] = Counter()

    with out_path.open("w", encoding="utf-8") as f:
        for _, doc in _iter_annotated_docs():
            candidates = (
                find_administrative_candidates(doc)
                if is_administrative
                else find_candidates(doc, target)
            )
            if not candidates:
                continue
            docs_with_candidates += 1
            total_candidates += len(candidates)
            by_source[doc.get("source", "?")] += len(candidates)
            for c in candidates:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    label = "행정상태" if is_administrative else f"{target}호"
    print(f"[{label}] 후보 {total_candidates}개 / 문서 {docs_with_candidates}개")
    for source, count in by_source.most_common():
        print(f"    {source:8} {count}개")
    print(f"    저장: {out_path.relative_to(_REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clause", choices=[*_CLAUSES, _ADMINISTRATIVE, "all"], default="all",
        help="조항 후보 또는 문서유형별 행정상태 후보 선택",
    )
    args = parser.parse_args()

    targets = [*_CLAUSES, _ADMINISTRATIVE] if args.clause == "all" else [args.clause]
    for target in targets:
        _run_one_target(target)


if __name__ == "__main__":
    main()
