"""정보공개포털 "원문정보" 어댑터 대량 수집 스크립트.

collect_open_go_kr.py와 동일한 체크포인트/문서타입 상한(cap) 구조 —
차이는 이 게시판은 fetch_list() 단계에서 이미 ORGNAL_YN="Y"(실제 첨부파일
있음)만 골라 넘기고, parse_detail()이 wonmun 체인으로 실제 파일까지
다운로드해서 data/{source}/{doc_type}/에 저장한다는 점이다(TODOS.md
"원문정보(orginlInfoList) 어댑터 + 파일 다운로드 체인" 항목 참고).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.conformance import assert_conformance  # noqa: E402
from rd2.adapters.orginl_info import OriginalInfoAdapter  # noqa: E402
from rd2.schema.models import DisclosureStatus  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def _load_checkpoint(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("processed", 0)
    except (json.JSONDecodeError, OSError):
        return 0


def _save_checkpoint(path: Path, processed: int) -> None:
    path.write_text(json.dumps({"processed": processed}, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("count", type=int, help="수집할 건수")
    parser.add_argument("--start-date", default="2013-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--skip", type=int, default=None)
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--reset-checkpoint", action="store_true")
    parser.add_argument(
        "--doc-type-cap", type=int, default=None,
        help=(
            "doc_type 하나당 최대 수집 건수 — collect_open_go_kr.py의 --doc-type-cap과 "
            "동일한 정책: 공개 문서에만 적용, 비공개/부분공개는 C/S 트랙 시드로 귀해 "
            "상한과 무관하게 항상 수집한다."
        ),
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    checkpoint_path = (
        Path(args.checkpoint) if args.checkpoint else repo_root / "rd2.db.orginl_info_checkpoint.json"
    )

    if args.reset_checkpoint:
        base_skip = args.skip or 0
    elif args.skip is not None:
        base_skip = args.skip
    else:
        base_skip = _load_checkpoint(checkpoint_path)

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    )

    print(f"Checkpoint file: {checkpoint_path}")
    print(f"Starting from position: {base_skip}")
    print(f"Date range: {start_date} ~ {end_date}")

    adapter = OriginalInfoAdapter()

    collected = 0
    quarantined = 0
    cap_skipped = 0
    processed_position = base_skip
    docs = []
    with DocumentStore() as store:
        doc_type_counts = (
            store.count_by_doc_type(adapter.source_name) if args.doc_type_cap is not None else {}
        )
        try:
            for raw_item in adapter.fetch_list(
                start_date=start_date, end_date=end_date, max_items=args.count, skip=base_skip
            ):
                try:
                    detail = adapter.parse_detail(raw_item)
                    doc = adapter.to_schema(detail)
                except Exception as exc:  # noqa: BLE001
                    store.quarantine(raw_item, str(exc))
                    quarantined += 1
                    print(f"QUARANTINED: {exc}")
                    processed_position += 1
                    _save_checkpoint(checkpoint_path, processed_position)
                    continue

                is_capped_bucket = doc.disclosure_status == DisclosureStatus.OPEN
                if (
                    args.doc_type_cap is not None
                    and is_capped_bucket
                    and doc_type_counts.get(doc.doc_type, 0) >= args.doc_type_cap
                ):
                    cap_skipped += 1
                    processed_position += 1
                    _save_checkpoint(checkpoint_path, processed_position)
                    continue

                stored = store.upsert(doc)
                docs.append(doc)
                collected += 1
                if stored and is_capped_bucket:
                    doc_type_counts[doc.doc_type] = doc_type_counts.get(doc.doc_type, 0) + 1
                print(f"[{'stored' if stored else 'dup-skip'}] {doc.title!r}")
                print(f"    agency={doc.ordering_agency!r} dept={doc.department!r}")
                print(f"    disclosure={doc.disclosure_status.value!r} cso={doc.cso_classification.value!r}")
                print(f"    doc_type={doc.doc_type!r} body_file_path={doc.body_file_path!r}")

                processed_position += 1
                _save_checkpoint(checkpoint_path, processed_position)
        finally:
            print()
            print(f"Processed: {collected}, Quarantined: {quarantined}, Cap-skipped: {cap_skipped}")
            print(f"Checkpoint now at position: {processed_position} ({checkpoint_path})")
            print(f"Total O-track docs in DB (전체 소스 합산): {store.count_documents(cso_classification='O')}")
            if args.doc_type_cap is not None:
                print(f"doc_type distribution ({adapter.source_name}): {doc_type_counts}")
            if docs:
                assert_conformance(adapter.source_name, docs)
                print("Conformance: PASS")


if __name__ == "__main__":
    main()
