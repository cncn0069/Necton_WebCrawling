"""보건복지부 입찰안내 게시판(mohw.go.kr, bid=0025) 어댑터.

RD-2 O트랙 신규 출처. 실제 사이트 조사(httpx 직접 요청, 2026-07-08) 결과:
- 전자정부 표준프레임워크 게시판(`board.es`/`boardDownload.es`)이라 PRISM과 달리
  봇탐지·JS렌더링이 없다 — 목록/상세 모두 순수 httpx GET으로 그대로 읽힌다.
  browse_client(헤드리스 브라우저)가 필요 없는 첫 어댑터.
- 목록 행에 `list_no`가 담긴 상세 링크가 정적으로 노출되어 있어(PRISM의
  "클릭해야만 URL을 알 수 있는" SPA 문제 없음), 첨부파일도
  `/boardDownload.es?bid=0025&list_no=..&seq=N` 고정 URL로 접근 제어 없이
  바로 받을 수 있다(PRISM의 클릭 검증/차단 로직이 여기선 불필요).
- 게시판 성격상 전부 공개 입찰/사전규격공개/공모 공고문이라 비공개·부분공개
  개념 자체가 없다 — disclosure_status/cso_classification은 항상 OPEN/O.
- 이 게시판은 입찰공고/사전규격공개/공모/재공고가 섞여 있어(15건 샘플로 확인)
  doc_type을 PRISM처럼 고정값으로 두면 안 되고, 제목 키워드로 추론한다
  (2026-07-08 사용자 지적 — plan 리뷰에서 "실제로 입찰 공고가 아닌데 고정값이면
  안 된다"는 피드백 반영).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.file_select import pick_primary_file
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.files import save_body_file
from rd2.storage.naming import (
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_BID_RENOTICE,
    DOC_TYPE_NOTICE,
    DOC_TYPE_PRE_SPEC_NOTICE,
    DOC_TYPE_PUBLIC_OFFERING,
    SOURCE_MOHW,
)

BASE_URL = "https://www.mohw.go.kr"
LIST_URL = f"{BASE_URL}/board.es"
MID = "a10502000000"
BID = "0025"

# 우선순위 순서대로 검사 — "사전규격"이 있으면 "공고"로 끝나도 사전규격공개가 맞다
# (예: "[사전규격공개] 전자바우처 통합카드사업"에는 "공고"라는 글자 자체가 없지만,
# 만약 있었다 해도 사전규격공개가 더 구체적인 유형이라 먼저 검사한다).
_DOC_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("사전규격", DOC_TYPE_PRE_SPEC_NOTICE),
    ("재공고", DOC_TYPE_BID_RENOTICE),
    ("공모", DOC_TYPE_PUBLIC_OFFERING),
]


def _infer_doc_type(title: str) -> str:
    """제목 키워드로 문서 유형을 추론한다. 이 게시판은 입찰공고 게시판이라는
    이름과 달리 사전규격공개/공모/재공고 등이 섞여 있어 고정값을 쓸 수 없다
    (실사 15건 샘플 기준 — adapters/conformance.py나 doc_type 확장 필요 시
    이 함수와 _DOC_TYPE_KEYWORDS만 고치면 된다)."""
    for keyword, doc_type in _DOC_TYPE_KEYWORDS:
        if keyword in title:
            return doc_type
    if "입찰" in title or title.rstrip().endswith("공고"):
        return DOC_TYPE_BID_NOTICE
    return DOC_TYPE_NOTICE


def _parse_period(period_text: str | None) -> tuple[date | None, date | None]:
    """"제안서제출기간" 값을 시작/종료일로 나눈다. None을 반환하는 게 정상인 경우가
    실제로 많다 — 2026-07-08 전수 크롤링(733건 샘플)으로 확인: 2021년 이후 게시물은
    거의 다 채워져 있지만, 2012~2020년 게시물(이 게시판 물량의 대부분)은 상세페이지에
    "제안서제출기간" 항목 자체는 있으나 사이트가 값을 비워둔 채(`<span> ~ </span>`)
    등록해놨다 — 사이트 템플릿이 그 시절엔 이 필드를 안 쓴 것으로 보이며, 어댑터
    파싱 버그가 아니다(원문 HTML 직접 확인함). 그래서 conformance.py의 보건복지부
    계약에서도 start_date/end_date를 always_filled에 넣지 않는다."""
    if not period_text or "~" not in period_text:
        return None, None
    start_str, end_str = (p.strip() for p in period_text.split("~", 1))
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except ValueError:
        return None, None
    return start, end


def _fetch_list_html(page: int) -> str:
    """목록 페이지 요청. browse_client가 필요 없어 이 모듈에 직접 둔다 —
    테스트에서는 이 함수를 monkeypatch한다(browse_client 기반 어댑터들과 동일 패턴)."""
    response = httpx.get(
        LIST_URL, params={"mid": MID, "bid": BID, "nPage": page}, timeout=15, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(url: str) -> str:
    response = httpx.get(url, timeout=15, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _download_file_bytes(url: str) -> bytes:
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.content


class MohwAdapter(SourceAdapter):
    source_name = SOURCE_MOHW

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 페이지 단위(nPage)로 순회한다. 이 게시판은 날짜 range 검색 UI가
        없어 start_date/end_date 파라미터는 두지 않는다(PRISM도 인터페이스만
        있고 실질 미사용인 것과 동일한 이유)."""
        yielded = 0
        seen = 0
        page = 1
        while True:
            def _do_fetch(p: int = page) -> str:
                return _fetch_list_html(p)

            html = with_retry(_do_fetch)
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("tbody tr")
            if not rows:
                return

            for row in rows:
                title_cell = row.find("td", attrs={"data-label": "제목"})
                link = title_cell.find("a") if title_cell else None
                if not link or not link.get("href"):
                    continue

                seen += 1
                if seen <= skip:
                    continue

                href = link["href"]
                list_no = parse_qs(urlparse(href).query).get("list_no", [None])[0]
                if not list_no:
                    continue

                date_cell = row.find("td", attrs={"data-label": "등록일"})
                yield {
                    "_list_no": list_no,
                    "_title": link.get_text(strip=True),
                    "_detail_url": urljoin(BASE_URL, href),
                    "_list_date": date_cell.get_text(strip=True) if date_cell else None,
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            page += 1

    def _download_and_save_files(
        self, list_no: str, title: str, file_list: list[dict]
    ) -> tuple[str | None, list[str]]:
        """이 게시판은 PRISM과 달리 클릭 검증(alert 차단 감지)이 필요 없다 —
        boardDownload.es가 나열하는 파일을 그대로 받아도 사이트의 의도된 접근
        제어를 우회하는 게 아니다(전부 공개 다운로드, 모듈 독스트링 참고)."""
        if not file_list:
            return None, []

        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            def _do_download(u: str = file_meta["href"]) -> bytes:
                return _download_file_bytes(u)

            raw_bytes = with_retry(_do_download)
            path = save_body_file(
                self.files_root,
                self.source_name,
                _infer_doc_type(title),
                list_no,
                file_meta["filename"],
                raw_bytes,
            )
            saved.append((path, file_meta))

        primary_meta = pick_primary_file(file_list, title, filename_key="filename")
        body_file_path = next(path for path, fm in saved if fm is primary_meta)
        other_file_paths = [path for path, fm in saved if fm is not primary_meta]
        return body_file_path, other_file_paths

    def parse_detail(self, raw_item: dict, *, download_files: bool = True) -> dict:
        url = raw_item["_detail_url"]

        def _do_fetch() -> str:
            return _fetch_detail_html(url)

        html = with_retry(_do_fetch)
        soup = BeautifulSoup(html, "html.parser")

        def _info(label: str) -> str | None:
            strong = soup.find("strong", string=label)
            if not strong:
                return None
            span = strong.find_next_sibling("span")
            return span.get_text(strip=True) if span else None

        detail = dict(raw_item)
        title_el = soup.select_one("article.board_view h2.title")
        detail["_title"] = title_el.get_text(strip=True) if title_el else raw_item.get("_title")
        detail["_production_datetime"] = _info("작성일")
        detail["_department"] = _info("담당부서")
        detail["_period_text"] = _info("제안서제출기간")

        contents = soup.select_one("div.contents")
        detail["_body_text"] = contents.get_text("\n", strip=True) if contents else None

        file_list: list[dict] = []
        file_section = soup.select_one("div.file")
        if file_section:
            for a in file_section.select("a.btn_line"):
                href = a.get("href") or ""
                if "boardDownload.es" not in href:
                    continue
                filename = a.get("title")
                if not filename:
                    continue
                file_list.append({"filename": filename, "href": urljoin(BASE_URL, href)})

        list_no = raw_item["_list_no"]
        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files and file_list:
            body_file_path, other_file_paths = self._download_and_save_files(
                list_no, detail["_title"] or "", file_list
            )
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        title = enriched_item["_title"]

        production_date = None
        production_datetime = enriched_item.get("_production_datetime")
        if production_datetime:
            try:
                production_date = datetime.strptime(
                    production_datetime.split()[0], "%Y-%m-%d"
                ).date()
            except ValueError:
                pass

        start_date_val, end_date_val = _parse_period(enriched_item.get("_period_text"))

        return Document(
            title=title,
            ordering_agency="보건복지부",
            department=enriched_item.get("_department"),
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            body_text=enriched_item.get("_body_text") or None,
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            cso_classification=CsoClassification.O,
            start_date=start_date_val,
            end_date=end_date_val,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=_infer_doc_type(title),
            is_synthetic=False,
        )
