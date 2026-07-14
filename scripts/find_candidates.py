"""data/annotated/ 전체를 순회하며 조항(5/6/7/8호)별 "기밀도 상승 후보" span을
모아 data/candidates/clause_{N}.jsonl에 저장한다.

사용 예:
    python scripts/find_candidates.py --clause 8   # 조항 하나만
    python scripts/find_candidates.py --clause all # 5/6/7/8 전부
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.candidates import find_candidates  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_ANNOTATED_ROOT = _DATA_ROOT / "annotated"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"

_CLAUSES = ["5", "6", "7", "8"]


def _iter_annotated_docs():
    for path in sorted(_ANNOTATED_ROOT.rglob("*.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def _run_one_clause(clause_no: str) -> None:
    out_path = _CANDIDATES_ROOT / f"clause_{clause_no}.jsonl"
    _CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)

    total_candidates = 0
    docs_with_candidates = 0
    by_source: Counter[str] = Counter()

    with out_path.open("w", encoding="utf-8") as f:
        for _, doc in _iter_annotated_docs():
            candidates = find_candidates(doc, clause_no)
            if not candidates:
                continue
            docs_with_candidates += 1
            total_candidates += len(candidates)
            by_source[doc.get("source", "?")] += len(candidates)
            for c in candidates:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"[{clause_no}호] 후보 {total_candidates}개 / 문서 {docs_with_candidates}개")
    for source, count in by_source.most_common():
        print(f"    {source:8} {count}개")
    print(f"    저장: {out_path.relative_to(_REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clause", choices=[*_CLAUSES, "all"], default="all")
    args = parser.parse_args()

    clauses = _CLAUSES if args.clause == "all" else [args.clause]
    for clause_no in clauses:
        _run_one_clause(clause_no)


if __name__ == "__main__":
    main()
