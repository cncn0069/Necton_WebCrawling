"""ALIO(공공기관 경영정보 공개시스템) 첨부파일 검색 어댑터 실행 스크립트.

기본 검색어는 "감사결과"(2026-07-09 요청 — 전체 약 5,622건). collect_mohw.py/
collect_prism.py와 동일하게 매 건 처리 직후 진행 위치를 체크포인트 파일에
저장해, 대량 수집 중 끊겨도 다음 실행이 이어서 처리한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.alio import DEFAULT_QUERY, AlioAdapter  # noqa: E402
from rd2.adapters.conformance import assert_conformance  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def _load_checkpoint(path: Path) -> int:
    """마지막으로 완료 처리한 목록 위치(0-based 건수)를 읽는다. 파일이 없으면 0."""
    if not path.exists():
        return 0
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("processed", 0)
    except (json.JSONDecodeError, OSError):
        return 0


def _save_checkpoint(path: Path, processed: int) -> None:
    """매 건 처리 직후 즉시 기록 — 중간에 죽어도 다음 실행이 여기서부터 이어간다."""
    path.write_text(json.dumps({"processed": processed}, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("count", type=int, nargs="?", default=5, help="수집할 건수 (기본: 5)")
    parser.add_argument("--query", default=DEFAULT_QUERY, help=f"검색어 (기본: {DEFAULT_QUERY!r})")
    parser.add_argument("--db", default="rd2.db", help="저장할 DB 파일 경로 (기본: rd2.db)")
    parser.add_argument(
        "--skip", type=int, default=None,
        help="목록 앞에서 건너뛸 건수. 생략하면 체크포인트 파일의 이어할 위치를 사용",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="진행 상황 저장 파일 경로 (기본: <db 경로>.alio_checkpoint.json)",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true",
        help="체크포인트를 무시하고 --skip(기본 0)부터 새로 시작",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    db_path = repo_root / args.db
    checkpoint_path = (
        Path(args.checkpoint) if args.checkpoint else db_path.with_suffix(db_path.suffix + ".alio_checkpoint.json")
    )

    if args.reset_checkpoint:
        base_skip = args.skip or 0
    elif args.skip is not None:
        base_skip = args.skip
    else:
        base_skip = _load_checkpoint(checkpoint_path)

    print(f"Query: {args.query!r}")
    print(f"Checkpoint file: {checkpoint_path}")
    print(f"Starting from position: {base_skip}")

    adapter = AlioAdapter(query=args.query)

    collected = 0
    quarantined = 0
    processed_position = base_skip
    docs = []
    with DocumentStore(db_path) as store:
        try:
            for raw_item in adapter.fetch_list(skip=base_skip, max_items=args.count):
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

                stored = store.upsert(doc)
                docs.append(doc)
                collected += 1
                print(f"[{'stored' if stored else 'dup-skip'}] {doc.title!r}")
                print(f"    agency={doc.ordering_agency!r} production_date={doc.production_date}")
                print(f"    body_file_path={doc.body_file_path!r}")

                processed_position += 1
                _save_checkpoint(checkpoint_path, processed_position)
        finally:
            print()
            print(f"Processed: {collected}, Quarantined: {quarantined}")
            print(f"Checkpoint now at position: {processed_position} ({checkpoint_path})")
            # count_documents는 source가 아니라 cso_classification으로만 필터링한다
            # (storage/db.py) — 이 값은 ALIO만이 아니라 전체 소스의 O트랙 합계다.
            print(f"Total O-track docs in DB (전체 소스 합산): {store.count_documents(cso_classification='O')}")
            if docs:
                assert_conformance(adapter.source_name, docs)
                print("Conformance: PASS")


if __name__ == "__main__":
    main()
