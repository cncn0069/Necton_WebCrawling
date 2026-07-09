"""MariaDB 저장 계층.

설계 문서의 운영(ETL) 요소를 반영:
- 중복 제거 키(source+source_url)에 유니크 인덱스 (성능 리뷰 #1)
- 스키마 검증 실패 레코드는 드롭하지 않고 격리 저장 (quarantine)

접속정보는 인자로 안 주면 `.env`(MARIADB_HOST/PORT/USER/PASSWORD/DATABASE)에서 읽는다 —
로컬 개발은 `.env`가 localhost를 가리키고, EC2 배포 시에는 같은 변수가 RDS 엔드포인트를
가리키도록 `.env`만 바꾸면 된다(코드 변경 불필요).
"""

from __future__ import annotations

import json
import os
import uuid

import pymysql
import pymysql.err
from dotenv import load_dotenv

from rd2.schema.models import Document

# RD-2 v1.1 필수 메타정보 16개 필드 + source/doc_type —
# payload_json 안에만 있으면 department/production_date 등으로 SQL 필터링·정렬이
# 불가능하므로 실제 컬럼으로 승격한다. payload_json은 전체 문서 백업/향후 확장용으로 유지.
# source/doc_type은 2026-07-07 오전 office-hours에서 "16개 필드 한정" 결정에 따라
# 한 차례 컬럼에서 제거됐다가, 같은 날 오후 후속 office-hours에서 본문파일을
# 출처별/문서종류별 폴더로 정리하는 요구가 생기며 다시 컬럼으로 복원됨(design doc:
# 안정현-design-20260707-115203.md 참고) — 단순 조회 편의가 아니라 파일 저장 경로를
# 결정하는 입력값이 됐기 때문. is_synthetic/source_url은 이번 요청과 무관해 계속 제외.
#
# body_text만 LONGTEXT — MariaDB TEXT는 64KB 한계라(SQLite TEXT는 무제한이었음),
# molit 회의록처럼 긴 원문이 쌓이면 넘길 수 있어 여유있게 잡음. 나머지 메타필드는
# 전부 짧은 값이라 TEXT(64KB)로 충분.
_EXTRA_COLUMNS: list[tuple[str, str]] = [
    ("title", "TEXT"),
    ("ordering_agency", "TEXT"),
    ("department", "TEXT"),
    ("unit_task", "TEXT"),
    ("production_date", "TEXT"),
    ("disclosure_status", "TEXT"),
    ("subject_category", "TEXT"),
    ("content_summary", "TEXT"),
    ("body_text", "LONGTEXT"),
    ("body_file_path", "TEXT"),
    ("non_disclosure_reason", "TEXT"),
    ("cso_sub_clause", "TEXT"),
    ("performing_agency", "TEXT"),
    ("start_date", "TEXT"),
    ("end_date", "TEXT"),
    ("source", "TEXT"),
    ("doc_type", "TEXT"),
    ("other_file_paths", "TEXT"),
    ("table_of_contents", "TEXT"),
]

def _encode_for_storage(value: object) -> object:
    """list 타입 필드(현재 other_file_paths만)는 DB에 직접 바인딩할 수 없어 "|"로
    join한 문자열로 인코딩해서 저장한다. "|"는 파일시스템 금지문자라 각 경로 안에
    나올 수 없으므로(안정현-design-20260707-115203.md의 sanitize 규칙 참고) 구분자로
    안전하다 — 예전엔 json.dumps를 썼는데, 그러면 파일명 자체에 들어있는 쉼표와 리스트
    구분용 쉼표가 섞여 사람이 눈으로/스프레드시트로 훑어볼 때 헷갈렸다.
    payload_json에는 원래 list 그대로 남으므로 이 인코딩은 documents 컬럼 표시용일 뿐이다."""
    if isinstance(value, list):
        return "|".join(value)
    return value

# 과거 스키마에 있었지만 RD-2 v1.1 필수 필드 목록에 없어 컬럼에서 제거된 것들.
# payload_json에는 계속 남아있으므로 데이터 손실은 없다. abstract는 body_text와
# 설명이 중복돼(둘 다 "초록"을 가리킴, 2026-07-07 수집계획 리뷰로 발견) 필드 자체를
# Document 모델에서 제거함 — 기존 DB에 남은 컬럼도 이걸로 정리된다.
_DEPRECATED_COLUMNS: list[str] = ["is_synthetic", "source_url", "abstract"]

