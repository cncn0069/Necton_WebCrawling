from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rd2.extraction import hwp_text

_REPO_ROOT = Path(__file__).parent.parent
_REAL_HWPX_SAMPLE = (
    _REPO_ROOT / "data" / "molit" / "meeting_minutes" / "4785_★2024년 제4회 수도권정비실무위원회 회의록.hwpx"
)


class _FakeProc:
    def __init__(self, *, returncode: int, stdout: str = "", raise_timeout: bool = False, out_path: Path | None = None, out_content: str = ""):
        self.returncode = returncode
        self._stdout = stdout
        self._raise_timeout = raise_timeout
        self._out_path = out_path
        self._out_content = out_content
        self._communicate_calls = 0

    def communicate(self, timeout=None):
        self._communicate_calls += 1
        if self._raise_timeout and self._communicate_calls == 1:
            raise subprocess.TimeoutExpired(cmd="hwp5odt", timeout=timeout)
        if self._out_path is not None and self.returncode == 0:
            self._out_path.write_text(self._out_content, encoding="utf-8")
        return self._stdout, None

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode


class TestHwpxExtraction:
    def test_real_sample_extracts_nonempty_paragraphs(self):
        assert _REAL_HWPX_SAMPLE.exists(), "실사 픽스처 파일이 없음 — 경로 확인 필요"

        result = hwp_text.extract_hwp_spans(_REAL_HWPX_SAMPLE, data_root=_REPO_ROOT / "data")

        assert "error" not in result
        assert result["extractor"] == "hwpx-xml"
        spans = result["pages"][0]["spans"]
        assert len(spans) > 0
        assert any("수도권정비" in s["text"] for s in spans)
        # bbox 등 좌표 관련 필드가 전혀 없어야 한다(오늘 재설계의 핵심 목표).
        assert "bbox" not in spans[0]

    def test_single_pseudo_page(self):
        result = hwp_text.extract_hwp_spans(_REAL_HWPX_SAMPLE, data_root=_REPO_ROOT / "data")
        assert result["num_pages"] == 1
        assert len(result["pages"]) == 1
        assert result["pages"][0]["page_no"] == 1

    def test_invalid_hwpx_returns_error_dict(self, tmp_path):
        bad = tmp_path / "broken.hwpx"
        bad.write_bytes(b"not actually a zip file")

        result = hwp_text.extract_hwp_spans(bad, data_root=tmp_path)

        assert "error" in result
        assert "pages" not in result


class TestHwpExtractionFallback:
    """.hwp는 hwp5odt(주력) → hwp5txt(폴백) 순서로 시도한다 — 서브프로세스를
    모킹해 실제 pyhwp 실행 없이 그 분기 로직만 빠르게 검증."""

    def test_uses_odt_when_it_succeeds(self, monkeypatch, tmp_path):
        hwp_path = tmp_path / "a.hwp"
        hwp_path.write_bytes(b"stub")

        odt_xml = (
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            "<text:p>첫 문단</text:p><text:p>둘째 문단</text:p>"
            "</office:document-content>"
        )

        def fake_popen(cmd, **kwargs):
            assert cmd[0] == "hwp5odt"
            out_path = Path(cmd[cmd.index("--output") + 1])
            return _FakeProc(returncode=0, out_path=out_path, out_content=odt_xml)

        monkeypatch.setattr(hwp_text.subprocess, "Popen", fake_popen)

        result = hwp_text.extract_hwp_spans(hwp_path, data_root=tmp_path)

        assert "error" not in result
        assert result["extractor"] == "pyhwp-odt"
        texts = [s["text"] for s in result["pages"][0]["spans"]]
        assert texts == ["첫 문단", "둘째 문단"]

    def test_falls_back_to_txt_when_odt_times_out(self, monkeypatch, tmp_path):
        hwp_path = tmp_path / "b.hwp"
        hwp_path.write_bytes(b"stub")

        def fake_popen(cmd, **kwargs):
            if cmd[0] == "hwp5odt":
                return _FakeProc(returncode=0, raise_timeout=True)
            assert cmd[0] == "hwp5txt"
            out_path = Path(cmd[cmd.index("--output") + 1])
            return _FakeProc(returncode=0, out_path=out_path, out_content="평문 폴백 결과\n\n둘째 줄")

        monkeypatch.setattr(hwp_text.subprocess, "Popen", fake_popen)

        result = hwp_text.extract_hwp_spans(hwp_path, data_root=tmp_path)

        assert "error" not in result
        assert result["extractor"] == "pyhwp-txt"
        assert "timed out" in result["fallback_reason"]
        texts = [s["text"] for s in result["pages"][0]["spans"]]
        assert texts == ["평문 폴백 결과", "둘째 줄"]

    def test_error_when_both_odt_and_txt_fail(self, monkeypatch, tmp_path):
        hwp_path = tmp_path / "c.hwp"
        hwp_path.write_bytes(b"stub")

        def fake_popen(cmd, **kwargs):
            return _FakeProc(returncode=1, stdout="boom")

        monkeypatch.setattr(hwp_text.subprocess, "Popen", fake_popen)

        result = hwp_text.extract_hwp_spans(hwp_path, data_root=tmp_path)

        assert "error" in result
        assert "pages" not in result

    def test_unsupported_extension_returns_error(self, tmp_path):
        other = tmp_path / "file.txt"
        other.write_text("x")

        result = hwp_text.extract_hwp_spans(other, data_root=tmp_path)

        assert "error" in result
