"""data/ 폴더명과 DB에 저장된 한글 source/doc_type 값을 영어 코드로 일회성 정리.

RDS로 옮기기 전에, 지금까지 한글로 저장돼 온 출처명·문서유형명을 영어 코드로
바꾼다(2026-07-09 plan-eng-review 결정). 순서와 안전장치:

1. data/ 아래 물리적 폴더(출처명/문서유형명)를 먼저 rename한다.
2. DocumentStore(같은 timeout=60 커넥션)를 재사용해 documents 테이블의
   source/doc_type/body_file_path/other_file_paths 컬럼과 payload_json 백업을
   함께 갱신한다 — payload_json은 json.loads/dumps로 파싱해서 해당 키만
   바꾸고 다른 한글 텍스트(제목/본문 등)는 건드리지 않는다.
3. 실행 전 rd2.db를 자동 백업한다(rd2.db.bak-{timestamp}). data/ 폴더는
   rename이 원자적 연산이라 별도 백업 없이도 실패 시 그대로 존재한다.
4. 두 단계 모두 "이미 영어면 건드리지 않는다"로 구현되어 있어 재실행해도
   안전(idempotent) — 1단계만 끝난 상태에서 죽어도 재실행하면 2단계부터
   이어간다.
5. 마지막에 검증 게이트를 돌려 한글 잔존이 없는지, body_file_path가 가리키는
   파일이 실제로 존재하는지 확인한다. 하나라도 실패하면 0이 아닌 코드로 종료.

사용법: python scripts/rename_korean_paths.py [--db-path PATH] [--data-root PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Windows 콘솔이 기본 cp949 인코딩이면 한글 파일명을 print()할 때
# UnicodeEncodeError로 죽는다 — 검증 게이트까지 못 가고 스크립트가 중단되면
# 실제 마이그레이션은 이미 커밋된 채 검증만 못 하는 위험한 상태가 된다.
# stdout을 UTF-8로 강제하고, 그래도 안 되는 문자는 대체 처리한다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.storage.db import DocumentStore, _encode_for_sqlite  # noqa: E402
from rd2.storage.naming import LEGACY_DOC_TYPE_MAP, LEGACY_SOURCE_MAP  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HANGUL_RANGE = re.compile(r"[가-힣]")


def _rename_folders(data_root: Path, *, dry_run: bool) -> list[str]:
    """data/{source}/{doc_type}/ 폴더명을 한글→영어로 rename. 실행한 변경사항 목록을 반환."""
    changes: list[str] = []
    if not data_root.exists():
        return changes

    for source_dir in sorted(data_root.iterdir()):
        if not source_dir.is_dir():
            continue
        current_source_dir = source_dir
        if source_dir.name in LEGACY_SOURCE_MAP:
            new_source_dir = source_dir.parent / LEGACY_SOURCE_MAP[source_dir.name]
            changes.append(f"rename dir: {source_dir} -> {new_source_dir}")
            if not dry_run:
                source_dir.rename(new_source_dir)
            current_source_dir = new_source_dir

        if not current_source_dir.exists():
            continue
        for doc_type_dir in sorted(current_source_dir.iterdir()):
            if not doc_type_dir.is_dir():
                continue
            if doc_type_dir.name in LEGACY_DOC_TYPE_MAP:
                new_doc_type_dir = doc_type_dir.parent / LEGACY_DOC_TYPE_MAP[doc_type_dir.name]
                changes.append(f"rename dir: {doc_type_dir} -> {new_doc_type_dir}")
                if not dry_run:
                    doc_type_dir.rename(new_doc_type_dir)
    return changes


def _translate_path_str(path_str: str) -> str:
    """상대경로 문자열의 첫 두 세그먼트(source/doc_type)만 한글→영어로 치환.
    파일명 자체에 우연히 같은 글자가 있어도 건드리지 않도록, 문자열 치환이
    아니라 경로를 세그먼트 단위로 쪼개서 첫 두 개만 바꾼다."""
    parts = Path(path_str).parts
    if not parts:
        return path_str
    new_parts = list(parts)
    if len(new_parts) >= 1 and new_parts[0] in LEGACY_SOURCE_MAP:
        new_parts[0] = LEGACY_SOURCE_MAP[new_parts[0]]
    if len(new_parts) >= 2 and new_parts[1] in LEGACY_DOC_TYPE_MAP:
        new_parts[1] = LEGACY_DOC_TYPE_MAP[new_parts[1]]
    return str(Path(*new_parts))


def _migrate_db(db_path: Path, *, dry_run: bool) -> list[str]:
    changes: list[str] = []
    store = DocumentStore(db_path)
    try:
        rows = store._conn.execute(
            "SELECT id, source, doc_type, body_file_path, other_file_paths, payload_json "
            "FROM documents"
        ).fetchall()

        for row_id, source, doc_type, body_file_path, other_file_paths_raw, payload_json in rows:
            new_source = LEGACY_SOURCE_MAP.get(source, source)
            new_doc_type = LEGACY_DOC_TYPE_MAP.get(doc_type, doc_type)
            new_body_file_path = (
                _translate_path_str(body_file_path) if body_file_path else body_file_path
            )
            other_file_paths = (
                other_file_paths_raw.split("|") if other_file_paths_raw else []
            )
            new_other_file_paths = [_translate_path_str(p) for p in other_file_paths]

            payload = json.loads(payload_json)
            if payload.get("source") in LEGACY_SOURCE_MAP:
                payload["source"] = LEGACY_SOURCE_MAP[payload["source"]]
            if payload.get("doc_type") in LEGACY_DOC_TYPE_MAP:
                payload["doc_type"] = LEGACY_DOC_TYPE_MAP[payload["doc_type"]]
            if payload.get("body_file_path"):
                payload["body_file_path"] = _translate_path_str(payload["body_file_path"])
            if payload.get("other_file_paths"):
                payload["other_file_paths"] = [
                    _translate_path_str(p) for p in payload["other_file_paths"]
                ]

            unchanged = (
                new_source == source
                and new_doc_type == doc_type
                and new_body_file_path == body_file_path
                and new_other_file_paths == other_file_paths
            )
            if unchanged:
                continue

            changes.append(
                f"row {row_id}: source={source!r}->{new_source!r} "
                f"doc_type={doc_type!r}->{new_doc_type!r} "
                f"body_file_path={body_file_path!r}->{new_body_file_path!r}"
            )
            if not dry_run:
                store._conn.execute(
                    "UPDATE documents SET source = ?, doc_type = ?, body_file_path = ?, "
                    "other_file_paths = ?, payload_json = ? WHERE id = ?",
                    (
                        new_source,
                        new_doc_type,
                        new_body_file_path,
                        _encode_for_sqlite(new_other_file_paths),
                        json.dumps(payload, ensure_ascii=False),
                        row_id,
                    ),
                )
        if not dry_run:
            store._conn.commit()
    finally:
        store.close()
    return changes


def _path_folder_segments_have_korean(path_str: str) -> bool:
    """경로의 폴더 세그먼트(source/doc_type, 처음 두 개)만 한글 검사한다 —
    파일명(세 번째 세그먼트)은 문서 제목이 그대로 들어가 있어 한글이
    정상이다(예: "PRISM/research_report/..._새만금 스마트도시계획.pdf")."""
    parts = Path(path_str).parts
    return any(_HANGUL_RANGE.search(p) for p in parts[:2])


def _validate(db_path: Path, data_root: Path) -> list[str]:
    """검증 게이트: data/ 폴더명과 DB의 source/doc_type/경로 폴더 세그먼트에
    한글이 남아있지 않은지, body_file_path가 가리키는 파일이 실제로 존재하는지
    확인. 파일명 자체(문서 제목)의 한글은 정상이므로 검사하지 않는다.
    실패 항목 목록을 반환(빈 리스트=통과)."""
    failures: list[str] = []

    if data_root.exists():
        for path in data_root.rglob("*"):
            if path.is_dir() and _HANGUL_RANGE.search(path.name):
                failures.append(f"한글 폴더명 잔존: {path}")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, source, doc_type, body_file_path, other_file_paths FROM documents"
        ).fetchall()
        for row_id, source, doc_type, body_file_path, other_file_paths_raw in rows:
            for label, value in (("source", source), ("doc_type", doc_type)):
                if value and _HANGUL_RANGE.search(value):
                    failures.append(f"row {row_id}: {label}={value!r}에 한글 잔존")
            for label, value in (
                ("body_file_path", body_file_path),
                ("other_file_paths", other_file_paths_raw),
            ):
                if value and any(
                    _path_folder_segments_have_korean(p) for p in value.split("|")
                ):
                    failures.append(f"row {row_id}: {label}={value!r} 폴더 세그먼트에 한글 잔존")
            if body_file_path and not (data_root / body_file_path).exists():
                failures.append(f"row {row_id}: body_file_path가 가리키는 파일 없음: {body_file_path}")
    finally:
        conn.close()
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=_REPO_ROOT / "rd2.db")
    parser.add_argument("--data-root", type=Path, default=_REPO_ROOT / "data")
    parser.add_argument(
        "--dry-run", action="store_true", help="변경 없이 무엇이 바뀔지만 출력"
    )
    args = parser.parse_args()

    if not args.dry_run and args.db_path.exists():
        backup_path = args.db_path.with_name(
            f"{args.db_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(args.db_path, backup_path)
        print(f"백업 생성: {backup_path}")

    folder_changes = _rename_folders(args.data_root, dry_run=args.dry_run)
    for line in folder_changes:
        print(line)
    print(f"폴더 변경: {len(folder_changes)}건")

    db_changes = _migrate_db(args.db_path, dry_run=args.dry_run) if args.db_path.exists() else []
    for line in db_changes:
        print(line)
    print(f"DB 행 변경: {len(db_changes)}건")

    if args.dry_run:
        print("--dry-run: 실제로는 아무것도 바뀌지 않았습니다.")
        return

    failures = _validate(args.db_path, args.data_root) if args.db_path.exists() else []
    if failures:
        print("검증 실패:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("검증 통과: 한글 잔존 없음, 모든 body_file_path 파일 존재 확인.")


if __name__ == "__main__":
    main()
