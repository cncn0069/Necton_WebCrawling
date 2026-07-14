"""대한민국 정책브리핑(korea.kr) 보도자료 게시판 어댑터.

RD-2 O트랙 신규 출처 — open.go.kr(정보공개포털)의 robots.txt 전체 차단(2026-07-13
확인, TODOS.md P0 항목 참고)으로 자동 수집을 중단한 뒤, 대체 소스를 조사하며 발견한
후보다. open.go.kr처럼 여러 기관을 한곳에서 통합 조회할 수 있으면서도, robots.txt가
`Allow: /` (전체 허용)이라 정책상 문제가 없다.

실제 사이트 조사(httpx 직접 요청 + WebFetch, 2026-07-13) 결과:
- `robots.txt`(www.korea.kr/robots.txt)는 `User-Agent: * / Allow: / / Disallow:`로
  전체 허용 — 코드 작성 전 실사로 확인 완료.
- 목록 페이지(`/briefing/pressReleaseList.do`)는 POST 폼이다(`pageIndex` 쿼리
  파라미터로는 안 바뀜 — 실사로 확인, GET으로 pageIndex=2를 줘도 페이지 1과 동일한
  응답이 옴). 실제 파라미터는 `pageIndex`/`repCodeType`/`repCode`/`startDate`/
  `endDate`/`srchWord`/`period` 전부를 폼 그대로 보내야 한다.
- **목록 페이지 안에 본문 전문이 이미 들어있다**(`span.lead`) — 상세페이지의 본문
  영역은 원본 파일을 변환한 `<iframe>` 뷰어(`/docViewer/iframe_skin/...`)라 정적
  HTML로 못 읽는다. 그래서 이 어댑터는 title/body_text/date/agency를 전부 목록
  단계에서 채우고, 상세페이지는 첨부파일 목록을 얻기 위해서만 조회한다(다른
  어댑터들과 책임 분배가 다름 — fetch_list가 이미 본문을 포함해 yield한다).
- 목록의 `div.list_type` 앞에 검색 필터 UI(체크박스 등)가 있어 반드시
  `div.list_type li a`로 범위를 좁혀야 한다 — 페이지 전체에서 `newsId=`를 찾으면
  "실시간 인기뉴스"/"정책포커스" 등 사이드바 위젯의 링크까지 섞여 든다(실사로 확인).
- 상세페이지엔 `application/ld+json` NewsArticle 구조화 데이터가 있어(`headline`/
  `datePublished`/`author.name`) 검증용으로 쓸 수 있지만, 목록 단계 값으로 이미
  충분해 이 어댑터는 참고만 하고 파싱 소스로는 쓰지 않는다(중복 요청 방지).
- 첨부파일은 상세페이지의 `div.filedown dl dd` 안에 있고, 다운로드 링크는
  `/common/download.do?fileId=<id>&tblKey=GMN` 형태다. 파일명은 `<a>` 안의
  아이콘 `<img>` 다음에 오는 텍스트 노드에 있다(예: "...pdf" 텍스트가
  `<img alt="PDF파일">` 바로 뒤).
- 같은 문서가 PDF/HWPX 등 여러 포맷으로 동시 제공되는 경우가 흔하다 —
  `pick_primary_file`로 제목과 가장 비슷한 파일명을 대표로 고른다(다른 어댑터와
  동일 원칙).
- 이 게시판은 정부가 능동적으로 배포하는 보도자료라 비공개/부분공개 개념이 없다
  — disclosure_status/cso_classification은 moe와 동일하게 항상 OPEN/O.
- doc_type은 단일 유형(press_release)으로 고정한다 — Document 스키마 docstring에
  이미 "보도자료"가 예시로 명시돼 있다.
- ordering_agency는 moe(고정값 "교육부")와 달리 **문서마다 다르다**(질병관리청,
  행정안전부, 농림축산식품부 등 — 정책브리핑이 전 부처 보도자료를 통합 배포하는
  사이트라서). department는 신뢰할 수 있는 소스가 없어(상세페이지의 "담당자안내"는
  팝업 JS라 정적 파싱 불가) None으로 둔다.
- 요청 사이 최소 지연은 moe/molit과 동일하게 둔다(서버 부담 고려).
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.file_select import pick_primary_file
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.files import resolve_body_file_path
from rd2.storage.naming import DOC_TYPE_PRESS_RELEASE, SOURCE_KOREA_KR

BASE_URL = "https://www.korea.kr"
LIST_URL = f"{BASE_URL}/briefing/pressReleaseList.do"
DETAIL_URL = f"{BASE_URL}/briefing/pressReleaseView.do"

# 사이트 운영자가 이 요청이 무엇인지 알아볼 수 있도록 식별 가능한 UA를 명시한다
# (moe/molit과 동일한 결정 — 연락처는 넣지 않음).
USER_AGENT = "RD2-Crawler/1.0"
_HEADERS = {"User-Agent": USER_AGENT}

# 요청 사이 최소 지연(초).
_REQUEST_DELAY_SECONDS = 0.6

_NEWS_ID_RE = re.compile(r"newsId=(\d+)")


def _throttle() -> None:
    time.sleep(_REQUEST_DELAY_SECONDS)


def _fetch_list_html(page: int, start_date: str, end_date: str) -> str:
    response = httpx.post(
        LIST_URL,
        data={
            "pageIndex": str(page),
            "repCodeType": "",
            "repCode": "",
            "startDate": start_date,
            "endDate": end_date,
            "srchWord": "",
            "period": "",
        },
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(news_id: str) -> str:
    response = httpx.get(
        DETAIL_URL,
        params={"newsId": news_id},
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _download_file_streaming(url: str, dest: Path) -> None:
    """moe/molit과 동일한 스트리밍 다운로드 패턴 — 전체를 메모리에 올리지 않는다."""
    with httpx.stream(
        "GET", url, headers=_HEADERS, timeout=120, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


class KoreaKrAdapter(SourceAdapter):
    source_name = SOURCE_KOREA_KR

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        start_date: str = "2000-01-01",
        end_date: str | None = None,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 페이지 단위로 순회한다. 목록 페이지 안에 본문 전문이 이미 있어
        (`span.lead`), 이 단계에서 title/body_text/date/agency를 전부 채워 yield한다
        — 상세페이지는 parse_detail()에서 첨부파일 조회용으로만 별도 호출한다."""
        end_date = end_date or date.today().isoformat()
        yielded = 0
        seen = 0
        page = 1
        while True:
            def _do_fetch(p: int = page) -> str:
                return _fetch_list_html(p, start_date, end_date)

            html = with_retry(_do_fetch)
            _throttle()
            soup = BeautifulSoup(html, "html.parser")
            list_container = soup.select_one("div.list_type")
            items = list_container.select("li > a[href*='pressReleaseView.do']") if list_container else []

            if not items:
                return

            for link in items:
                href = link.get("href") or ""
                match = _NEWS_ID_RE.search(href)
                if not match:
                    continue

                seen += 1
                if seen <= skip:
                    continue

                news_id = match.group(1)
                title_el = link.select_one("strong")
                lead_el = link.select_one("span.lead")
                source_spans = link.select("span.source > span")

                yield {
                    "_news_id": news_id,
                    "_title": title_el.get_text(strip=True) if title_el else None,
                    "_body_text": lead_el.get_text(" ", strip=True) if lead_el else None,
                    "_date_text": source_spans[0].get_text(strip=True) if len(source_spans) > 0 else None,
                    "_agency": source_spans[1].get_text(strip=True) if len(source_spans) > 1 else None,
                    "_detail_url": f"{DETAIL_URL}?newsId={news_id}",
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            page += 1

    def _download_and_save_files(
        self, news_id: str, title: str, file_list: list[dict]
    ) -> tuple[str | None, list[str]]:
        if not file_list:
            return None, []

        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            final_path = resolve_body_file_path(
                self.files_root,
                SOURCE_KOREA_KR,
                DOC_TYPE_PRESS_RELEASE,
                news_id,
                file_meta["filename"],
            )

            def _do_download(u: str = file_meta["href"], p: Path = final_path) -> None:
                _download_file_streaming(u, p)

            with_retry(_do_download)
            _throttle()
            relative_path = str(final_path.relative_to(self.files_root))
            saved.append((relative_path, file_meta))

        primary_meta = pick_primary_file(file_list, title, filename_key="filename")
        body_file_path = next(path for path, fm in saved if fm is primary_meta)
        other_file_paths = [path for path, fm in saved if fm is not primary_meta]
        return body_file_path, other_file_paths

    def parse_detail(self, raw_item: dict, *, download_files: bool = True) -> dict:
        news_id = raw_item["_news_id"]

        def _do_fetch() -> str:
            return _fetch_detail_html(news_id)

        html = with_retry(_do_fetch)
        _throttle()
        soup = BeautifulSoup(html, "html.parser")

        detail = dict(raw_item)

        file_list: list[dict] = []
        filedown = soup.select_one("div.filedown")
        if filedown:
            for a in filedown.select('a[href*="/common/download.do"]'):
                # 파일명은 <a> 안의 아이콘 <img> 다음 텍스트 노드에 있다
                # (예: '<img alt="PDF파일">파일명.pdf').
                text_parts = [c for c in a.contents if isinstance(c, str)]
                filename = "".join(text_parts).strip()
                if not filename:
                    continue
                file_list.append({"filename": filename, "href": urljoin(BASE_URL, a["href"])})

        # 같은 파일이 "바로보기"/"내려받기" 두 링크로 중복 등장하므로 href 기준 dedup.
        seen_hrefs: set[str] = set()
        deduped_files: list[dict] = []
        for fm in file_list:
            if fm["href"] in seen_hrefs:
                continue
            seen_hrefs.add(fm["href"])
            deduped_files.append(fm)

        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files and deduped_files:
            body_file_path, other_file_paths = self._download_and_save_files(
                news_id, detail.get("_title") or "", deduped_files
            )
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        title = enriched_item["_title"]

        production_date: date | None = None
        date_text = enriched_item.get("_date_text")
        if date_text:
            try:
                production_date = datetime.strptime(date_text, "%Y.%m.%d").date()
            except ValueError:
                pass

        return Document(
            title=title,
            ordering_agency=enriched_item.get("_agency") or "대한민국 정책브리핑",
            department=None,
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            body_text=enriched_item.get("_body_text") or None,
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            cso_classification=CsoClassification.O,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=DOC_TYPE_PRESS_RELEASE,
            is_synthetic=False,
        )
