import json
import sqlite3

from rd2.storage.db import DocumentStore


def _create_old_schema_db(db_path, *, extra_columns: str = "") -> None:
    """DocumentStore가 생기기 전(또는 스키마 확장 전) 버전을 흉내낸 documents 테이블 —
    id/dedup_key/payload_json/cso_classification/created_at 핵심 컬럼만 있고
    department/production_date 등 RD-2 v1.1 컬럼이 없다."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            cso_classification TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')){extra_columns}
        );
        CREATE UNIQUE INDEX idx_documents_dedup_key ON documents(dedup_key);
        CREATE TABLE quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_payload_json TEXT NOT NULL,
            error TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()


def test_migrate_and_backfill_adds_missing_columns_from_payload_json(tmp_path):
    """구버전 DB(핵심 컬럼만 있음)를 열면, 누락된 RD-2 컬럼이 추가되고 기존 행의
    payload_json에서 값이 채워져야 한다(2026-07-07 설계, 오늘까지 실제 실행 검증 없었음)."""
    db_path = tmp_path / "old.db"
    _create_old_schema_db(db_path)

    payload = {
        "title": "구버전 문서",
        "ordering_agency": "테스트기관",
        "department": "테스트부서",
        "disclosure_status": "공개",
        "cso_classification": "O",
        "source": "정보공개포털",
        "doc_type": "공문",
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO documents (dedup_key, payload_json, cso_classification) VALUES (?, ?, ?)",
        ("정보공개포털::https://open.go.kr/1", json.dumps(payload, ensure_ascii=False), "O"),
    )
    conn.commit()
    conn.close()

    store = DocumentStore(db_path)
    try:
        row = store._conn.execute(
            "SELECT title, ordering_agency, department, disclosure_status, source, doc_type "
            "FROM documents"
        ).fetchone()
        assert row == (
            "구버전 문서",
            "테스트기관",
            "테스트부서",
            "공개",
            "정보공개포털",
            "공문",
        )
    finally:
        store.close()


def test_drop_deprecated_columns_removes_old_columns(tmp_path):
    """is_synthetic/source_url/abstract는 RD-2 v1.1 필수 필드 목록에 없어 제거 대상 —
    이 컬럼들이 이미 있는(구버전) DB를 열면 자동으로 빠져야 한다."""
    db_path = tmp_path / "old_with_deprecated.db"
    _create_old_schema_db(
        db_path,
        extra_columns=",\n            is_synthetic INTEGER,\n            source_url TEXT,\n            abstract TEXT",
    )

    store = DocumentStore(db_path)
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(documents)")}
    finally:
        store.close()

    assert "is_synthetic" not in columns
    assert "source_url" not in columns
    assert "abstract" not in columns
    # 동시에 RD-2 v1.1 컬럼은 정상적으로 추가되어 있어야 한다.
    assert "title" in columns
    assert "department" in columns
