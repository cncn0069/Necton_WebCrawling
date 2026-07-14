"""data/candidates/clause_{N}.jsonl의 후보를 문서 단위로 묶어 LLM에 보내고,
치환 결과를 data/augmented/llm/에 저장한다 (Hard-example 증강).

사용 예:
    python scripts/run_llm_augment.py --clause 8 --limit 1 --dry-run  # 프롬프트만 확인
    python scripts/run_llm_augment.py --clause 8 --limit 1            # 실제 호출 1건
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.llm_augment import augment_document, build_messages  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"
_AUGMENTED_LLM_ROOT = _DATA_ROOT / "augmented" / "llm"


def _load_candidates_by_doc(clause_no: str) -> dict[str, list[dict]]:
    path = _CANDIDATES_ROOT / f"clause_{clause_no}.jsonl"
    by_doc: dict[str, list[dict]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            by_doc[c["source_pdf_path"]].append(c)
    return by_doc


def _output_path(source_pdf_path: str, clause_no: str) -> Path:
    # source_pdf_path 예: "data/molit/policy_material/4623_....pdf"
    rel = Path(source_pdf_path).relative_to("data")
    return _AUGMENTED_LLM_ROOT / rel.parent / f"{rel.stem}_clause{clause_no}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clause", required=True, choices=["5", "6", "7", "8"])
    parser.add_argument("--limit", type=int, default=1, help="처리할 문서 수")
    parser.add_argument("--dry-run", action="store_true", help="실제 호출 없이 프롬프트만 출력")
    parser.add_argument("--force", action="store_true", help="이미 처리된 문서도 재실행")
    args = parser.parse_args()

    by_doc = _load_candidates_by_doc(args.clause)
    print(f"[{args.clause}호] 후보 있는 문서 {len(by_doc)}개 중 최대 {args.limit}개 처리\n")

    processed = 0
    for source_pdf_path, candidates in by_doc.items():
        if processed >= args.limit:
            break

        out_path = _output_path(source_pdf_path, args.clause)
        if out_path.exists() and not args.force and not args.dry_run:
            continue

        print(f"=== {source_pdf_path} (후보 {len(candidates)}개) ===")

        if args.dry_run:
            messages = build_messages(candidates, args.clause)
            for m in messages:
                print(f"--- {m['role']} ---")
                print(m["content"])
            print()
            processed += 1
            continue

        selections = augment_document(candidates, args.clause)
        if not selections:
            print("  (LLM이 아무것도 선택하지 않음 — 스킵)\n")
            processed += 1
            continue

        for s in selections:
            orig_len, syn_len = len(s["original"]), len(s["synthetic"])
            print(f"  span_id={s['span_id']} [{s['transformation']}]")
            print(f"    원문({orig_len}자): {s['original']!r}")
            print(f"    치환({syn_len}자): {s['synthetic']!r}")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "source_pdf_path": source_pdf_path,
                    "source": candidates[0]["source"],
                    "doc_type": candidates[0]["doc_type"],
                    "doc_id": candidates[0]["doc_id"],
                    "clause_no": args.clause,
                    "selections": selections,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  저장: {out_path.relative_to(_REPO_ROOT)}\n")
        processed += 1

    print(f"완료: {processed}개 문서 처리")


if __name__ == "__main__":
    main()
