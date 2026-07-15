"""ALIO(공공기관 경영정보 공개시스템, alio.go.kr) 첨부파일 검색 어댑터.

RD-2 O트랙 신규 출처. 실제 사이트 조사(httpx 직접 요청, 2026-07-09) 결과:
- 검색 결과 페이지(`/search/searchTabPage.do`)는 Vue 컴포넌트가 마운트되자마자
  `GET /search/findTotalSearch.json`을 호출해 렌더링한다 — 이 JSON API를 그대로
  쓰면 브라우저 없이 순수 httpx로 전량 수집 가능하다(mohw.py와 동일하게
  browse_client 불필요, 페이지 소스에 인라인된 Vue 컴포넌트 스크립트로 확인).
- 검색 결과 한 행이 곧 첨부파일 한 건이다(한 공시에 파일이 여러 개면 행도 그만큼
  나뉘어 나온다 — DISCLOSURE_NO는 같고 FILE_NO만 다른 행들을 실사로 확인함).
- 검색 API의 TITLE 필드는 실제 보고서명이 아니라 검색어 하이라이트가 섞인
  게시판 분류명이다(예: "내부·외부 <b>감사결과</b>" — 어느 기관 게시물이든 다
  똑같다). 진짜 보고서명/작성부서/제출일은 게시글을 클릭했을 때 뜨는 상세뷰
  (`goPub()` → `/item/itemReport.do?seq={disclosure_no}`)가 jQuery로 비동기
  로드하는 조각 HTML(`/upload/disclosure/{yyyy}/{mm}/{dd}/{disclosure_no}/
  doc.html`)에만 있다(2026-07-09 사용자 지적 — 처음 버전은 이 상세 조회를
  건너뛰고 검색 API 필드만으로 채웠었다). 이 조각의 URL 안 날짜 폴더는
  DISCLOSURE_NO 앞 8자리(YYYYMMDD)와 항상 일치한다 — 검색 API의 IDATE와는
  최대 하루 어긋날 수 있어(실사 7건 샘플로 확인, 인덱싱 시각 차이로 추정) 날짜
  폴더는 반드시 DISCLOSURE_NO에서 뽑아야 한다. itemReport.do 자체는 그냥
  뼈대 HTML이라(내용은 전부 이 doc.html을 jQuery `.load()`로 붙여넣음) 굳이
  먼저 조회할 필요 없이 doc.html 경로를 직접 구성해 바로 가져온다 — 요청 한
  번을 아낀다.
- `/upload/disclosure/.../toc.html`도 같은 규칙으로 접근 가능해 목차
  텍스트(Document.table_of_contents)로 저장한다. 두 조각 다 상세페이지
  전용이라 404 등으로 못 가져오면(오래된 게시물의 경로 규칙이 다를 가능성 등)
  parse_detail()은 예외를 던지지 않고 검색 API 필드로만 채운 채 계속
  진행한다 — 다운로드 대상 파일 자체는 여전히 확보되므로 이 보강 실패가
  전체 건을 격리(quarantine)시킬 이유는 아니다.
- 첨부파일 다운로드는 `GET /download/file.json?f={FILE_NO}&d={DISCLOSURE_NO}&s={SUBMISSION_NO}`
  로 접근 제어 없이 바로 받을 수 있다(`resources/js/page/portal/downloadUtils.js`의
  `downReportAttachFile()`가 쓰는 엔드포인트). 실제 다운로드 버튼이 쓰는
  `/download/pfile.json`은 SAVE_FILE_NA/ORCP_FILE_NA가 알 수 없는 방식으로
  난독화돼 있어 그대로 못 쓰는데, 이 엔드포인트는 검색 API가 그대로 주는
  disclosureNo/fileNo/submissionNo 세 값만으로 동일 파일을 내려줘 더 단순하다.
  실제 원본 파일명은 응답의 Content-Disposition 헤더에 이중 따옴표로 감싸여
  들어있다(`filename=""실제파일명.pdf"";`) — `_parse_content_disposition_filename()`이
  처리한다. httpx는 이 헤더를 기본적으로 UTF-8로 정확히 디코드해준다(실사로 확인).
- source_url(=dedup_key 결정 요소)은 다운로드 엔드포인트가 아니라 게시글 상세페이지
  주소(`/item/itemReport.do?seq={disclosure_no}&disclosureNo={disclosure_no}
  &fileNo={file_no}`)로 둔다(2026-07-09 사용자 요청 — dedup_key가 사람이 실제로
  게시글을 열람하는 주소를 가리켜야 함). 레코드 구조는 파일 단위(검색 결과 한
  행 = Document 한 건)를 그대로 유지하므로, 상세페이지 자체는 DISCLOSURE_NO
  단위라도 fileNo를 덧붙여 한 공시에 첨부파일이 여러 개인 경우(실사 확인: 최대
  3개)에도 dedup_key가 겹치지 않게 한다.
- 검색어당 문서 유형이 고정되므로(예: "감사결과"→audit_result, "개별 비상임이사
  활동내용"→director_activity) `query`와 `doc_type`을 함께 생성자 인자로 받는다
  (2026-07-15 파라미터화 — 두 콘텐츠 유형이 section=attach 행 구조를 그대로
  공유함을 확인한 뒤 어댑터 자체를 확장했다, 별도 모듈 불필요). alio.go.kr의
  `q` 파라미터는 정확한 필터가 아니라 느슨한 텍스트 검색이라(실사 확인: `q=활동내역`
  같은 느슨한 단어는 무관한 문서까지 섞인다) 반드시 정확한 REPORT_FORM_NA 값을
  검색어로 써야 한다. `fetch_list()`가 각 행의 REPORT_FORM_NA를 보존하고
  `parse_detail()`이 생성자에 전달된 query와 대조해 불일치 시 quarantine 처리한다
  (30건 표본 검증만으로는 전체 수천 건의 순수성을 보장할 수 없다는 판단).
- ALIO는 전부 공개된 경영공시 자료라 비공개·부분공개 개념이 없다 —
  disclosure_status/cso_classification은 항상 OPEN/O.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import httpx
from bs4 import BeautifulSoup

from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.files import save_body_file
from rd2.storage.naming import DOC_TYPE_AUDIT_RESULT, SOURCE_ALIO

BASE_URL = "https://alio.go.kr"
SEARCH_URL = f"{BASE_URL}/search/findTotalSearch.json"
DOWNLOAD_URL = f"{BASE_URL}/download/file.json"
DETAIL_URL = f"{BASE_URL}/item/itemReport.do"
DEFAULT_QUERY = "감사결과"
PAGE_SIZE = 100

_TAG_RE = re.compile(r"</?b>")
_FILENAME_RE = re.compile(r'filename="*([^"]+)"*')


def _clean_title(raw_title: str) -> str:
    """검색어 하이라이트용 <b> 태그를 제거한다(예: '내부·외부 <b>감사결과</b>')."""
    return _TAG_RE.sub("", raw_title).strip()


def _parse_idate(idate: str | None) -> date | None:
    if not idate:
        return None
    try:
        return datetime.strptime(idate, "%Y.%m.%d").date()
    except ValueError:
        return None


def _parse_korean_date(text: str | None) -> date | None:
    """'2026년 07월 08일' 형태(doc.html의 제출일/기준일 값)를 파싱한다."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y년 %m월 %d일").date()
    except ValueError:
        return None


