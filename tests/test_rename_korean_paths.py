import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from rename_korean_paths import _migrate_db, _rename_folders, _translate_path_str, _validate  # noqa: E402

from rd2.storage.db import DocumentStore
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_PRISM


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch):
    """_migrate_db/_validate는 rename_korean_paths.py 안에서 인자 없이 DocumentStore()를
    호출해 .env의 MARIADB_DATABASE를 그대로 본다 — 그게 rd2_dump(실 작업 데이터)를
    가리키므로, env를 rd2_test로 덮어써서 이 테스트 파일의 모든 DocumentStore() 호출
    (테스트 헬퍼+스크립트 함수 양쪽 다)이 같은 테스트 DB를 보게 강제한다. 이걸
    빼먹으면 테스트가 실수로 실 작업 DB를 TRUNCATE/UPDATE할 수 있다(2026-07-09,
    2026-07-13 두 번 실제로 이렇게 걸렸음 — 후자는 rd2_test를 실 작업 DB와 그대로
    공유하고 있어서 발생, .env 분리로 재발 방지)."""
    monkeypatch.setenv("MARIADB_DATABASE", "rd2_test")
    store = DocumentStore()
    try:
        with store._conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE documents")
        store._conn.commit()
    finally:
        store.close()
    yield


def _seed_db(*, source: str, doc_type: str, body_file_path: str | None) -> None:
    store = DocumentStore()
    try:
        with store._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (dedup_key, cso_classification, title, "
                "ordering_agency, source, doc_type, body_file_path, other_file_paths, "
                "disclosure_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"{source}::https://example.com/1",
                    "O",
                    "테스트 문서",
                    "테스트기관",
                    source,
                    doc_type,
                    body_file_path,
                    "",
                    "공개",
                ),
            )
        store._conn.commit()
    finally:
        store.close()


def _fetch_one(sql: str):
    store = DocumentStore()
    try:
        with store._conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()
    finally:
        store.close()


def test_translate_path_str_only_touches_source_and_doc_type_segments():
    assert _translate_path_str("PRISM/연구보고서/1_공개 연구 샘플.pdf") == str(
        Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "1_공개 연구 샘플.pdf"
    )


def test_translate_path_str_leaves_already_english_paths_unchanged():
    path = str(Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "1_file.pdf")
    assert _translate_path_str(path) == path


def test_rename_folders_renames_source_and_doc_type_dirs(tmp_path):
    data_root = tmp_path / "data"
    korean_dir = data_root / "PRISM" / "연구보고서"
    korean_dir.mkdir(parents=True)
    (korean_dir / "1_file.pdf").write_bytes(b"content")

    changes = _rename_folders(data_root, dry_run=False)

    assert len(changes) == 1
    english_dir = data_root / "PRISM" / "research_report"
    assert english_dir.exists()
    assert (english_dir / "1_file.pdf").read_bytes() == b"content"
    assert not korean_dir.exists()


def test_rename_folders_is_idempotent(tmp_path):
    data_root = tmp_path / "data"
    korean_dir = data_root / "PRISM" / "연구보고서"
    korean_dir.mkdir(parents=True)

    _rename_folders(data_root, dry_run=False)
    second_run_changes = _rename_folders(data_root, dry_run=False)

    assert second_run_changes == []
    assert (data_root / "PRISM" / "research_report").exists()


def test_rename_folders_dry_run_makes_no_changes(tmp_path):
    data_root = tmp_path / "data"
    korean_dir = data_root / "PRISM" / "연구보고서"
    korean_dir.mkdir(parents=True)

    changes = _rename_folders(data_root, dry_run=True)

    assert len(changes) == 1
    assert korean_dir.exists()
    assert not (data_root / "PRISM" / "research_report").exists()


def test_migrate_db_updates_columns():
    _seed_db(
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path="보건복지부/입찰공고/1_file.pdf",
    )

    changes = _migrate_db(dry_run=False)
    assert len(changes) == 1

    source, doc_type, body_file_path = _fetch_one(
        "SELECT source, doc_type, body_file_path FROM documents"
    )

    expected_path = str(Path("mohw") / "bid_notice" / "1_file.pdf")
    assert source == "mohw"
    assert doc_type == "bid_notice"
    assert body_file_path == expected_path

    # 한글 텍스트(제목 등)는 이 마이그레이션이 건드리면 안 된다.
    title = _fetch_one("SELECT title FROM documents")[0]
    assert title == "테스트 문서"


def test_migrate_db_is_idempotent():
    _seed_db(
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path="보건복지부/입찰공고/1_file.pdf",
    )

    _migrate_db(dry_run=False)
    second_run_changes = _migrate_db(dry_run=False)

    assert second_run_changes == []


def test_migrate_db_dry_run_makes_no_changes():
    _seed_db(
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path="보건복지부/입찰공고/1_file.pdf",
    )

    changes = _migrate_db(dry_run=True)
    assert len(changes) == 1

    source = _fetch_one("SELECT source FROM documents")[0]
    assert source == "보건복지부"


def test_validate_passes_after_full_migration(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "mohw" / "bid_notice").mkdir(parents=True)
    (data_root / "mohw" / "bid_notice" / "1_file.pdf").write_bytes(b"x")
    _seed_db(
        source="mohw",
        doc_type="bid_notice",
        body_file_path="mohw/bid_notice/1_file.pdf",
    )

    failures = _validate(data_root)
    assert failures == []


def test_validate_catches_leftover_korean_column_value(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _seed_db(
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path=None,
    )

    failures = _validate(data_root)
    assert any("한글" in f for f in failures)


def test_validate_catches_missing_referenced_file(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    _seed_db(
        source="mohw",
        doc_type="bid_notice",
        body_file_path="mohw/bid_notice/missing.pdf",
    )

    failures = _validate(data_root)
    assert any("파일 없음" in f for f in failures)
