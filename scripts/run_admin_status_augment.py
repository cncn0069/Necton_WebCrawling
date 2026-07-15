"""data/annotated/ 문서를 직접 순회하며 "행정처리"(정보공개법 제9조 1~8호 밖의
절차적 비공개 상태) 문구를 LLM으로 반영하고, 결과를 data/augmented/llm/에
저장한다.

5~8호(run_llm_augment.py)와 달리 정규식 후보탐지 단계가 없다 — 문서 앞부분
span을 통째로 LLM에 보내고 알아서 고르게 한다(이유는
src/rd2/augmentation/administrative_status.py 모듈 docstring 참고).

사용 예:
    python scripts/run_admin_status_augment.py --category pending_disclosure_date --limit 1 --dry-run
    python scripts/run_admin_status_augment.py --category pending_disclosure_date --limit 1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.administrative_status import (  # noqa: E402
    augment_administrative_status,
    build_document_context,
    build_messages,
)
from rd2.augmentation.administrative_status_data import ADMINISTRATIVE_STATUSES  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_ANNOTATED_ROOT = _DATA_ROOT / "annotated"
_AUGMENTED_LLM_ROOT = _DATA_ROOT / "augmented" / "llm"


def _iter_annotated_docs():
    for path in sorted(_ANNOTATED_ROOT.rglob("*.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def _output_path(source_pdf_path: str, category: str) -> Path:
    rel = Path(source_pdf_path).relative_to("data")
    return _AUGMENTED_LLM_ROOT / rel.parent / f"{rel.stem}_admin_{category}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--category", required=True, choices=list(ADMINISTRATIVE_STATUSES))
    parser.add_argument("--limit", type=int, default=1, help="처리할 문서 수")
    parser.add_argument("--dry-run", action="store_true", help="실제 호출 없이 프롬프트만 출력")
    parser.add_argument("--force", action="store_true", help="이미 처리된 문서도 재실행")
    parser.add_argument("--max-spans", type=int, default=50, help="문서당 LLM에 보낼 컨텍스트 span 상한")
    args = parser.parse_args()

    store = None if args.dry_run else DocumentStore()

    processed = 0
    for path, doc in _iter_annotated_docs():
        if processed >= args.limit:
            break

        source_pdf_path = doc.get("source_pdf_path")
        if not source_pdf_path:
            continue

        out_path = _output_path(source_pdf_path, args.category)
        if out_path.exists() and not args.force and not args.dry_run:
            continue

        context = build_document_context(doc, max_spans=args.max_spans)
        if not context:
            continue  # 문서 앞부분에 텍스트 span이 아예 없음(스캔 이미지 표지 등) — 스킵

        print(f"=== {source_pdf_path} (컨텍스트 span {len(context)}개) ===")

        if args.dry_run:
            messages = build_messages(context, args.category)
            for m in messages:
                print(f"--- {m['role']} ---")
                print(m["content"])
            print()
            processed += 1
            continue

        selections = augment_administrative_status(context, args.category)
        if not selections:
            print("  (LLM이 아무것도 선택하지 않음 — 스킵)\n")
            processed += 1
            continue

        for s in selections:
            print(f"  span_id={s['span_id']} page={s['page_no']} [{s['transformation']}]")
            print(f"    원문: {s['original']!r}")
            print(f"    치환: {s['synthetic']!r}")
            print(f"    근거: {s['reason']}")

        body_file_path = str(Path(source_pdf_path).relative_to("data"))
        origin_document = store.get_by_body_file_path(body_file_path) if store else None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "source_pdf_path": source_pdf_path,
                    "source": doc.get("source"),
                    "doc_type": doc.get("doc_type"),
                    "doc_id": doc.get("doc_id"),
                    "category": args.category,
                    "origin_document": origin_document,
                    "selections": selections,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  저장: {out_path.relative_to(_REPO_ROOT)}\n")
        processed += 1

    if store:
        store.close()

    print(f"완료: {processed}개 문서 처리")


if __name__ == "__main__":
    main()