def _parse_content_disposition_filename(header_value: str | None, fallback: str) -> str:
    """'filename=""실제파일명.pdf"";' 형태(이중 따옴표)에서 파일명만 뽑는다."""
    if not header_value:
        return fallback
    match = _FILENAME_RE.search(header_value)
    return match.group(1) if match else fallback


def _doc_upload_base_url(disclosure_no: str) -> str:
    """DISCLOSURE_NO 앞 8자리(YYYYMMDD)로 상세 조각 HTML의 날짜 폴더를 구성한다
    (모듈 독스트링 참고 — 검색 API의 IDATE는 최대 하루 어긋나 못 쓴다)."""
    year, month, day = disclosure_no[:4], disclosure_no[4:6], disclosure_no[6:8]
    return f"{BASE_URL}/upload/disclosure/{year}/{month}/{day}/{disclosure_no}"


def _fetch_search_page(query: str, page: int, *, page_size: int = PAGE_SIZE) -> dict:
    response = httpx.get(
        SEARCH_URL,
        params={
            "q": query,
            "dsort": "11",  # 11=최신순, 10=오래된순 (app 인라인 스크립트 확인)
            "pg": page,
            "section": "attach",
            "apbaNm": "",
            "outmax": page_size,
        },
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def _fetch_fragment(url: str) -> str:
    response = httpx.get(url, timeout=15, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _fetch_doc_html(disclosure_no: str) -> str | None:
    """상세 조각 HTML을 가져온다. 404 등으로 없으면 None — 이 보강은 있으면
    좋은 정보라 실패해도 검색 API 필드만으로 계속 진행한다(모듈 독스트링 참고)."""
    url = f"{_doc_upload_base_url(disclosure_no)}/doc.html"
    try:
        return with_retry(lambda: _fetch_fragment(url))
    except httpx.HTTPStatusError:
        return None


def _fetch_toc_html(disclosure_no: str) -> str | None:
    url = f"{_doc_upload_base_url(disclosure_no)}/toc.html"
    try:
        return with_retry(lambda: _fetch_fragment(url))
    except httpx.HTTPStatusError:
        return None


def _find_label_value(soup: BeautifulSoup, label: str) -> str | None:
    """doc.html의 '라벨 셀 → 값 셀' 2열 표 구조에서 값을 뽑는다
    (예: <td>제목</td><td>실제 제목</td>)."""
    label_cell = soup.find("td", string=label)
    if not label_cell:
        return None
    value_cell = label_cell.find_next_sibling("td")
    return value_cell.get_text(strip=True) if value_cell else None


def _parse_doc_detail(html: str) -> dict:
    """doc.html 조각에서 보고서명/작성부서/제출일/기준일/본문 텍스트를 뽑는다.

    '기관 공시 담당자' 표는 구분(작성자/감독자/확인자)·담당자명·부서명·전화번호
    4열 구조다 — '작성자' 행의 부서명(2번째 다음 셀)을 department로 쓴다
    (실사 확인: 감사실 소속 작성자의 실제 부서, mohw의 담당부서 개념과 대응).
    """
    soup = BeautifulSoup(html, "html.parser")

    department = None
    writer_label_cell = soup.find("td", string="작성자")
    if writer_label_cell:
        name_cell = writer_label_cell.find_next_sibling("td")
        dept_cell = name_cell.find_next_sibling("td") if name_cell else None
        department = dept_cell.get_text(strip=True) if dept_cell else None

    return {
        "title": _find_label_value(soup, "제목"),
        "department": department,
        "submission_date_text": _find_label_value(soup, "제출일"),
        "reference_date_text": _find_label_value(soup, "기준일"),
        "body_text": soup.get_text("\n", strip=True) or None,
    }


def _parse_toc(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    items = [a.get_text(strip=True) for a in soup.select("li a")]
    return "\n".join(items) if items else None


def _download_file(disclosure_no: str, file_no: str, submission_no: str) -> tuple[bytes, str]:
    response = httpx.get(
        DOWNLOAD_URL,
        params={"f": file_no, "d": disclosure_no, "s": submission_no},
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    filename = _parse_content_disposition_filename(
        response.headers.get("content-disposition"),
        fallback=f"{disclosure_no}_{file_no}",
    )
    return response.content, filename


class AlioAdapter(SourceAdapter):
    source_name = SOURCE_ALIO

    def __init__(
        self,
        files_root: Path | None = None,
        query: str = DEFAULT_QUERY,
        doc_type: str = DOC_TYPE_AUDIT_RESULT,
    ):
        self.files_root = files_root or DEFAULT_FILES_ROOT
        self.query = query
        self.doc_type = doc_type

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """검색 결과를 페이지 단위(pg, outmax=100)로 순회한다. 목록 응답에는
        진짜 보고서명이 없어(모듈 독스트링 참고) parse_detail()이 상세 조각을
        따로 조회해야 한다 — 여기서는 다운로드/조회에 필요한 식별자만 담는다."""
        yielded = 0
        seen = 0
        page = 1
        while True:
            def _do_fetch(p: int = page) -> dict:
                return _fetch_search_page(self.query, p)

            payload = with_retry(_do_fetch)
            items = payload.get("data", {}).get("searchList") or []
            if not items:
                return

            for item in items:
                seen += 1
                if seen <= skip:
                    continue

                yield {
                    "_title": _clean_title(item.get("TITLE") or ""),
                    "_agency": item.get("APBA_NA"),
                    "_idate": item.get("IDATE"),
                    "_disclosure_no": item["DISCLOSURE_NO"],
                    "_submission_no": item["SUBMISSION_NO"],
                    "_file_no": item["FILE_NO"],
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            page += 1

    def parse_detail(self, raw_item: dict, *, download_files: bool = True) -> dict:
        """게시글 상세 조각(doc.html/toc.html)을 조회해 진짜 보고서명/작성부서/
        제출일/본문/목차를 채우고, 첨부파일 본체를 다운로드한다.

        alio.go.kr의 `q` 파라미터는 정확한 필터가 아니라 느슨한 텍스트 검색이라,
        검색 결과 행 중 실제로는 무관한 문서(다른 REPORT_FORM_NA)가 섞여 나올 수
        있다(실사 확인: q=활동내역 같은 느슨한 검색어는 무관 문서 수천 건을
        섞는다 — 모듈 독스트링 참고). 검색 결과 목록에 REPORT_FORM_NA 필드
        자체가 없어(section=attach 행은 항상 null, 실사 확인) TITLE(하이라이트
        태그 제거 후)로 매칭을 검증한다 — self.query를 부분문자열로 포함하지
        않으면 무관한 문서로 보고 quarantine 처리한다(예외를 던져 호출부의
        quarantine 로직을 태운다). exact match가 아니라 부분포함 검사인 이유는
        기존 감사결과 어댑터의 TITLE도 "내부·외부 감사결과"처럼 query와 정확히
        일치하지 않고 포함 관계이기 때문 — exact match면 기존 5,622건 수집
        경로가 전부 quarantine된다."""
        if self.query not in raw_item["_title"]:
            raise ValueError(
                f"검색어 불일치 — TITLE {raw_item['_title']!r}에 검색어 "
                f"{self.query!r}가 포함되지 않음 (느슨한 검색으로 섞인 무관 문서 의심)"
            )

        detail = dict(raw_item)
        disclosure_no = raw_item["_disclosure_no"]

        doc_html = _fetch_doc_html(disclosure_no)
        if doc_html:
            doc_fields = _parse_doc_detail(doc_html)
            if doc_fields.get("title"):
                detail["_title"] = doc_fields["title"]
            detail["_department"] = doc_fields.get("department")
            detail["_submission_date_text"] = doc_fields.get("submission_date_text")
            detail["_reference_date_text"] = doc_fields.get("reference_date_text")
            detail["_body_text"] = doc_fields.get("body_text")
        else:
            detail["_department"] = None
            detail["_submission_date_text"] = None
            detail["_reference_date_text"] = None
            detail["_body_text"] = None

        toc_html = _fetch_toc_html(disclosure_no)
        detail["_table_of_contents"] = _parse_toc(toc_html) if toc_html else None

        detail["_body_file_path"] = None
        if not download_files:
            return detail

        file_no = raw_item["_file_no"]
        submission_no = raw_item["_submission_no"]

        def _do_download() -> tuple[bytes, str]:
            return _download_file(disclosure_no, file_no, submission_no)

        raw_bytes, filename = with_retry(_do_download)
        identifier = f"{disclosure_no}_{file_no}"
        path = save_body_file(
            self.files_root,
            self.source_name,
            self.doc_type,
            identifier,
            filename,
            raw_bytes,
        )
        detail["_body_file_path"] = path
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        disclosure_no = enriched_item["_disclosure_no"]
        file_no = enriched_item["_file_no"]
        # source_url을 게시글 상세페이지 주소(itemReport.do)로 삼는다(2026-07-09
        # 사용자 요청). 상세페이지 자체는 DISCLOSURE_NO 단위(공시 1건)라, 한
        # 공시에 첨부파일이 여러 개인 경우(실사 확인: 3개까지) 구분이 안 되면
        # dedup_key(source+source_url)가 겹쳐 파일이 준복분으로 스킵된다 —
        # fileNo를 덧붙여 파일 단위 유일성을 유지한다(2026-07-09 사용자 결정:
        # 파일 단위 레코드 구조를 유지하고 source_url만 상세페이지 기반으로 교체).
        source_url = f"{DETAIL_URL}?seq={disclosure_no}&disclosureNo={disclosure_no}&fileNo={file_no}"

        # 제출일(doc.html)이 검색 API의 IDATE보다 더 정확한 생산일자다(둘 다
        # 있으면 제출일 우선) — doc.html 조회가 실패했을 때만 IDATE로 폴백한다.
        production_date = _parse_korean_date(
            enriched_item.get("_submission_date_text")
        ) or _parse_idate(enriched_item.get("_idate"))
        # 기준일→start_date, 제출일→end_date로 매핑한다(2026-07-09 사용자 결정):
        # 감사가 다루는 시점(기준일)과 실제 공개된 시점(제출일) 사이의 간격이
        # "문서 공개 판단에 걸린 시일"을 나타낸다는 판단 — 단순 단일 시점이 아니라
        # 기준일→제출일을 하나의 기간으로 본다.
        start_date = _parse_korean_date(enriched_item.get("_reference_date_text"))
        end_date = _parse_korean_date(enriched_item.get("_submission_date_text"))

        return Document(
            title=enriched_item["_title"],
            ordering_agency=enriched_item.get("_agency"),
            department=enriched_item.get("_department"),
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            body_text=enriched_item.get("_body_text"),
            body_file_path=enriched_item.get("_body_file_path"),
            table_of_contents=enriched_item.get("_table_of_contents"),
            start_date=start_date,
            end_date=end_date,
            cso_classification=CsoClassification.O,
            source=self.source_name,
            source_url=source_url,
            doc_type=self.doc_type,
            is_synthetic=False,
        )
