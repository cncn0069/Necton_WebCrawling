"""PRISM O/S트랙 어댑터 실행 검증 스크립트 — 실제 사이트에서 문서 몇 건을
수집해 스키마 검증 + 본문파일 다운로드(공개 문서만) + 저장까지 end-to-end로 확인한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.conformance import assert_conformance  # noqa: E402
from rd2.adapters.prism import PrismAdapter  # noqa: E402
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
    parser.add_argument("count", type=int, default=10, help="수집할 건수 (기본: 10)")
    parser.add_argument(
        "--skip", type=int, default=None,
        help="목록 앞에서 건너뛸 건수. 생략하면 체크포인트 파일의 이어할 위치를 사용",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="진행 상황 저장 파일 경로 (기본: rd2.db.prism_checkpoint.json)",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true",
        help="체크포인트를 무시하고 --skip(기본 0)부터 새로 시작",
    )
    parser.add_argument(
        "--skip-files", action="store_true",
        help=(
            "파일 다운로드(클릭 검증, 건당 최대 수십 초)를 건너뛰고 메타데이터만 "
            "수집한다. 다운로드 대상은 pending_downloads 큐에 등록되며, "
            "scripts/backfill_prism_files.py를 별도로 실행해야 실제로 받아온다."
        ),
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    # 파일명은 SQLite 시절 그대로 유지 — 기존 체크포인트 파일과의 연속성을 위해
    # DB 경로에서 파생시키지 않고 이름을 고정한다.
    checkpoint_path = (
        Path(args.checkpoint) if args.checkpoint else repo_root / "rd2.db.prism_checkpoint.json"
    )

    if args.reset_checkpoint:
        base_skip = args.skip or 0
    elif args.skip is not None:
        base_skip = args.skip
    else:
        base_skip = _load_checkpoint(checkpoint_path)

    print(f"Checkpoint file: {checkpoint_path}")
    print(f"Starting from position: {base_skip}")

    adapter = PrismAdapter()

    collected = 0
    quarantined = 0
    processed_position = base_skip
    docs = []
    with DocumentStore() as store:
        try:
            # skip을 fetch_list에 직접 넘겨 건너뛸 행은 클릭(상세 URL 조회) 자체를
            # 하지 않는다 — 예전엔 max_items=base_skip+count로 받아 파이썬에서
            # 앞부분을 버렸는데, 그 버려지는 행도 fetch_list 안에서 이미 클릭까지
            # 끝난 뒤였다(plan-eng-review 2026-07-08 발견, adapters/prism.py 참고).
            for raw_item in adapter.fetch_list(skip=base_skip, max_items=args.count):
                try:
                    detail = adapter.parse_detail(raw_item, download_files=not args.skip_files)
                    doc = adapter.to_schema(detail)
                except Exception as exc:  # noqa: BLE001
                    store.quarantine(raw_item, str(exc))
                    quarantined += 1
                    print(f"QUARANTINED: {exc}")
                    processed_position += 1
                    _save_checkpoint(checkpoint_path, processed_position)
                    continue

                stored = store.upsert(doc)
                if detail.get("_files_pending"):
                    store.mark_pending_download(doc)
                docs.append(doc)
                collected += 1
                print(f"[{'stored' if stored else 'dup-skip'}] {doc.title!r}")
                print(f"    disclosure={doc.disclosure_status.value!r} cso={doc.cso_classification.value!r} "
                      f"sub_clause={doc.cso_sub_clause!r}")
                if detail.get("_files_pending"):
                    print("    body_file_path=(다운로드 보류 — backfill_prism_files.py 대상)")
                else:
                    print(f"    body_file_path={doc.body_file_path!r}")

                processed_position += 1
                _save_checkpoint(checkpoint_path, processed_position)
        finally:
            print()
            print(f"Processed: {collected}, Quarantined: {quarantined}")
            print(f"Checkpoint now at position: {processed_position} ({checkpoint_path})")
            print(f"Total docs in DB (all sources): {store.count_documents()}")
            if args.skip_files:
                print(f"Pending file downloads (PRISM): {len(store.list_pending_downloads('PRISM'))} "
                      f"— run scripts/backfill_prism_files.py to fetch them")
            if docs:
                assert_conformance("PRISM", docs)
                print("Conformance: PASS")


if __name__ == "__main__":
    main()
