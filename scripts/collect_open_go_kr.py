"""정보공개포털 O트랙 어댑터 실행 검증 스크립트 (The Assignment 1번째 실행).

실제 사이트에서 문서 몇 건을 수집해 스키마 검증 + 저장까지 end-to-end로 확인한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.open_go_kr import OpenGoKrAdapter  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def main() -> None:
    adapter = OpenGoKrAdapter()

    end = date.today()
    start = end - timedelta(days=29)

    collected = 0
    quarantined = 0
    with DocumentStore(Path(__file__).parent.parent / "rd2.db") as store:
        try:
            for raw_item in adapter.fetch_list(start_date=start, end_date=end, max_items=5):
                try:
                    detail = adapter.parse_detail(raw_item)
                    doc = adapter.to_schema(detail)
                except Exception as exc:  # noqa: BLE001
                    store.quarantine(raw_item, str(exc))
                    quarantined += 1
                    print(f"QUARANTINED: {exc}")
                    continue

                stored = store.upsert(doc)
                collected += 1
                print(f"[{'stored' if stored else 'dup-skip'}] {doc.title!r}")
                print(f"    agency={doc.ordering_agency!r} dept={doc.department!r}")
                print(f"    unit_task={doc.unit_task!r} category={doc.subject_category!r}")
                print(f"    disclosure={doc.disclosure_status.value!r} body_text={doc.body_text!r}")
                print(f"    is_synthetic={doc.is_synthetic!r} url={doc.source_url}")
        finally:
            print()
            print(f"Processed: {collected}, Quarantined: {quarantined}")
            print(f"Total O-track docs in DB: {store.count_documents(cso_classification='O')}")


if __name__ == "__main__":
    main()
