"""src/rd2/extractors/hwp.py 테스트.

hwp-hwpx-parser는 HWPX를 생성하는 API가 없어(순수 리더), pdf_extractor처럼
합성 fixture를 만들 수 없다 — 실제 수집된 O트랙 문서(공개 문서라 저작권/민감정보
문제 없음) 중 가장 작은 파일을 tests/fixtures/hwp/에 그대로 사용한다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from rd2.extractors import hwp as hwp_extractor
from rd2.extraction import hwp_text
from rd2.extractors.hwp import ExtractedHwpDocument, extract_hwp
from rd2.extractors.pdf import ExtractedTable

_FIXTURES = Path(__file__).parent / "fixtures" / "hwp"


class TestNeedsQuarantine:
    def test_normal_document_does_not_need_quarantine(self):
        doc = ExtractedHwpDocument(source_path=Path("x.hwp"), text="본문")
        assert doc.needs_quarantine is False

    def test_encrypted_document_needs_quarantine(self):
        doc = ExtractedHwpDocument(source_path=Path("x.hwp"), is_encrypted=True)
        assert doc.needs_quarantine is True

    def test_invalid_document_needs_quarantine(self):
        doc = ExtractedHwpDocument(source_path=Path("x.hwp"), is_valid=False)
        assert doc.needs_quarantine is True


class TestExtractHwp:
    def test_hwp5_extracts_text_and_table(self):
        result = extract_hwp(_FIXTURES / "sample_audit_result.hwp")

        assert result.is_encrypted is False
        assert result.is_valid is True
        assert result.needs_quarantine is False
        assert "감사 개요" in result.text
        assert result.tables == [ExtractedTable(rows=[["감사 결과"]])]

    def test_hwpx_extracts_text(self):
        result = extract_hwp(_FIXTURES / "sample_notification.hwpx")

        assert result.is_encrypted is False
        assert result.needs_quarantine is False
        assert "고용위기 선제대응지역" in result.text

    def test_file_type_is_detected_by_magic_not_misleading_suffix(self, tmp_path: Path):
        misleading_path = tmp_path / "actually_hwp5.hwpx"
        misleading_path.write_bytes((_FIXTURES / "sample_audit_result.hwp").read_bytes())

        result = extract_hwp(misleading_path)

        assert result.is_valid is True
        assert "감사 개요" in result.text

    def test_corrupt_file_is_marked_invalid_not_raised(self, tmp_path: Path):
        garbage = tmp_path / "broken.hwp"
        garbage.write_bytes(b"not a real hwp file at all")

        result = extract_hwp(garbage)

        assert result.is_valid is False
        assert result.needs_quarantine is True
        assert result.text == ""

    def test_oversized_hwpx_member_is_quarantined_before_parser(self, tmp_path, monkeypatch):
        archive_path = tmp_path / "oversized.hwpx"
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("Contents/section0.xml", "x" * 128)
        monkeypatch.setattr(hwp_extractor, "_MAX_HWPX_MEMBER_BYTES", 64)

        result = extract_hwp(archive_path)

        assert result.is_valid is False
        assert result.needs_quarantine is True

    def test_parser_timeout_quarantines_only_the_document(self, monkeypatch):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

        monkeypatch.setattr(hwp_extractor.subprocess, "run", timeout)

        result = extract_hwp(_FIXTURES / "sample_audit_result.hwp")

        assert result.is_valid is False
        assert result.needs_quarantine is True
        assert result.error == "parser_timeout_after_60s"

    def test_worker_crash_quarantines_only_the_document(self, monkeypatch):
        def crash(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=-9, stdout="", stderr="killed")

        monkeypatch.setattr(hwp_extractor.subprocess, "run", crash)

        result = extract_hwp(_FIXTURES / "sample_audit_result.hwp")

        assert result.is_valid is False
        assert result.needs_quarantine is True
        assert result.error == "worker_exit_-9: killed"


def test_hwp_text_preserves_specific_isolated_parser_error(tmp_path: Path, monkeypatch):
    data_root = tmp_path / "data"
    source_path = data_root / "moe" / "official_document" / "broken.hwp"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"broken")
    monkeypatch.setattr(
        hwp_text,
        "extract_hwp",
        lambda path: ExtractedHwpDocument(
            source_path=path,
            is_valid=False,
            error="parser_timeout_after_60s",
        ),
    )

    result = hwp_text.extract_hwp_spans(source_path, data_root=data_root)

    assert result["error"] == "parser_timeout_after_60s"
