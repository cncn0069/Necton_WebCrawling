"""src/rd2/adapters/orginl_info.py 테스트.

2026-07-15 EC2 실사로 발견한 확장자/실제 바이트 불일치 버그(`_save_filename`)만
다룬다 — 이 어댑터의 나머지 부분(브라우저 세션 필요)은 아직 전용 테스트가 없다.
"""

from __future__ import annotations

from rd2.adapters.orginl_info import _save_filename


class TestSaveFilename:
    def test_pdf_conversion_requested_swaps_hwp_extension_to_pdf(self):
        assert _save_filename("공고문.hwp", is_pdf="Y") == "공고문.pdf"

    def test_pdf_conversion_requested_swaps_hwpx_extension_to_pdf(self):
        assert _save_filename("계획서.hwpx", is_pdf="Y") == "계획서.pdf"

    def test_already_pdf_with_conversion_flag_is_unchanged(self):
        assert _save_filename("보고서.pdf", is_pdf="Y") == "보고서.pdf"

    def test_no_conversion_requested_keeps_original_name(self):
        assert _save_filename("원본.hwp", is_pdf="N") == "원본.hwp"

    def test_filename_with_multiple_dots_only_last_suffix_replaced(self):
        assert _save_filename("2019.최종본.hwp", is_pdf="Y") == "2019.최종본.pdf"
