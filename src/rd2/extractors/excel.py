"""XLSX/XLSM 워크북에서 시트별 셀 값을 안전하게 추출한다.

openpyxl은 순수 파이썬이고 read-only 스트리밍 모드에서 시트를 행 단위로 흘려
읽으므로 `hwp.py`처럼 자식 프로세스로 격리하지 않는다. 대신 파싱 전에 파일
시그니처·크기·zip 폭탄을 먼저 검사하고, 파싱 중에는 셀 예산으로 상한을 둔다 —
손상되거나 과대한 워크북 하나가 배치 전체의 메모리를 먹지 않게 하기 위함이다.

암호가 걸린 xlsx는 OOXML zip이 아니라 OLE 복합문서로 감싸져 저장된다. 파일
시그니처가 OLE면 openpyxl이 열 수 없으므로 격리 대상으로 분류한다(구형 이진
`.xls`도 같은 OLE 시그니처라 확장자로 구분해 다른 사유를 남긴다).

예외를 던지지 않고 실패 시 `is_valid=False`인 결과를 반환한다 — 대량 배치
처리 중 파일 하나 때문에 전체가 멈추지 않게 하기 위함(`hwp.py`와 동일 관례).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

_MAX_EXCEL_FILE_BYTES = 100 * 1024 * 1024
_MAX_ZIP_MEMBERS = 2_048
_MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 200

# 시트 하나가 아니라 워크북 전체에 걸어야 의미가 있는 상한이다 — 시트 수백 개에
# 조금씩 나눠 담긴 과대 워크북도 같은 예산 안에서 멈춘다. 예산은 실제로 보관하는
# 셀에만 물린다: 실제 수집 문서 중에는 내용은 3만 행 남짓인데 빈 셀까지 세면
# 100만 셀이 넘는 희소·광폭 시트가 있어(PRISM 목록화 파일), 빈 셀을 예산에
# 넣으면 멀쩡한 문서가 절반쯤 잘려나간다. 현재 코퍼스 최대치가 그 파일의 69만
# 셀(54,943행)이라 100만으로 잡아 실제 문서는 전량 통과시키고 폭주만 막는다.
_MAX_CELLS_TOTAL = 1_000_000
# 예산은 메모리를, 이 상한은 병적으로 긴 시트의 순회 시간을 막는다.
_MAX_ROWS_TOTAL = 1_000_000

_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass
class ExtractedSheet:
    """워크시트 하나의 이름과 비어있지 않은 행들(셀은 이미 문자열로 렌더링됨)."""

    name: str
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class ExtractedExcelDocument:
    source_path: Path
    sheets: list[ExtractedSheet] = field(default_factory=list)
    truncated: bool = False
    is_encrypted: bool = False
    is_valid: bool = True
    error: str | None = None

    @property
    def needs_quarantine(self) -> bool:
        """암호화되어 있거나 파싱 자체가 불가능한 워크북은 격리 대상이다."""
        return self.is_encrypted or not self.is_valid


def _invalid(path: Path, error: str) -> ExtractedExcelDocument:
    return ExtractedExcelDocument(source_path=path, is_valid=False, error=error)


def _detect_file_type(path: Path) -> tuple[str | None, str | None]:
    """확장자가 아니라 파일 시그니처로 OOXML/OLE를 구분한다."""
    try:
        with path.open("rb") as file:
            header = file.read(len(_OLE_MAGIC))
    except OSError as exc:
        return None, f"input_read_error: {exc}"

    if header.startswith(_ZIP_MAGIC):
        return "ooxml", None
    if header.startswith(_OLE_MAGIC):
        # 구형 이진 .xls와 암호화된 .xlsx는 시그니처가 같다 — 확장자로만 구분된다.
        if path.suffix.lower() == ".xls":
            return None, "unsupported_legacy_xls"
        return None, "encrypted_or_ole_workbook"
    return None, "unknown_excel_format"


def _preflight(path: Path) -> tuple[str | None, str | None]:
    """파서 호출 전에 과대 파일과 zip 폭탄을 거부한다."""
    file_type, error = _detect_file_type(path)
    if error:
        return None, error

    try:
        if path.stat().st_size > _MAX_EXCEL_FILE_BYTES:
            return None, "file_size_limit_exceeded"

        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > _MAX_ZIP_MEMBERS:
                return None, "zip_member_count_limit_exceeded"
            total_size = 0
            for member in members:
                total_size += member.file_size
                if member.file_size > _MAX_ZIP_MEMBER_BYTES:
                    return None, "zip_member_size_limit_exceeded"
                if total_size > _MAX_ZIP_TOTAL_BYTES:
                    return None, "zip_total_size_limit_exceeded"
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > _MAX_ZIP_COMPRESSION_RATIO
                ):
                    return None, "zip_compression_ratio_limit_exceeded"
    except (OSError, BadZipFile) as exc:
        return None, f"invalid_ooxml_archive: {exc}"
    return file_type, None


def render_cell(value: Any) -> str:
    """셀 값 하나를 행 안에서 안전하게 쓸 수 있는 한 줄 문자열로 만든다.

    셀 안 줄바꿈은 공백으로 접는다 — 한 행은 한 논리 줄로 다뤄야 표 구조가
    유지되기 때문이다(HWP 추출기가 표를 마크다운 행으로 인라인하는 것과 같은
    관례).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, dt.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, float):
        # 엑셀은 정수도 float으로 돌려준다 — "2024.0"이 아니라 "2024"로 남긴다.
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, (int, Decimal)):
        return str(value)

    text = str(value)
    if "\r" in text or "\n" in text:
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text.strip()


