"""src/rd2/extractors/pdf.py 테스트.

실제 수집 문서(data/)는 gitignore 대상이라 CI에서 재현 불가능하므로, reportlab로
그 자리에서 생성하는 합성 fixture를 쓴다 — pdf_render.py가 이미 같은 방식으로
Korean CID 폰트를 등록해 쓰고 있어 그 패턴을 재사용한다.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from rd2.extractors.pdf import (
    _LARGE_DOC_PAGE_THRESHOLD,
    _MAX_TABLE_PAGES_FOR_LARGE_DOC,
    ExtractedDocument,
    _page_needs_ocr,
    _printable_ratio,
    extract_pdf,
)

_FONT_NAME = "HYSMyeongJo-Medium"
_font_registered = False


def _ensure_font_registered() -> None:
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))
        _font_registered = True


def _make_text_pdf(path: Path, text: str) -> None:
    _ensure_font_registered()
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont(_FONT_NAME, 14)
    c.drawString(50, 700, text)
    c.save()


def _make_table_pdf(path: Path, rows: list[list[str]]) -> None:
    """GRID 스타일로 실제 셀 경계선을 그린다 — pdfplumber의 기본 표 인식(선 기반
    휴리스틱)이 경계선 없는 표는 표로 인식하지 못한다."""
    _ensure_font_registered()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    table = Table(rows)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ]
        )
    )
    doc.build([table])


def _make_multi_page_pdf_with_tables(
    path: Path, *, page_count: int, table_pages: dict[int, list[list[str]]]
) -> None:
    """1-indexed 페이지 번호를 키로 하는 `table_pages`에 지정된 페이지에만 GRID
    표를 그린다. 나머지 페이지는 짧은 문단만 채운다(빈 페이지는 reportlab이
    생략할 수 있어 페이지 수 보장을 위해 최소 내용을 둔다)."""
    _ensure_font_registered()
    style = TableStyle(
        [("FONTNAME", (0, 0), (-1, -1), _FONT_NAME), ("GRID", (0, 0), (-1, -1), 0.5, colors.black)]
    )
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    flowables = []
    for page_no in range(1, page_count + 1):
        if page_no in table_pages:
            table = Table(table_pages[page_no])
            table.setStyle(style)
            flowables.append(table)
        else:
            flowables.append(Paragraph(f"페이지 {page_no} 본문", getSampleStyleSheet()["Normal"]))
        if page_no != page_count:
            flowables.append(PageBreak())
    doc.build(flowables)


def _make_image_only_pdf(path: Path, tmp_path: Path) -> None:
    """텍스트 레이어 없이 이미지 하나만 있는 PDF — 스캔본 시뮬레이션."""
    image_path = tmp_path / "blank.png"
    Image.new("RGB", (200, 100), color="white").save(image_path)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(str(image_path), 50, 650, width=200, height=100)
    c.save()


class TestPrintableRatio:
    def test_all_printable_text_is_ratio_one(self):
        assert _printable_ratio("정상적인 한글 텍스트입니다") == 1.0

    def test_empty_text_is_ratio_zero(self):
        assert _printable_ratio("") == 0.0

    def test_whitespace_only_text_is_ratio_zero(self):
        assert _printable_ratio("   \n\t  ") == 0.0

    def test_all_replacement_chars_is_ratio_zero(self):
        assert _printable_ratio("���") == 0.0

    def test_mixed_replacement_chars_below_threshold(self):
        # 공백 제외 9글자 중 4글자(a,b,c,d)만 인쇄 가능, 나머지 5글자는 U+FFFD
        text = "ab���cd��"
        assert _printable_ratio(text) == 4 / 9


class TestPageNeedsOcr:
    def test_clean_text_does_not_need_ocr(self):
        assert _page_needs_ocr("충분히 긴 정상 텍스트 " * 5) is False

    def test_empty_text_needs_ocr(self):
        assert _page_needs_ocr("") is True

    def test_mostly_replacement_chars_needs_ocr(self):
        assert _page_needs_ocr("�" * 20 + "ok") is True


class TestExtractPdf:
    def test_native_text_pdf_extracts_text_and_does_not_need_ocr(self, tmp_path: Path):
        pdf_path = tmp_path / "native.pdf"
        _make_text_pdf(pdf_path, "정보공개포털 O트랙 테스트 문서입니다")

        result = extract_pdf(pdf_path)

        assert isinstance(result, ExtractedDocument)
        assert "정보공개포털" in result.full_text
        assert result.needs_ocr is False
        assert result.pages[0].needs_ocr is False

    def test_image_only_pdf_is_flagged_for_ocr(self, tmp_path: Path):
        pdf_path = tmp_path / "scanned.pdf"
        _make_image_only_pdf(pdf_path, tmp_path)

        result = extract_pdf(pdf_path)

        assert result.needs_ocr is True
        assert result.pages[0].needs_ocr is True
        assert result.pages[0].text.strip() == ""

    def test_table_pdf_extracts_rows(self, tmp_path: Path):
        pdf_path = tmp_path / "table.pdf"
        rows = [["기관명", "부서", "분류"], ["국토교통부", "정책기획팀", "O"]]
        _make_table_pdf(pdf_path, rows)

        result = extract_pdf(pdf_path)

        assert len(result.pages[0].tables) == 1
        assert result.pages[0].tables[0].rows == rows

    def test_multi_page_document_needs_ocr_is_any_page(self, tmp_path: Path):
        text_pdf = tmp_path / "text_only.pdf"
        _make_text_pdf(text_pdf, "정상 텍스트 페이지")
        result = extract_pdf(text_pdf)

        # 단일 페이지 문서이므로 문서 전체 needs_ocr은 그 페이지 결과와 같아야 한다.
        assert result.needs_ocr == result.pages[0].needs_ocr

    def test_large_document_only_extracts_tables_up_to_page_limit(self, tmp_path: Path):
        pdf_path = tmp_path / "large.pdf"
        page_count = _LARGE_DOC_PAGE_THRESHOLD + 5
        within_limit_page = _MAX_TABLE_PAGES_FOR_LARGE_DOC
        beyond_limit_page = _MAX_TABLE_PAGES_FOR_LARGE_DOC + 3
        _make_multi_page_pdf_with_tables(
            pdf_path,
            page_count=page_count,
            table_pages={
                within_limit_page: [["안쪽", "표"]],
                beyond_limit_page: [["바깥쪽", "표"]],
            },
        )

        result = extract_pdf(pdf_path)

        assert result.tables_truncated is True
        assert len(result.pages[within_limit_page - 1].tables) == 1
        assert result.pages[beyond_limit_page - 1].tables == []

    def test_small_document_is_not_truncated(self, tmp_path: Path):
        pdf_path = tmp_path / "small.pdf"
        rows = [["기관명", "부서"]]
        _make_multi_page_pdf_with_tables(pdf_path, page_count=2, table_pages={2: rows})

        result = extract_pdf(pdf_path)

        assert result.tables_truncated is False
        assert result.pages[1].tables[0].rows == rows
