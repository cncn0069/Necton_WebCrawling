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
from rd2.storage.naming import DOC_TYPE_AUDIT_RESULT  # noqa: E402


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
    parser.add_argument(
        "--doc-type", default=DOC_TYPE_AUDIT_RESULT,
        help=f"문서유형 코드 (기본: {DOC_TYPE_AUDIT_RESULT!r}) — --query와 짝을 맞춰야 함",
    )
    parser.add_argument(
        "--database", default=None,
        help="MariaDB 데이터베이스 이름 (기본: .env의 MARIADB_DATABASE, 보통 운영 DB인 "
             "rd2_dump). 검증용 수집은 --database rd2_test처럼 명시적으로 분리할 것 — "
             "생략하면 운영 DB에 그대로 쓰인다.",
    )
    parser.add_argument(
        "--skip", type=int, default=None,
        help="목록 앞에서 건너뛸 건수. 생략하면 체크포인트 파일의 이어할 위치를 사용",
    )
    parser.add_argument(
        "--checkpoint", default=None,
        help="진행 상황 저장 파일 경로 (기본: rd2.db.alio_checkpoint.json 계열)",
    )
    parser.add_argument(
        "--reset-checkpoint", action="store_true",
        help="체크포인트를 무시하고 --skip(기본 0)부터 새로 시작",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    # 체크포인트 파일명은 SQLite 시절 그대로 유지한다(다른 collect_*.py와 동일
    # 관례 — DocumentStore가 MariaDB 전용으로 바뀐 뒤에도 파일명은 안 바꿈).
    # doc_type을 파일명에 포함시켜 query/doc_type 조합별로 분리한다(plan-eng-review
    # outside voice 지적, 2026-07-15) — 분리 안 하면 같은 체크포인트로 다른
    # 검색어를 수집할 때 이전 검색어의 체크포인트 위치를 그대로 읽어, 새 검색어
    # 결과 대부분을 건너뛰고 에러 없이 조용히 0건 처리로 끝날 수 있다. 기본값
    # (audit_result)은 기존 체크포인트 파일명을 그대로 유지한다 — 이미 운영 중인
    # 감사결과 수집(5,622건)의 진행 위치를 새 파일명 규칙 때문에 잃어버리면
    # 처음부터 다시 수집하게 된다.
    if args.checkpoint:
        checkpoint_path = Path(args.checkpoint)
    elif args.doc_type == DOC_TYPE_AUDIT_RESULT:
        checkpoint_path = repo_root / "rd2.db.alio_checkpoint.json"
    else:
        checkpoint_path = repo_root / f"rd2.db.alio_{args.doc_type}_checkpoint.json"

    if args.reset_checkpoint:
        base_skip = args.skip or 0
    elif args.skip is not None:
        base_skip = args.skip
    else:
        base_skip = _load_checkpoint(checkpoint_path)

    print(f"Query: {args.query!r}")
    print(f"Checkpoint file: {checkpoint_path}")
    print(f"Starting from position: {base_skip}")

    adapter = AlioAdapter(query=args.query, doc_type=args.doc_type)

    collected = 0
    quarantined = 0
    processed_position = base_skip
    docs = []
    db_kwargs = {"database": args.database} if args.database else {}
    with DocumentStore(**db_kwargs) as store:
        print(f"Target database: {store.database!r}")
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
                print(f"    doc_type={doc.doc_type!r} body_file_path={doc.body_file_path!r}")

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
