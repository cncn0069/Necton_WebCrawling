"""본문파일 로컬 저장 규칙.

설계 문서: ~/.gstack/projects/CODE/안정현-design-20260707-115203.md 참고.

data/{source}/{doc_type}/{bucket}/ 중첩 폴더에 저장하고, 최종 경로 문자열을
Document.body_file_path에 기록하는 건 호출부(어댑터) 책임이다. 실제 다운로드
체인(원문정보 어댑터, TODOS P2)이 이 함수를 호출하는 자리만 표시해둔 스켈레톤 —
동시 쓰기/원자적 저장(임시파일→rename)은 다루지 않는다(단일 프로세스·순차 실행 전제).

**버킷 분할(2026-07-15 추가)**: doc_type 폴더 하나에 파일이 수천 개씩 쌓이는
문제(예: alio audit_result 5,622건)를 막기 위해, {doc_type}/ 바로 아래가 아니라
{doc_type}/{start}-{end}/ 500개 단위 하위폴더에 저장한다("1-500", "501-1000"...).
버킷 분할 도입 이전에 이미 {doc_type}/ 바로 아래 flat하게 저장된 기존 파일은
마이그레이션하지 않고 그대로 둔다(2026-07-15 사용자 결정) — _resolve_bucket_dir()가
디렉터리 안의 하위"폴더"만 버킷으로 인식하고 기존 flat 파일(디렉터리 아님)은
무시하므로 자연스럽게 섞이지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')
_UNCLASSIFIED = "_unclassified"
_BUCKET_SIZE = 500
_BUCKET_DIR_RE = re.compile(r"^(\d+)-(\d+)$")


def _sanitize_path_component(value: str | None) -> str:
    """폴더명/파일명 조각으로 쓸 수 있도록 파일시스템 금지문자를 제거한다.
    None이거나 제거 후 빈 문자열이면 미분류 버킷으로 보낸다."""
    if not value:
        return _UNCLASSIFIED
    cleaned = _INVALID_PATH_CHARS.sub("", value).strip()
    return cleaned or _UNCLASSIFIED


def _resolve_bucket_dir(dir_path: Path) -> Path:
    """dir_path(root/source/doc_type) 아래에서 지금 채울 500개 단위 버킷
    하위폴더("{start}-{end}")를 정하고 반환한다(폴더 자체는 아직 안 만듦 —
    호출부가 mkdir한다). 상태를 어디에도 저장하지 않고 매 호출마다 디렉터리를
    다시 스캔한다 — 이 모듈 전체가 이미 전제하는 단일 프로세스·순차 실행 안에서는
    이걸로 충분하고, 버킷 하나(최대 500개)만 스캔하므로 비용도 작다.

    버킷 분할 도입 이전 flat 파일은 디렉터리가 아니라 파일이라 여기서 무시된다
    (기존 데이터 마이그레이션 안 함, 모듈 독스트링 참고)."""
    if not dir_path.exists():
        return dir_path / f"1-{_BUCKET_SIZE}"

    bucket_starts = sorted(
        int(m.group(1))
        for m in (_BUCKET_DIR_RE.match(p.name) for p in dir_path.iterdir() if p.is_dir())
        if m
    )
    if not bucket_starts:
        return dir_path / f"1-{_BUCKET_SIZE}"

    start = bucket_starts[-1]
    current_bucket = dir_path / f"{start}-{start + _BUCKET_SIZE - 1}"
    count = sum(1 for p in current_bucket.iterdir() if p.is_file()) if current_bucket.exists() else 0
    if count >= _BUCKET_SIZE:
        start += _BUCKET_SIZE
    return dir_path / f"{start}-{start + _BUCKET_SIZE - 1}"


def resolve_body_file_path(
    root: Path,
    source: str,
    doc_type: str | None,
    identifier: str,
    filename: str,
) -> Path:
    """root/{source}/{doc_type}/{bucket}/{identifier}_{filename} 최종 경로를
    만들고 상위 폴더를 생성해둔다. save_body_file()과 스트리밍 다운로드(대용량
    파일이라 바이트를 메모리에 올리지 않고 직접 디스크에 쓰고 싶은 경우, 예:
    molit.py)가 이 경로 결정 규칙을 공유한다."""
    dir_path = root / _sanitize_path_component(source) / _sanitize_path_component(doc_type)
    bucket_dir = _resolve_bucket_dir(dir_path)
    bucket_dir.mkdir(parents=True, exist_ok=True)

    safe_identifier = _sanitize_path_component(identifier)
    safe_filename = _sanitize_path_component(filename)
    return bucket_dir / f"{safe_identifier}_{safe_filename}"


def save_body_file(
    root: Path,
    source: str,
    doc_type: str | None,
    identifier: str,
    filename: str,
    raw_bytes: bytes,
) -> str:
    """본문파일을 root/{source}/{doc_type}/{bucket}/ 에 저장하고 저장된 경로를
    반환한다(bucket은 500개 단위, resolve_body_file_path() 참고).

    identifier(dedup_key 또는 documents.id)를 파일명 접두사로 붙여 같은
    source/doc_type/bucket 폴더 안에서 파일명이 겹치지 않게 한다.
    """
    final_path = resolve_body_file_path(root, source, doc_type, identifier, filename)
    final_path.write_bytes(raw_bytes)
    # root 기준 상대경로로 반환 — DB가 다른 머신/체크아웃 위치로 옮겨져도
    # (root만 다시 맞춰주면) 경로가 깨지지 않도록 절대경로를 저장하지 않는다
    # (2026-07-07 사용자 결정).
    return str(final_path.relative_to(root))
