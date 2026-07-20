"""HWP/HWPX에서 텍스트/표를 결정론적으로 추출한다 (모델 추론 없이 파싱만).

`hwp-hwpx-parser`(Apache-2.0, 순수 Python, olefile만 의존)로 HWP 5.0과 HWPX를
동일 인터페이스로 처리한다. pyhwp(AGPLv3+, 5년간 미갱신·사실상 방치)는
라이선스·유지보수 리스크로 배제하기로 확정(2026-07-15 plan-eng-review).

암호화된 문서나 파싱 자체가 불가능한 손상 문서는 복호화/복구를 시도하지 않고
`needs_quarantine=True`로만 표시한다 — storage/db.py의 `quarantine` 테이블에
격리해 비율만 추적한다(TODOS.md "HWP 암호화(배포용 문서) 처리 정책" 참고).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from hwp_hwpx_parser import read as read_hwp

from rd2.extractors.pdf import ExtractedTable

_MAX_HWP_FILE_BYTES = 100 * 1024 * 1024
_MAX_HWPX_MEMBERS = 2_048
_MAX_HWPX_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_HWPX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_HWPX_COMPRESSION_RATIO = 200


@dataclass
class ExtractedHwpDocument:
    source_path: Path
    text: str = ""
    tables: list[ExtractedTable] = field(default_factory=list)
    is_encrypted: bool = False
    is_valid: bool = True

    @property
    def needs_quarantine(self) -> bool:
        """암호화되어 있거나 파싱 자체가 불가능한 문서는 격리 대상이다."""
        return self.is_encrypted or not self.is_valid


def _is_safe_input(path: Path) -> bool:
    """파서 호출 전에 과대 파일과 HWPX 압축 폭탄을 거부한다."""
    try:
        if path.stat().st_size > _MAX_HWP_FILE_BYTES:
            return False
        if path.suffix.lower() != ".hwpx":
            return True

        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > _MAX_HWPX_MEMBERS:
                return False
            total_size = 0
            for member in members:
                total_size += member.file_size
                if member.file_size > _MAX_HWPX_MEMBER_BYTES:
                    return False
                if total_size > _MAX_HWPX_TOTAL_BYTES:
                    return False
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > _MAX_HWPX_COMPRESSION_RATIO
                ):
                    return False
    except (OSError, BadZipFile):
        return False
    return True


def extract_hwp(path: Path) -> ExtractedHwpDocument:
    """HWP(.hwp)/HWPX(.hwpx) 파일에서 텍스트+표를 추출한다.

    암호화되었거나 유효하지 않은 문서는 텍스트/표 추출을 시도하지 않고 빈
    상태로 반환한다 — 호출자는 `needs_quarantine`을 보고 quarantine 테이블로
    보낼지 판단한다.

    `hwp-hwpx-parser`의 `is_valid` 프로퍼티는 예외를 삼키지만, 컨텍스트 매니저
    진입(`__enter__` → `_open()`)은 손상된 파일에 대해 `ValueError`를 그대로
    던진다 — 그 시점의 예외도 "파싱 불가능한 문서"와 동일하게 격리 대상으로
    처리한다.
    """
    if not _is_safe_input(path):
        return ExtractedHwpDocument(source_path=path, is_valid=False)

    try:
        with read_hwp(path) as reader:
            if not reader.is_valid:
                return ExtractedHwpDocument(source_path=path, is_valid=False)
            if reader.is_encrypted:
                return ExtractedHwpDocument(source_path=path, is_encrypted=True)

            text = reader.text
            tables = [ExtractedTable(rows=table.rows) for table in reader.tables]
    except Exception:
        return ExtractedHwpDocument(source_path=path, is_valid=False)

    return ExtractedHwpDocument(source_path=path, text=text, tables=tables)
