import json
import os

import pymysql
from dotenv import load_dotenv

from rd2.storage.db import DocumentStore
from rd2.storage.naming import DOC_TYPE_OFFICIAL_DOCUMENT, SOURCE_OPEN_GO_KR

_TEST_DATABASE = "rd2_test"


def _raw_connection():
    load_dotenv()
    return pymysql.connect(
        host=os.environ["MARIADB_HOST"],
        port=int(os.environ.get("MARIADB_PORT", 3306)),
        user=os.environ["MARIADB_USER"],
        password=os.environ["MARIADB_PASSWORD"],
        database=_TEST_DATABASE,
        charset="utf8mb4",
        autocommit=True,
    )


def _create_old_schema_db(conn, *, extra_columns: str = "") -> None:
    """DocumentStore가 생기기 전(또는 스키마 확장 전) 버전을 흉내낸 documents 테이블 —
    id/dedup_key/payload_json/cso_classification/created_at 핵심 컬럼만 있고
    department/production_date 등 RD-2 v1.1 컬럼이 없다."""
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS documents")
        cur.execute("DROP TABLE IF EXISTS quarantine")
        cur.execute("DROP TABLE IF EXISTS pending_downloads")
        cur.execute(
            f"""
            CREATE TABLE documents (
                id INT PRIMARY KEY AUTO_INCREMENT,
                dedup_key VARCHAR(768) NOT NULL,
                payload_json LONGTEXT NOT NULL,
                cso_classification VARCHAR(16) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP{extra_columns}
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute("CREATE UNIQUE INDEX idx_documents_dedup_key ON documents(dedup_key)")
        cur.execute(
            """
            CREATE TABLE quarantine (
                id INT PRIMARY KEY AUTO_INCREMENT,
                raw_payload_json LONGTEXT NOT NULL,
                error TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )


def _columns(store: DocumentStore) -> set[str]:
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'documents'",
            (store.database,),
        )
        return {row[0] for row in cur.fetchall()}


def test_migrate_and_backfill_adds_missing_columns_from_payload_json():
    """구버전 DB(핵심 컬럼만 있음)를 열면, 누락된 RD-2 컬럼이 추가되고 기존 행의
    payload_json에서 값이 채워져야 한다(2026-07-07 설계, 오늘까지 실제 실행 검증 없었음)."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(conn)

        payload = {
            "title": "구버전 문서",
            "ordering_agency": "테스트기관",
            "department": "테스트부서",
            "disclosure_status": "공개",
            "cso_classification": "O",
            "source": SOURCE_OPEN_GO_KR,
            "doc_type": DOC_TYPE_OFFICIAL_DOCUMENT,
        }
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (dedup_key, payload_json, cso_classification) "
                "VALUES (%s, %s, %s)",
                (
                    f"{SOURCE_OPEN_GO_KR}::https://open.go.kr/1",
                    json.dumps(payload, ensure_ascii=False),
                    "O",
                ),
            )
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        with store._conn.cursor() as cur:
            cur.execute(
                "SELECT title, ordering_agency, department, disclosure_status, source, doc_type "
                "FROM documents"
            )
            row = cur.fetchone()
        assert row == (
            "구버전 문서",
            "테스트기관",
            "테스트부서",
            "공개",
            SOURCE_OPEN_GO_KR,
            DOC_TYPE_OFFICIAL_DOCUMENT,
        )
    finally:
        store.close()


def test_drop_deprecated_columns_removes_old_columns():
    """is_synthetic/source_url/abstract는 RD-2 v1.1 필수 필드 목록에 없어 제거 대상 —
    이 컬럼들이 이미 있는(구버전) DB를 열면 자동으로 빠져야 한다."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(
            conn,
            extra_columns=",\n                is_synthetic TINYINT,\n"
            "                source_url TEXT,\n                abstract TEXT",
        )
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        columns = _columns(store)
    finally:
        store.close()

    assert "is_synthetic" not in columns
    assert "source_url" not in columns
    assert "abstract" not in columns
    # 동시에 RD-2 v1.1 컬럼은 정상적으로 추가되어 있어야 한다.
    assert "title" in columns
    assert "department" in columns
