"""1회성 마이그레이션: 로컬 SQLite(rd2.db) → 로컬 MariaDB.

원본 rd2.db는 읽기 전용으로만 열어 손대지 않는다 — 문제 생기면 언제든
SQLite로 롤백 가능(이관은 복사이지 이동이 아니다). dedup_key/id를 원본
그대로 보존해서 이관 후에도 체크포인트 기반 재개가 그대로 동작한다
(dedup_key 유니크 인덱스가 재실행 시 중복 재삽입을 막아줌 — 그래서 이
스크립트도 재실행에 안전하다, 이미 들어간 행은 조용히 스킵됨).
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.storage.db import DocumentStore  # noqa: E402

_TABLES = ["documents", "quarantine", "pending_downloads"]


def _migrate_table(sqlite_conn: sqlite3.Connection, store: DocumentStore, table: str) -> tuple[int, int]:
    cur = sqlite_conn.execute(f"SELECT * FROM {table}")
    columns = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        return 0, 0

    col_list = ", ".join(columns)
    placeholders = ", ".join("%s" for _ in columns)
    inserted = 0
    skipped = 0
    for row in rows:
        try:
            with store._conn.cursor() as maria_cur:
                maria_cur.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", tuple(row)
                )
            store._conn.commit()
            inserted += 1
        except Exception as exc:  # noqa: BLE001
            # 이미 이관된 행(재실행) 또는 dedup_key/PK 충돌 — 건너뛴다
            store._conn.rollback()
            skipped += 1
            print(f"    skip {table} row (이미 존재하거나 오류): {exc}")
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-db", default="rd2.db", help="원본 SQLite 파일 (기본: rd2.db)")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    sqlite_path = repo_root / args.sqlite_db

    sqlite_conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        with DocumentStore() as store:
            for table in _TABLES:
                inserted, skipped = _migrate_table(sqlite_conn, store, table)
                print(f"{table}: inserted={inserted} skipped={skipped}")

            print()
            print(f"MariaDB documents total: {store.count_documents()}")
            print(f"MariaDB quarantine total: {store.count_quarantine()}")
    finally:
        sqlite_conn.close()


if __name__ == "__main__":
    main()
