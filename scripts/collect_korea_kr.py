"""대한민국 정책브리핑(korea.kr) 보도자료 어댑터 실행 검증 스크립트.

실제 사이트에서 문서 몇 건을 수집해 스키마 검증 + 첨부파일 다운로드 + 저장까지
end-to-end로 확인한다. collect_moe.py와 동일한 구조 — 이 소스도 헤드리스 브라우저
없이 순수 httpx로 동작하고 클릭 검증이 필요 없어 체크포인트는 있지만 skip 기반
재개만으로 충분하다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters.conformance import assert_conformance  # noqa: E402
from rd2.adapters.korea_kr import KoreaKrAdapter  # noqa: E402
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
    parser.add_argument(
        "--skip", type=int, default=None,
        help="목록 앞에서 건너뛸 건수. 생략하면 체크포인트 파일의 이어할 위치를 사용",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="진행 상황 저장 파일 경로 (기본: rd2.db.korea_kr_checkpoint.json)",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true",
        help="체크포인트를 무시하고 --skip(기본 0)부터 새로 시작",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    checkpoint_path = (
        Path(args.checkpoint) if args.checkpoint else repo_root / "rd2.db.korea_kr_checkpoint.json"
    )

    if args.reset_checkpoint:
        base_skip = args.skip or 0
    elif args.skip is not None:
        base_skip = args.skip
    else:
        base_skip = _load_checkpoint(checkpoint_path)

    print(f"Checkpoint file: {checkpoint_path}")
    print(f"Starting from position: {base_skip}")

    adapter = KoreaKrAdapter()

    collected = 0
    quarantined = 0
    processed_position = base_skip
    docs = []
    with DocumentStore() as store:
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
                print(f"    agency={doc.ordering_agency!r}")
                print(f"    body_file_path={doc.body_file_path!r} other_files={len(doc.other_file_paths)}")

                processed_position += 1
                _save_checkpoint(checkpoint_path, processed_position)
        finally:
            print()
            print(f"Processed: {collected}, Quarantined: {quarantined}")
            print(f"Checkpoint now at position: {processed_position} ({checkpoint_path})")
            print(f"Total O-track docs in DB (전체 소스 합산): {store.count_documents(cso_classification='O')}")
            if docs:
                assert_conformance(adapter.source_name, docs)
                print("Conformance: PASS")


if __name__ == "__main__":
    main()
