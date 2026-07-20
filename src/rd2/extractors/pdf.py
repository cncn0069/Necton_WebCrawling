"""네이티브 PDF에서 텍스트/표를 결정론적으로 추출한다 (모델 추론 없이 파싱만).

2026-07-15 office-hours → plan-eng-review 논의: 네이티브 PDF는 콘텐츠 스트림에
이미 좌표(Tm/Td/Tf/Tj)가 있어 OCR 없이 파싱만으로 레이아웃을 뽑을 수 있다는
가설을 실제 수집 파일 샘플로 확인한 뒤 채택. 텍스트 레이어가 없거나 깨진
페이지(스캔본, CID 폰트에 디센던트 폰트가 없는 경우 등)는 `needs_ocr=True`로만
표시하고 내용은 빈 문자열로 둔다 — 실제 OCR/VLM 호출은 TODOS.md
"OCR/VLM 폴백 배치 단계"의 책임이다.

OCR 폴백 트리거 기준으로 ToUnicode CMap 존재 여부를 사전 체크하는 대신, 추출된
텍스트에서 인쇄 가능 문자 비율을 사후 검증하는 방식을 쓴다 — CMap이 있어도
깨진 폰트 서브셋은 있을 수 있어 사후 검증이 더 정확하다(PyMuPDF는 유니코드로
매핑 불가능한 글리프를 U+FFFD로 반환한다).

2026-07-15 EC2 실제 데이터로 검증 중 발견: pdfplumber `extract_tables()`는
페이지당 0.01~0.4초가 걸려, 수백 페이지짜리 대용량 편람류 문서(예:
"근로기준법 질의회시집" 246페이지)에서 표 추출에만 30초 이상 걸린다(PyMuPDF
자체 `find_tables()`로 바꿔도 마찬가지 — 텍스트 추출은 0.5초 미만이라 표
추출만의 문제). "수집 직후 동기 실행"이라는 설계 전제가 깨지므로, 페이지 수가
`_LARGE_DOC_PAGE_THRESHOLD`를 넘는 문서는 앞 `_MAX_TABLE_PAGES_FOR_LARGE_DOC`
페이지까지만 표를 추출한다(사용자 결정) — `ExtractedDocument.tables_truncated`로
잘렸는지 노출한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pymupdf

_REPLACEMENT_CHAR = "�"
_MIN_PRINTABLE_RATIO = 0.7
_LARGE_DOC_PAGE_THRESHOLD = 20
_MAX_TABLE_PAGES_FOR_LARGE_DOC = 5


@dataclass
class ExtractedTable:
    """PDF/HWP 공통 표 스키마 — rows는 원본 셀 값 그대로 두고 헤더를 가정하지 않는다.

    `page_number`는 PDF에만 있는 개념이라 Optional — HWP는 페이지 단위로 표를
    노출하지 않으므로 None을 쓴다.
    """

    rows: list[list[str | None]]
    page_number: int | None = None


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    tables: list[ExtractedTable] = field(default_factory=list)
    needs_ocr: bool = False


@dataclass
class ExtractedDocument:
    source_path: Path
    pages: list[ExtractedPage]
    tables_truncated: bool = False

    @property
    def needs_ocr(self) -> bool:
        """한 페이지라도 텍스트 레이어가 없거나 깨졌으면 문서 전체를 OCR 대기열 후보로 본다."""
        return any(page.needs_ocr for page in self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)


def _printable_ratio(text: str) -> float:
    """공백을 제외한 문자 중 U+FFFD(디코딩 불가)가 아닌 비율. 빈 텍스트는 0.0(=OCR 필요)."""
    non_whitespace = [ch for ch in text if not ch.isspace()]
    if not non_whitespace:
        return 0.0
    printable = sum(1 for ch in non_whitespace if ch != _REPLACEMENT_CHAR)
    return printable / len(non_whitespace)


def _page_needs_ocr(text: str) -> bool:
    return _printable_ratio(text) < _MIN_PRINTABLE_RATIO


def _extract_tables_for_page(plumber_page: pdfplumber.page.Page, page_number: int) -> list[ExtractedTable]:
    return [
        ExtractedTable(page_number=page_number, rows=[list(row) for row in raw_table])
        for raw_table in plumber_page.extract_tables()
    ]


def extract_pdf(path: Path) -> ExtractedDocument:
    """네이티브 PDF에서 페이지별 텍스트+표를 추출한다.

    손상되거나 텍스트 레이어가 없는 페이지는 예외를 던지지 않고 `needs_ocr=True`로
    표시한다 — 호출자는 `ExtractedDocument.needs_ocr`를 보고 OCR 배치 대상 여부를
    판단한다.

    페이지 수가 `_LARGE_DOC_PAGE_THRESHOLD`를 넘으면 앞 `_MAX_TABLE_PAGES_FOR_LARGE_DOC`
    페이지까지만 표를 추출한다 — `tables_truncated=True`로 표시되며, 그 이후
    페이지의 `tables`는 빈 리스트다(표가 없었다는 뜻이 아니라 시도하지 않았다는 뜻).
    """
    pages: list[ExtractedPage] = []
    with pymupdf.open(path) as mupdf_doc, pdfplumber.open(path) as plumber_doc:
        page_count = len(mupdf_doc)
        is_large_doc = page_count > _LARGE_DOC_PAGE_THRESHOLD
        table_page_limit = _MAX_TABLE_PAGES_FOR_LARGE_DOC if is_large_doc else page_count

        for page_number, (mupdf_page, plumber_page) in enumerate(
            zip(mupdf_doc, plumber_doc.pages), start=1
        ):
            text = mupdf_page.get_text("text")
            tables = (
                _extract_tables_for_page(plumber_page, page_number)
                if page_number <= table_page_limit
                else []
            )
            pages.append(
                ExtractedPage(
                    page_number=page_number,
                    text=text,
                    tables=tables,
                    needs_ocr=_page_needs_ocr(text),
                )
            )
    return ExtractedDocument(source_path=path, pages=pages, tables_truncated=is_large_doc)
