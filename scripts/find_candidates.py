"""data/annotated/ 전체를 순회하며 조항(5/6/7/8호) 또는 행정상태별
"기밀도 상승 후보" span을 모아 data/candidates/*.jsonl에 저장한다.

사용 예:
    python scripts/find_candidates.py --clause 8   # 조항 하나만
    python scripts/find_candidates.py --clause administrative  # 문서유형별 행정상태 후보
    python scripts/find_candidates.py --clause all # 5/6/7/8 + 행정상태 전부
    python scripts/find_candidates.py --cells      # (조항,세부조항,문서유형,행정상태) 셀별 측정
                                                    # (data/candidates/*.jsonl이 이미 있어야 함)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.candidates import (  # noqa: E402
    find_administrative_candidates,
    find_candidates,
)
from rd2.generators.template_matrix import _GROUPS, infer_subclause_key  # noqa: E402

try:
    # Lane B가 별도 워크스트림에서 _ADMIN_STATUS_RULES_BY_DOC_TYPE(private)를
    # ADMIN_STATUS_RULES_BY_DOC_TYPE(public)로 리네임 중이다. 리네임이 이미
    # 반영된 rd2.augmentation.candidates를 임포트한 경우를 우선 시도한다.
    from rd2.augmentation.candidates import ADMIN_STATUS_RULES_BY_DOC_TYPE
except ImportError:  # 아직 리네임이 반영되지 않은 경우 기존 private 이름으로 폴백
    from rd2.augmentation.candidates import (
        _ADMIN_STATUS_RULES_BY_DOC_TYPE as ADMIN_STATUS_RULES_BY_DOC_TYPE,
    )

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_ANNOTATED_ROOT = _DATA_ROOT / "annotated"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"

_CLAUSES = ["5", "6", "7", "8"]
_ADMINISTRATIVE = "administrative"

# 셀 측정 결과의 3가지 상태(design doc "Phase A" 참고).
NO_RULE_DEFINED = "no_rule_defined"
RULE_DEFINED_ZERO_MATCHES = "rule_defined_zero_matches"
COUNTED = "counted"


def _iter_annotated_docs(root: Path = _ANNOTATED_ROOT):
    for path in sorted(root.rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            print(f"[경고] 주석 파일 파싱 실패, 건너뜀: {path} ({exc})")
            continue
        yield path, doc


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


def _doc_key(record: dict[str, Any]) -> tuple[Any, Any]:
    """후보 span 레코드가 같은 원본 문서에서 나왔는지 판별할 키.

    doc_id가 있으면 (source, doc_id)를, 없으면 (source, source_pdf_path)를 쓴다.
    """
    return (record.get("source"), record.get("doc_id") or record.get("source_pdf_path"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_clause_candidates(clause_no: str, candidates_root: Path = _CANDIDATES_ROOT) -> list[dict[str, Any]]:
    return _load_jsonl(candidates_root / f"clause_{clause_no}.jsonl")


def _load_administrative_candidates(candidates_root: Path = _CANDIDATES_ROOT) -> list[dict[str, Any]]:
    return _load_jsonl(candidates_root / "administrative.jsonl")


def measure_cells(candidates_root: Path = _CANDIDATES_ROOT) -> dict[tuple[str, str, str], dict[str, Any]]:
    """(clause_no, subclause_key, doc_type) 삼중쌍마다 행정상태 축까지 곱한
    셀 단위 후보 수를 집계한다.

    행정상태는 독립 축이 아니라 doc_type에 종속된다(설계 문서 "조합 규모" 절
    참고) — 삼중쌍마다 그 doc_type에 등록된 상태 목록만 곱해서 셀을 만든다.
    각 삼중쌍은 다음 3가지 상태 중 하나로 분류된다:

    - ``no_rule_defined``: 이 doc_type이 ADMIN_STATUS_RULES_BY_DOC_TYPE에
      아예 없음(규칙 자체가 없는 저비용 갭).
    - ``rule_defined_zero_matches``: 규칙은 있지만 이 (조항,세부조항,문서유형)
      조합에서 실제로 매치된 문서가 0건(영구 0건일 수도 있는, 다른 종류의 문제).
    - ``counted``: 규칙도 있고 실제 매치도 있음 — 행정상태 라벨별 문서 수를 반환.
    """
    admin_candidates = _load_administrative_candidates(candidates_root)
    admin_status_by_doc: dict[tuple[Any, Any], set[str]] = defaultdict(set)
    for c in admin_candidates:
        admin_status_by_doc[_doc_key(c)].add(c["document_status"])

    clause_candidates_cache: dict[str, list[dict[str, Any]]] = {
        clause_no: _load_clause_candidates(clause_no, candidates_root) for clause_no in _CLAUSES
    }

    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for clause_no, groups in _GROUPS.items():
        if clause_no not in _CLAUSES:
            continue
        clause_candidates = clause_candidates_cache[clause_no]
        for subclause_key, _label, doc_types in groups:
            for doc_type in doc_types:
                cell_key = (clause_no, subclause_key, doc_type)

                matching_docs: set[tuple[Any, Any]] = set()
                span_candidates = 0
                for c in clause_candidates:
                    if c.get("doc_type") != doc_type:
                        continue
                    inferred = infer_subclause_key(clause_no, doc_type, keyword_text=c.get("text", ""))
                    if inferred != subclause_key:
                        continue
                    span_candidates += 1
                    matching_docs.add(_doc_key(c))

                rules = ADMIN_STATUS_RULES_BY_DOC_TYPE.get(doc_type)
                if not rules:
                    cells[cell_key] = {
                        "state": NO_RULE_DEFINED,
                        "span_candidates": span_candidates,
                        "matching_docs": len(matching_docs),
                    }
                    continue

                status_counts: Counter[str] = Counter()
                for doc_key in matching_docs:
                    for status in admin_status_by_doc.get(doc_key, ()):
                        status_counts[status] += 1

                if not status_counts:
                    cells[cell_key] = {
                        "state": RULE_DEFINED_ZERO_MATCHES,
                        "span_candidates": span_candidates,
                        "matching_docs": len(matching_docs),
                    }
                else:
                    cells[cell_key] = {
                        "state": COUNTED,
                        "span_candidates": span_candidates,
                        "matching_docs": len(matching_docs),
                        "by_admin_status": dict(status_counts),
                    }

    return cells


def _print_cell_report(cells: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    by_state: Counter[str] = Counter(cell["state"] for cell in cells.values())
    print(f"셀(조항,세부조항,문서유형) 총 {len(cells)}개")
    print(f"  no_rule_defined         : {by_state[NO_RULE_DEFINED]}개 (행정상태 규칙 자체가 없음)")
    print(f"  rule_defined_zero_matches: {by_state[RULE_DEFINED_ZERO_MATCHES]}개 (규칙은 있으나 매치 0건)")
    print(f"  counted                 : {by_state[COUNTED]}개 (실제 매치 있음)")
    print()
    for (clause_no, subclause_key, doc_type), cell in sorted(cells.items()):
        prefix = f"  [{clause_no}호/{subclause_key}/{doc_type}]"
        if cell["state"] == NO_RULE_DEFINED:
            print(f"{prefix} span후보={cell['span_candidates']} → no_rule_defined")
        elif cell["state"] == RULE_DEFINED_ZERO_MATCHES:
            print(f"{prefix} span후보={cell['span_candidates']} → rule_defined_zero_matches")
        else:
            print(f"{prefix} span후보={cell['span_candidates']} → {cell['by_admin_status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clause", choices=[*_CLAUSES, _ADMINISTRATIVE, "all"], default="all",
        help="조항 후보 또는 문서유형별 행정상태 후보 선택",
    )
    parser.add_argument(
        "--cells", action="store_true",
        help="(조항,세부조항,문서유형,행정상태) 셀별 측정 리포트 출력. "
             "data/candidates/*.jsonl이 이미 생성돼 있어야 함(먼저 --clause all 실행 필요).",
    )
    args = parser.parse_args()

    if args.cells:
        _print_cell_report(measure_cells())
        return

    targets = [*_CLAUSES, _ADMINISTRATIVE] if args.clause == "all" else [args.clause]
    for target in targets:
        _run_one_target(target)


if __name__ == "__main__":
    main()
