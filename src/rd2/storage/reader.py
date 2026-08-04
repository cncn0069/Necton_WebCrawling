"""``documents`` 읽기 질의 — SQL이 사는 곳.

스크립트가 SQL 문자열을 들고 있으면 컬럼 하나가 바뀔 때 고칠 자리를 grep으로
찾아야 한다. 질의는 여기 모으고, 스크립트는 무엇을 원하는지만 말한다.

쓰기는 여기 없다. 이 클래스는 ``commit()``을 부르지 않으며 INSERT/UPDATE도
보내지 않는다 — 쓰기가 필요하면 ``DocumentStore``(db.py)를 쓴다.
"""

from __future__ import annotations

from typing import Any

from rd2.storage.connection import connect

#: 원문에서 물려받을 값. 생성 행은 원문의 업무 맥락을 그대로 쓰므로 여기 있는
#: 값이 그대로 ``documents`` 컬럼이 된다(writeback_minimal_to_rds._source_row).
#: unit_task/subject_category는 판별·생성에는 쓰이지 않지만 되쓸 때 채울 수 있는
#: 칸이라 함께 가져온다 — 여기서 빼면 그 두 컬럼이 NULL로 남는다.
SOURCE_ROW_COLUMNS: tuple[str, ...] = (
    "id",
    "source",
    "doc_type",
    "title",
    "ordering_agency",
    "department",
    "unit_task",
    "subject_category",
    "production_date",
    "body_file_path",
)

#: doc_type마다 id 내림차순 상위 N건. 윈도 함수로 뽑아야 doc_type이 하나만
#: 큰 코퍼스(audit_result 4,303건)여도 다른 종류가 밀려나지 않는다.
_TOP_BY_DOC_TYPE_SQL = """
SELECT {columns}
FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY doc_type ORDER BY id DESC) AS rn
    FROM documents
    WHERE doc_type IN ({placeholders})
      AND disclosure_status = '공개'
      AND body_file_path IS NOT NULL
      AND body_file_path REGEXP '\\\\.pdf$'
) AS ranked
WHERE rn <= %s
ORDER BY doc_type, id DESC
"""


class DocumentReader:
    """``documents``를 읽기만 하는 연결. 스키마를 건드리지 않는다."""

    def __init__(
        self,
        connection=None,
        *,
        settings: dict[str, Any] | None = None,
        **overrides: Any,
    ):
        if connection is not None:
            self._conn = connection
            #: 받아 온 커넥션은 준 쪽이 닫는다. 여기서 닫으면 같은 커넥션을
            #: 쓰는 다른 코드가 with 블록을 나가는 순간 끊긴다.
            self._owns_connection = False
        else:
            self._conn = connect(settings, **overrides)
            self._owns_connection = True

    def close(self) -> None:
        """직접 연 커넥션만 닫는다 — 받아 온 커넥션의 수명은 준 쪽 것이다."""

        if self._owns_connection:
            self._conn.close()

    def __enter__(self) -> "DocumentReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def top_pdfs_by_doc_type(
        self,
        doc_types: tuple[str, ...],
        per_doc_type: int,
        *,
        columns: tuple[str, ...] = SOURCE_ROW_COLUMNS,
    ) -> list[dict]:
        """doc_type마다 최신 공개 PDF를 ``per_doc_type``건씩 돌려준다."""

        sql = _TOP_BY_DOC_TYPE_SQL.format(
            columns=", ".join(columns),
            placeholders=", ".join("%s" for _ in doc_types),
        )
        with self._conn.cursor() as cursor:
            cursor.execute(sql, (*doc_types, per_doc_type))
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