# dedup_key(source::source_url)에 유니크 인덱스를 걸어야 해서 VARCHAR로 길이 제한이
# 필요하다(TEXT는 InnoDB가 인덱스를 못 만듦). utf8mb4에서 InnoDB 인덱스 최대 길이가
# 3072바이트(=768자*4바이트)라 그 한도까지 잡아서 긴 URL도 여유있게 수용한다.
_DEDUP_KEY_MAXLEN = 768

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INT PRIMARY KEY AUTO_INCREMENT,
    dedup_key VARCHAR(""" + str(_DEDUP_KEY_MAXLEN) + """) NOT NULL,
    payload_json LONGTEXT NOT NULL,
    cso_classification VARCHAR(16) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP""" + "".join(
    f",\n    {name} {sqltype}" for name, sqltype in _EXTRA_COLUMNS
) + """
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_dedup_key ON documents(dedup_key);

CREATE TABLE IF NOT EXISTS quarantine (
    id INT PRIMARY KEY AUTO_INCREMENT,
    raw_payload_json LONGTEXT NOT NULL,
    error TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2-pass 수집(메타데이터 우선, 파일 다운로드는 나중에 별도 지시로 실행)용 큐.
-- 성공하면 바로 삭제되므로 이 테이블 자체가 진행 상태다 — 별도 체크포인트 파일 불필요.
CREATE TABLE IF NOT EXISTS pending_downloads (
    dedup_key VARCHAR(""" + str(_DEDUP_KEY_MAXLEN) + """) PRIMARY KEY,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def _dedup_key(source: str, source_url: str | None) -> str:
    """O트랙은 source+URL로 결정적 키를 만들고, 합성 문서는 URL이 없으므로
    항상 새 레코드로 취급되도록 uuid를 붙인다 (합성 문서는 매번 새 문서라 dedup 대상이 아님)."""
    if source_url:
        return f"{source}::{source_url}"
    return f"{source}::synthetic::{uuid.uuid4()}"


class DocumentStore:
    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ):
        load_dotenv()
        self.host = host or os.environ["MARIADB_HOST"]
        self.port = int(port) if port is not None else int(os.environ.get("MARIADB_PORT", 3306))
        self.user = user or os.environ["MARIADB_USER"]
        self.password = password if password is not None else os.environ["MARIADB_PASSWORD"]
        self.database = database or os.environ["MARIADB_DATABASE"]
        self._conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=False,
        )
        self._execute_script(_SCHEMA)
        self._conn.commit()
        self._migrate_and_backfill()
        self._drop_deprecated_columns()

    def _execute_script(self, script: str) -> None:
        """pymysql은 sqlite3.executescript 같은 다중 statement 실행기가 없어
        ";"로 나눠 하나씩 실행한다 — 지금 스키마엔 세미콜론을 포함하는 statement가
        없으므로(트리거/프로시저 없음) 단순 split으로 충분하다."""
        with self._conn.cursor() as cur:
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DocumentStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _existing_columns(self, table: str) -> set[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (self.database, table),
            )
            return {row[0] for row in cur.fetchall()}

    def _migrate_and_backfill(self) -> None:
        """구 스키마(payload_json 컬럼만 있던 DB)를 열었을 때, 누락된 컬럼을
        추가하고 기존 행의 payload_json에서 값을 채운다."""
        existing = self._existing_columns("documents")
        missing = [(name, sqltype) for name, sqltype in _EXTRA_COLUMNS if name not in existing]
        if not missing:
            return
        with self._conn.cursor() as cur:
            for name, sqltype in missing:
                cur.execute(f"ALTER TABLE documents ADD COLUMN {name} {sqltype}")
        self._conn.commit()

        with self._conn.cursor() as cur:
            cur.execute("SELECT id, payload_json FROM documents")
            rows = cur.fetchall()
        with self._conn.cursor() as cur:
            for row_id, payload_json in rows:
                payload = json.loads(payload_json)
                values = [_encode_for_storage(payload.get(name)) for name, _ in missing]
                set_clause = ", ".join(f"{name} = %s" for name, _ in missing)
                cur.execute(
                    f"UPDATE documents SET {set_clause} WHERE id = %s", (*values, row_id)
                )
        self._conn.commit()

    def _drop_deprecated_columns(self) -> None:
        """RD-2 v1.1 필수 필드 목록에 없는 컬럼(source/is_synthetic/source_url/doc_type)을
        기존 DB에서 제거한다. payload_json에 값이 남아있으므로 데이터 손실은 없다."""
        existing = self._existing_columns("documents")
        with self._conn.cursor() as cur:
            for name in _DEPRECATED_COLUMNS:
                if name in existing:
                    cur.execute(f"ALTER TABLE documents DROP COLUMN {name}")
        self._conn.commit()

    def upsert(self, doc: Document) -> bool:
        """문서를 저장한다. 이미 존재하는 dedup_key면 조용히 무시(중복 스킵)하고 False 반환.

        유니크 인덱스가 있으므로 중복 판정은 O(1) 인덱스 조회로 처리된다 —
        전체 테이블 스캔이 아니다 (성능 리뷰 #1 반영).
        """
        key = _dedup_key(doc.source, doc.source_url)
        # mode="json"으로 dump하면 date는 ISO 문자열, enum은 .value로 직렬화되어
        # payload_json과 개별 컬럼 값이 항상 동일한 표현을 갖는다.
        dump = doc.model_dump(mode="json")
        extra_columns = [name for name, _ in _EXTRA_COLUMNS]
        extra_values = [_encode_for_storage(dump.get(name)) for name in extra_columns]
        columns = ["dedup_key", "payload_json", "cso_classification", *extra_columns]
        placeholders = ", ".join("%s" for _ in columns)
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO documents ({', '.join(columns)}) VALUES ({placeholders})",
                    (
                        key,
                        doc.model_dump_json(),
                        doc.cso_classification.value,
                        *extra_values,
                    ),
                )
            self._conn.commit()
            return True
        except pymysql.err.IntegrityError:
            self._conn.rollback()
            return False

    def mark_pending_download(self, doc: Document) -> None:
        """파일 다운로드를 나중으로 미룬 문서를 큐에 등록한다(멱등 — 이미 있으면 무시).
        source_url이 없는 합성 문서는 다운로드 대상이 아니므로 등록하지 않는다."""
        if not doc.source_url:
            return
        key = _dedup_key(doc.source, doc.source_url)
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO pending_downloads (dedup_key, source, source_url) "
                "VALUES (%s, %s, %s)",
                (key, doc.source, doc.source_url),
            )
        self._conn.commit()

    def list_pending_downloads(self, source: str) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT dedup_key, source_url FROM pending_downloads "
                "WHERE source = %s ORDER BY created_at",
                (source,),
            )
            rows = cur.fetchall()
        return [{"dedup_key": row[0], "source_url": row[1]} for row in rows]

    def clear_pending_download(self, dedup_key: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM pending_downloads WHERE dedup_key = %s", (dedup_key,))
        self._conn.commit()

    def get_title(self, dedup_key: str) -> str | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT title FROM documents WHERE dedup_key = %s", (dedup_key,)
            )
            row = cur.fetchone()
        return row[0] if row else None

    def update_files(
        self, dedup_key: str, body_file_path: str | None, other_file_paths: list[str]
    ) -> None:
        """백필 패스가 나중에 받아온 파일 경로를 기존 행에 반영한다. payload_json도
        같이 갱신해야 컬럼과 백업 JSON이 어긋나지 않는다."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM documents WHERE dedup_key = %s", (dedup_key,)
            )
            row = cur.fetchone()
        if row is None:
            return
        payload = json.loads(row[0])
        payload["body_file_path"] = body_file_path
        payload["other_file_paths"] = other_file_paths
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET body_file_path = %s, other_file_paths = %s, "
                "payload_json = %s WHERE dedup_key = %s",
                (
                    body_file_path,
                    _encode_for_storage(other_file_paths),
                    json.dumps(payload, ensure_ascii=False),
                    dedup_key,
                ),
            )
        self._conn.commit()

    def quarantine(self, raw_payload: dict, error: str) -> None:
        """스키마 검증 실패 레코드 — 드롭하지 않고 격리 저장 후 수동 검토 대상으로 남긴다."""
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quarantine (raw_payload_json, error) VALUES (%s, %s)",
                (json.dumps(raw_payload, ensure_ascii=False, default=str), error),
            )
        self._conn.commit()

    def count_documents(self, *, cso_classification: str | None = None) -> int:
        with self._conn.cursor() as cur:
            if cso_classification:
                cur.execute(
                    "SELECT COUNT(*) FROM documents WHERE cso_classification = %s",
                    (cso_classification,),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM documents")
            return cur.fetchone()[0]

    def count_quarantine(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM quarantine")
            return cur.fetchone()[0]
