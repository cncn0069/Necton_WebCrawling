import json
import os

import pymysql
import pytest
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
    department/production_date 등 RD-2 v1.1 컬럼이 없다. payload_json은 실제
    구DB에는 있었지만(2026-07-15 스키마 정리로 신규 스키마에서는 제거) 이
    구버전 흉내에는 여전히 포함한다 — 마이그레이션이 그걸 드롭하는지도 검증해야 하므로."""
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


def _column_type(store: DocumentStore, column: str) -> str:
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'documents' AND column_name = %s",
            (store.database, column),
        )
        return cur.fetchone()[0].lower()


def _is_nullable(store: DocumentStore, column: str) -> bool:
    with store._conn.cursor() as cur:
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'documents' AND column_name = %s",
            (store.database, column),
        )
        return cur.fetchone()[0] == "YES"


def test_add_missing_columns_adds_rd2_columns_as_null():
    """구버전 DB(핵심 컬럼만 있음)를 열면 누락된 RD-2 컬럼이 추가된다. payload_json
    백업이 2026-07-15에 제거된 뒤로는 과거 행을 역추출해 채우는 안전망도 함께
    없어져서, 새로 추가된 컬럼은 그냥 NULL로 남는다(컬럼이 유일한 SSOT가 된 트레이드오프)."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (dedup_key, payload_json, cso_classification) "
                "VALUES (%s, %s, %s)",
                (
                    f"{SOURCE_OPEN_GO_KR}::https://open.go.kr/1",
                    json.dumps({"title": "구버전 문서"}, ensure_ascii=False),
                    "O",
                ),
            )
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        with store._conn.cursor() as cur:
            cur.execute("SELECT title, department, source FROM documents")
            row = cur.fetchone()
        assert row == (None, None, None)
        assert "payload_json" not in _columns(store)
    finally:
        store.close()


def test_drop_deprecated_columns_removes_old_columns():
    """is_synthetic/source_url/abstract/payload_json은 RD-2 v1.1 필수 필드 목록에
    없어 제거 대상 — 이 컬럼들이 이미 있는(구버전) DB를 열면 자동으로 빠져야 한다."""
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
    assert "payload_json" not in columns
    # 동시에 RD-2 v1.1 컬럼은 정상적으로 추가되어 있어야 한다.
    assert "title" in columns
    assert "department" in columns


def test_migrate_column_types_converts_text_to_date_and_varchar():
    """구버전 DB는 production_date 등이 전부 TEXT였다(2026-07-15 이전) — 새
    DocumentStore를 열면 DATE/VARCHAR로 타입이 맞춰지고 기존 값(ISO 문자열)도
    그대로 보존돼야 한다."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(
            conn,
            extra_columns=",\n                title TEXT,\n"
            "                production_date TEXT,\n"
            "                source TEXT,\n"
            "                doc_type TEXT",
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (dedup_key, payload_json, cso_classification, "
                "title, production_date, source, doc_type) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    f"{SOURCE_OPEN_GO_KR}::https://open.go.kr/2",
                    "{}",
                    "O",
                    "날짜 테스트",
                    "2024-03-15",
                    SOURCE_OPEN_GO_KR,
                    DOC_TYPE_OFFICIAL_DOCUMENT,
                ),
            )
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        assert _column_type(store, "production_date") == "date"
        assert _column_type(store, "title") == "varchar"
        with store._conn.cursor() as cur:
            cur.execute("SELECT title, production_date FROM documents")
            row = cur.fetchone()
        assert row[0] == "날짜 테스트"
        assert str(row[1]) == "2024-03-15"
    finally:
        store.close()


def test_check_constraint_rejects_invalid_cso_classification():
    """cso_classification은 O/C/S만 허용 — 앱을 거치지 않은 직접 INSERT도 막아야 한다."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(conn)
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        with pytest.raises(pymysql.err.OperationalError):
            with store._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (dedup_key, cso_classification) VALUES (%s, %s)",
                    ("bad::key", "X"),
                )
    finally:
        store._conn.rollback()
        store.close()


def test_check_constraint_rejects_invalid_disclosure_status():
    """disclosure_status는 공개/부분공개/비공개만 허용."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(conn)
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        with pytest.raises(pymysql.err.OperationalError):
            with store._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (dedup_key, cso_classification, disclosure_status) "
                    "VALUES (%s, %s, %s)",
                    ("bad::key2", "O", "잘못된값"),
                )
    finally:
        store._conn.rollback()
        store.close()


def test_migrate_not_null_widens_columns_when_no_nulls_exist():
    """ordering_agency/disclosure_status는 Document 모델의 필수 필드 — 기존 행에
    NULL이 하나도 없으면 NOT NULL로 좁혀져야 한다."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (dedup_key, payload_json, cso_classification) "
                "VALUES (%s, %s, %s)",
                (f"{SOURCE_OPEN_GO_KR}::https://open.go.kr/3", "{}", "O"),
            )
    finally:
        conn.close()

    # 1차: 컬럼이 없는 구DB를 열어 nullable로 컬럼 추가(기존 행은 값이 없어 NULL).
    store = DocumentStore(database=_TEST_DATABASE)
    store.close()

    conn = _raw_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET ordering_agency = %s, disclosure_status = %s "
                "WHERE dedup_key = %s",
                ("테스트기관", "공개", f"{SOURCE_OPEN_GO_KR}::https://open.go.kr/3"),
            )
    finally:
        conn.close()

    # 2차: NULL이 없어졌으니 재오픈 시 NOT NULL로 좁혀져야 한다.
    store = DocumentStore(database=_TEST_DATABASE)
    try:
        assert _is_nullable(store, "ordering_agency") is False
        assert _is_nullable(store, "disclosure_status") is False
    finally:
        store.close()


def test_migrate_not_null_skips_columns_when_nulls_exist():
    """기존 행에 NULL이 남아있으면(과거 payload_json 시절 백필 안 된 행 등) NOT NULL
    MODIFY는 실패하므로 조용히 스킵돼야 한다 — DocumentStore 생성 자체가 죽으면 안 됨."""
    conn = _raw_connection()
    try:
        _create_old_schema_db(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (dedup_key, payload_json, cso_classification) "
                "VALUES (%s, %s, %s)",
                (f"{SOURCE_OPEN_GO_KR}::https://open.go.kr/4", "{}", "O"),
            )
    finally:
        conn.close()

    store = DocumentStore(database=_TEST_DATABASE)
    try:
        # ordering_agency/disclosure_status가 여전히 NULL인 행이 있으므로 NOT NULL로
        # 좁혀지지 않아야 한다(예외 없이 nullable로 남음).
        assert _is_nullable(store, "ordering_agency") is True
        assert _is_nullable(store, "disclosure_status") is True
    finally:
        store.close()
