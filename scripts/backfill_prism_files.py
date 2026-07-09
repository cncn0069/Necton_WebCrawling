"""PRISM 2차 패스 — collect_prism.py --skip-files로 미뤄둔 파일 다운로드를
나중에 별도로 실행한다.

pending_downloads 테이블 자체가 진행 상태다: 성공한 건은 바로 큐에서 빠지고,
실패한 건은 큐에 남아 다음 실행 때 자동으로 재시도된다 — 별도 체크포인트 파일이
필요 없다.
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.prism import PrismAdapter  # noqa: E402
from rd2.storage.db import DocumentStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--count", type=int, default=None,
        help="이번 실행에서 처리할 최대 건수 (생략하면 큐 전체)",
    )
    args = parser.parse_args()

    adapter = PrismAdapter()

    with DocumentStore() as store:
        pending = store.list_pending_downloads("PRISM")
        if args.count is not None:
            pending = pending[: args.count]

        print(f"Pending downloads to process: {len(pending)}")

        done = 0
        failed = 0
        try:
            for entry in pending:
                dedup_key = entry["dedup_key"]
                source_url = entry["source_url"]
                title = store.get_title(dedup_key) or ""
                try:
                    body_file_path, other_file_paths = adapter.download_files_for(source_url, title)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"FAILED (재시도 대상으로 큐에 남음) {source_url}: {exc}")
                    continue

                store.update_files(dedup_key, body_file_path, other_file_paths)
                store.clear_pending_download(dedup_key)
                done += 1
                print(f"[downloaded] {title!r} body_file_path={body_file_path!r} "
                      f"other_file_paths={len(other_file_paths)}개")
        finally:
            print()
            print(f"Done: {done}, Failed: {failed}")
            print(f"Remaining in queue: {len(store.list_pending_downloads('PRISM'))}")


if __name__ == "__main__":
    main()