def _rendered_row(raw_row: tuple[Any, ...]) -> list[str]:
    """행 하나를 렌더링하고 오른쪽 빈 셀을 잘라낸다. 전부 비었으면 빈 목록."""
    cells = [render_cell(value) for value in raw_row]
    while cells and not cells[-1]:
        cells.pop()
    return cells if any(cell for cell in cells) else []


def extract_excel(path: Path) -> ExtractedExcelDocument:
    """XLSX/XLSM 하나를 열어 시트별 행 목록을 만든다.

    수식은 마지막으로 저장될 때 캐시된 계산 결과로 읽는다(`data_only=True`) —
    엑셀이 한 번도 계산하지 않은 수식 셀은 값이 없어 빈 셀로 남는다. 셀 예산을
    넘기면 그 지점에서 읽기를 멈추고 `truncated=True`로 표시한다.
    """
    path = Path(path)
    _file_type, error = _preflight(path)
    if error:
        if error == "encrypted_or_ole_workbook":
            return ExtractedExcelDocument(
                source_path=path,
                is_encrypted=True,
                error="encrypted_document",
            )
        return _invalid(path, error)

    try:
        workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # noqa: BLE001 — 배치 처리 중 파일 하나 실패로 전체를 죽이지 않음
        return _invalid(path, f"parser_error: {type(exc).__name__}: {exc}")

    sheets: list[ExtractedSheet] = []
    truncated = False
    remaining_cells = _MAX_CELLS_TOTAL
    remaining_rows = _MAX_ROWS_TOTAL
    try:
        for worksheet in workbook.worksheets:
            sheet = ExtractedSheet(name=str(worksheet.title))
            sheets.append(sheet)
            if remaining_cells <= 0 or remaining_rows <= 0:
                truncated = True
                continue
            for raw_row in worksheet.iter_rows(values_only=True):
                remaining_rows -= 1
                cells = _rendered_row(raw_row)
                if cells:
                    sheet.rows.append(cells)
                    remaining_cells -= len(cells)
                if remaining_cells <= 0 or remaining_rows <= 0:
                    truncated = True
                    break
    except Exception as exc:  # noqa: BLE001 — 스트리밍 도중 깨진 시트도 같은 관례로 처리
        return _invalid(path, f"parser_error: {type(exc).__name__}: {exc}")
    finally:
        workbook.close()

    return ExtractedExcelDocument(source_path=path, sheets=sheets, truncated=truncated)
