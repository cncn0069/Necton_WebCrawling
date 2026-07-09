"""본문파일 로컬 저장 규칙.

설계 문서: ~/.gstack/projects/CODE/안정현-design-20260707-115203.md 참고.

data/{source}/{doc_type}/ 중첩 폴더에 저장하고, 최종 경로 문자열을
Document.body_file_path에 기록하는 건 호출부(어댑터) 책임이다. 실제 다운로드
체인(원문정보 어댑터, TODOS P2)이 이 함수를 호출하는 자리만 표시해둔 스켈레톤 —
동시 쓰기/원자적 저장(임시파일→rename)은 다루지 않는다(단일 프로세스·순차 실행 전제).
"""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')
_UNCLASSIFIED = "_unclassified"


def _sanitize_path_component(value: str | None) -> str:
    """폴더명/파일명 조각으로 쓸 수 있도록 파일시스템 금지문자를 제거한다.
    None이거나 제거 후 빈 문자열이면 미분류 버킷으로 보낸다."""
    if not value:
        return _UNCLASSIFIED
    cleaned = _INVALID_PATH_CHARS.sub("", value).strip()
    return cleaned or _UNCLASSIFIED


def resolve_body_file_path(
    root: Path,
    source: str,
    doc_type: str | None,
    identifier: str,
    filename: str,
) -> Path:
    """root/{source}/{doc_type}/{identifier}_{filename} 최종 경로를 만들고
    상위 폴더를 생성해둔다. save_body_file()과 스트리밍 다운로드(대용량 파일이라
    바이트를 메모리에 올리지 않고 직접 디스크에 쓰고 싶은 경우, 예: molit.py)가
    이 경로 결정 규칙을 공유한다."""
    dir_path = root / _sanitize_path_component(source) / _sanitize_path_component(doc_type)
    dir_path.mkdir(parents=True, exist_ok=True)

    safe_identifier = _sanitize_path_component(identifier)
    safe_filename = _sanitize_path_component(filename)
    return dir_path / f"{safe_identifier}_{safe_filename}"


def save_body_file(
    root: Path,
    source: str,
    doc_type: str | None,
    identifier: str,
    filename: str,
    raw_bytes: bytes,
) -> str:
    """본문파일을 root/{source}/{doc_type}/ 에 저장하고 저장된 경로를 반환한다.

    identifier(dedup_key 또는 documents.id)를 파일명 접두사로 붙여 같은
    source/doc_type 폴더 안에서 파일명이 겹치지 않게 한다.
    """
    final_path = resolve_body_file_path(root, source, doc_type, identifier, filename)
    final_path.write_bytes(raw_bytes)
    # root 기준 상대경로로 반환 — DB가 다른 머신/체크아웃 위치로 옮겨져도
    # (root만 다시 맞춰주면) 경로가 깨지지 않도록 절대경로를 저장하지 않는다
    # (2026-07-07 사용자 결정).
    return str(final_path.relative_to(root))
