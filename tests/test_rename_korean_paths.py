import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from rename_korean_paths import _migrate_db, _rename_folders, _translate_path_str, _validate  # noqa: E402

from rd2.storage.db import DocumentStore
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_PRISM


def _seed_db(db_path: Path, *, source: str, doc_type: str, body_file_path: str) -> None:
    store = DocumentStore(db_path)
    try:
        payload = {
            "title": "테스트 문서",
            "ordering_agency": "테스트기관",
            "source": source,
            "doc_type": doc_type,
            "body_file_path": body_file_path,
            "other_file_paths": [],
            "cso_classification": "O",
            "disclosure_status": "공개",
            "is_synthetic": False,
        }
        store._conn.execute(
            "INSERT INTO documents (dedup_key, payload_json, cso_classification, source, "
            "doc_type, body_file_path, other_file_paths) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"{source}::https://example.com/1",
                json.dumps(payload, ensure_ascii=False),
                "O",
                source,
                doc_type,
                body_file_path,
                "",
            ),
        )
        store._conn.commit()
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


def test_migrate_db_updates_column_and_payload_json_together(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path="보건복지부/입찰공고/1_file.pdf",
    )

    changes = _migrate_db(db_path, dry_run=False)
    assert len(changes) == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT source, doc_type, body_file_path, payload_json FROM documents"
        ).fetchone()
    finally:
        conn.close()

    source, doc_type, body_file_path, payload_json = row
    expected_path = str(Path("mohw") / "bid_notice" / "1_file.pdf")
    assert source == "mohw"
    assert doc_type == "bid_notice"
    assert body_file_path == expected_path

    payload = json.loads(payload_json)
    assert payload["source"] == "mohw"
    assert payload["doc_type"] == "bid_notice"
    assert payload["body_file_path"] == expected_path
    # payload_json의 다른 한글 텍스트(제목 등)는 이 마이그레이션이 건드리면 안 된다.
    assert payload["title"] == "테스트 문서"


def test_migrate_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path="보건복지부/입찰공고/1_file.pdf",
    )

    _migrate_db(db_path, dry_run=False)
    second_run_changes = _migrate_db(db_path, dry_run=False)

    assert second_run_changes == []


def test_migrate_db_dry_run_makes_no_changes(tmp_path):
    db_path = tmp_path / "test.db"
    _seed_db(
        db_path,
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path="보건복지부/입찰공고/1_file.pdf",
    )

    changes = _migrate_db(db_path, dry_run=True)
    assert len(changes) == 1

    conn = sqlite3.connect(db_path)
    try:
        source = conn.execute("SELECT source FROM documents").fetchone()[0]
    finally:
        conn.close()
    assert source == "보건복지부"


def test_validate_passes_after_full_migration(tmp_path):
    data_root = tmp_path / "data"
    db_path = tmp_path / "test.db"
    (data_root / "mohw" / "bid_notice").mkdir(parents=True)
    (data_root / "mohw" / "bid_notice" / "1_file.pdf").write_bytes(b"x")
    _seed_db(
        db_path,
        source="mohw",
        doc_type="bid_notice",
        body_file_path="mohw/bid_notice/1_file.pdf",
    )

    failures = _validate(db_path, data_root)
    assert failures == []


def test_validate_catches_leftover_korean_column_value(tmp_path):
    data_root = tmp_path / "data"
    db_path = tmp_path / "test.db"
    data_root.mkdir()
    _seed_db(
        db_path,
        source="보건복지부",
        doc_type="입찰공고",
        body_file_path=None,
    )

    failures = _validate(db_path, data_root)
    assert any("한글" in f for f in failures)


def test_validate_catches_missing_referenced_file(tmp_path):
    data_root = tmp_path / "data"
    db_path = tmp_path / "test.db"
    data_root.mkdir()
    _seed_db(
        db_path,
        source="mohw",
        doc_type="bid_notice",
        body_file_path="mohw/bid_notice/missing.pdf",
    )

    failures = _validate(db_path, data_root)
    assert any("파일 없음" in f for f in failures)
