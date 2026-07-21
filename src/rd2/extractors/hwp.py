"""HWP/HWPX에서 텍스트와 표를 안전하게 추출한다.

파서는 파일에 따라 무한 대기하거나 프로세스 수준에서 종료될 수 있으므로 실제
파싱은 항상 자식 프로세스에서 수행한다. 부모 프로세스는 입력 형식과 크기를 먼저
검사하고, 제한 시간 안에 정상 JSON 응답을 받았을 때만 결과를 채택한다. 따라서
문서 하나가 손상돼도 전체 배치 프로세스는 계속 진행할 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from hwp_hwpx_parser import HWP5Reader, HWPXReader

from rd2.extractors.pdf import ExtractedTable

_MAX_HWP_FILE_BYTES = 100 * 1024 * 1024
_MAX_HWPX_MEMBERS = 2_048
_MAX_HWPX_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_HWPX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_HWPX_COMPRESSION_RATIO = 200
_HWP_PARSE_TIMEOUT_SECONDS = 60

_OLE_HWP5_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_HWP3_MAGIC = b"HWP Document File V3.00"
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass
class ExtractedHwpDocument:
    source_path: Path
    text: str = ""
    tables: list[ExtractedTable] = field(default_factory=list)
    is_encrypted: bool = False
    is_valid: bool = True
    error: str | None = None

    @property
    def needs_quarantine(self) -> bool:
        """암호화되어 있거나 파싱 자체가 불가능한 문서는 격리 대상이다."""
        return self.is_encrypted or not self.is_valid


def _invalid(path: Path, error: str) -> ExtractedHwpDocument:
    return ExtractedHwpDocument(source_path=path, is_valid=False, error=error)


def _detect_file_type(path: Path) -> tuple[str | None, str | None]:
    """확장자가 아니라 파일 시그니처로 HWP5/HWPX를 구분한다."""
    try:
        with path.open("rb") as file:
            header = file.read(max(len(_HWP3_MAGIC), len(_OLE_HWP5_MAGIC)))
    except OSError as exc:
        return None, f"input_read_error: {exc}"

    if header.startswith(_OLE_HWP5_MAGIC):
        return "hwp5", None
    if header.startswith(_ZIP_MAGIC):
        return "hwpx", None
    if header.startswith(_HWP3_MAGIC):
        return None, "unsupported_hwp3"
    return None, "unknown_hwp_format"


def _preflight(path: Path) -> tuple[str | None, str | None]:
    """파서 호출 전에 과대 파일과 HWPX 압축 폭탄을 거부한다."""
    file_type, error = _detect_file_type(path)
    if error:
        return None, error

    try:
        if path.stat().st_size > _MAX_HWP_FILE_BYTES:
            return None, "file_size_limit_exceeded"
        if file_type != "hwpx":
            return file_type, None

        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > _MAX_HWPX_MEMBERS:
                return None, "hwpx_member_count_limit_exceeded"
            total_size = 0
            for member in members:
                total_size += member.file_size
                if member.file_size > _MAX_HWPX_MEMBER_BYTES:
                    return None, "hwpx_member_size_limit_exceeded"
                if total_size > _MAX_HWPX_TOTAL_BYTES:
                    return None, "hwpx_total_size_limit_exceeded"
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > _MAX_HWPX_COMPRESSION_RATIO
                ):
                    return None, "hwpx_compression_ratio_limit_exceeded"
    except (OSError, BadZipFile) as exc:
        return None, f"invalid_hwpx_archive: {exc}"
    return file_type, None


def _extract_hwp_in_process(path: Path) -> ExtractedHwpDocument:
    """자식 프로세스 안에서만 호출되는 실제 파서 진입점."""
    file_type, error = _preflight(path)
    if error or file_type is None:
        return _invalid(path, error or "unknown_hwp_format")

    reader_class = HWP5Reader if file_type == "hwp5" else HWPXReader
    try:
        with reader_class(path) as reader:
            if not reader.is_valid():
                return _invalid(path, "parser_reported_invalid_document")
            if reader.is_encrypted():
                return ExtractedHwpDocument(
                    source_path=path,
                    is_encrypted=True,
                    error="encrypted_document",
                )
            text = reader.extract_text()
            tables = [ExtractedTable(rows=table.rows) for table in reader.get_tables()]
    except Exception as exc:
        return _invalid(path, f"parser_error: {type(exc).__name__}: {exc}")

    return ExtractedHwpDocument(source_path=path, text=text, tables=tables)


def _worker_payload(result: ExtractedHwpDocument) -> dict[str, Any]:
    return {
        "text": result.text,
        "tables": [table.rows for table in result.tables],
        "is_encrypted": result.is_encrypted,
        "is_valid": result.is_valid,
        "error": result.error,
    }


def _result_from_payload(path: Path, payload: dict[str, Any]) -> ExtractedHwpDocument:
    try:
        return ExtractedHwpDocument(
            source_path=path,
            text=str(payload.get("text") or ""),
            tables=[ExtractedTable(rows=rows) for rows in payload.get("tables", [])],
            is_encrypted=bool(payload.get("is_encrypted", False)),
            is_valid=bool(payload["is_valid"]),
            error=payload.get("error"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _invalid(path, f"invalid_worker_payload: {exc}")


def extract_hwp(path: Path) -> ExtractedHwpDocument:
    """HWP/HWPX를 별도 프로세스에서 제한 시간 안에 추출한다.

    확장자가 잘못된 파일도 파일 시그니처로 실제 형식을 판별한다. 자식 파서가
    멈추거나 비정상 종료되면 해당 문서만 격리 결과로 반환하고 호출자 프로세스에는
    예외를 전파하지 않는다.
    """
    path = Path(path)
    _, error = _preflight(path)
    if error:
        return _invalid(path, error)

    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[2])
    current_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not current_pythonpath else os.pathsep.join((source_root, current_pythonpath))
    )
    command = [sys.executable, "-m", "rd2.extractors.hwp", "--worker", str(path.resolve())]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_HWP_PARSE_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return _invalid(path, f"parser_timeout_after_{_HWP_PARSE_TIMEOUT_SECONDS}s")
    except OSError as exc:
        return _invalid(path, f"worker_start_error: {exc}")

    if completed.returncode != 0:
        detail = completed.stderr.strip()[-500:]
        return _invalid(path, f"worker_exit_{completed.returncode}: {detail}".rstrip())
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return _invalid(path, f"invalid_worker_json: {exc}")
    if not isinstance(payload, dict):
        return _invalid(path, "invalid_worker_json: expected object")
    return _result_from_payload(path, payload)


def _worker_main(path: Path) -> int:
    result = _extract_hwp_in_process(path)
    print(json.dumps(_worker_payload(result), ensure_ascii=False))
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker is None:
        parser.error("--worker is required")
    return _worker_main(args.worker)


if __name__ == "__main__":
    raise SystemExit(_main())
