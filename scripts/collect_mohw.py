"""보건복지부 입찰안내 게시판 어댑터 실행 검증 스크립트 (The Assignment 실행).

실제 사이트에서 문서 몇 건을 수집해 스키마 검증 + 첨부파일 다운로드 + 저장까지
end-to-end로 확인한다. collect_open_go_kr.py/collect_prism.py와 동일한 구조.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.conformance import assert_conformance  # noqa: E402
from rd2.adapters.mohw import MohwAdapter  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("count", type=int, nargs="?", default=5, help="수집할 건수 (기본: 5)")
    parser.add_argument("--db", default="rd2.db", help="저장할 DB 파일 경로 (기본: rd2.db)")
    parser.add_argument("--skip", type=int, default=0, help="목록 앞에서 건너뛸 건수")
    args = parser.parse_args()

    adapter = MohwAdapter()

    collected = 0
    quarantined = 0
    docs = []
    with DocumentStore(Path(__file__).parent.parent / args.db) as store:
        try:
            for raw_item in adapter.fetch_list(skip=args.skip, max_items=args.count):
                try:
                    detail = adapter.parse_detail(raw_item)
                    doc = adapter.to_schema(detail)
                except Exception as exc:  # noqa: BLE001
                    store.quarantine(raw_item, str(exc))
                    quarantined += 1
                    print(f"QUARANTINED: {exc}")
                    continue

                stored = store.upsert(doc)
                docs.append(doc)
                collected += 1
                print(f"[{'stored' if stored else 'dup-skip'}] {doc.title!r}")
                print(f"    agency={doc.ordering_agency!r} dept={doc.department!r} doc_type={doc.doc_type!r}")
                print(f"    period={doc.start_date}~{doc.end_date} url={doc.source_url}")
                print(f"    body_file_path={doc.body_file_path!r}")
        finally:
            print()
            print(f"Processed: {collected}, Quarantined: {quarantined}")
            # count_documents는 source가 아니라 cso_classification으로만 필터링한다
            # (storage/db.py) — 이 값은 보건복지부만이 아니라 전체 소스의 O트랙 합계다.
            print(f"Total O-track docs in DB (전체 소스 합산): {store.count_documents(cso_classification='O')}")
            if docs:
                assert_conformance("보건복지부", docs)
                print("Conformance: PASS")


if __name__ == "__main__":
    main()
