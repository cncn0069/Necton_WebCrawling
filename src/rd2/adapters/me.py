"""기후에너지환경부(me.go.kr) 행정규칙(고시·훈령·예규) 어댑터.

RD-2 O트랙 신규 출처 — korea_kr(보도자료) 다음으로 추가한 두 번째 대체 소스.
korea_kr은 여러 기관을 다루지만 문서유형이 press_release 하나뿐이라, 이 어댑터는
문서유형 다양성을 채우기 위해 의도적으로 "행정규칙"이라는 완전히 다른 성격의
문서(법적 효력이 있는 행정 내부 규범 — 뉴스성 보도자료나 입찰공고와 무관)를
고른다.

실제 사이트 조사(httpx 직접 요청, 2026-07-14) 결과:
- `robots.txt`(me.go.kr/robots.txt)는 `/upload/`, `/search/`, `/api/` 등
  일부 경로만 금지하고 이 게시판(`/home/web/law/`)은 막지 않는다 — 코드 작성
  전 실사로 확인 완료.
- 이 페이지는 기후에너지환경부 자체 데이터가 아니라 **법제처 국가법령정보센터
  (law.go.kr)와 연계된 데이터를 정적 HTML로 미러링**한 것이다(페이지 안내문:
  "기후에너지환경부 고시·훈령·예규는 법제처의 국가법령정보센터와 연계하여
  서비스하고 있습니다"). law.go.kr 자체는 검색 UI가 AJAX 기반이라 파싱이
  번거로운데, 이 미러 페이지는 서버사이드 정적 HTML이라 훨씬 간단하다.
- 목록(`/home/web/law/list.do`)은 GET 파라미터 `pagerOffset`(10씩 증가)로
  페이지네이션한다 — `jsessionid`가 URL에 붙어 있지만 없어도 동작한다(실사로
  확인, 세션 의존 없음).
- 목록 페이지에는 순번/제목/발령일자/발령번호/소관부서명 5개 컬럼만 있고
  **문서유형(고시/훈령/예규) 구분은 상세페이지에만 있다** — 그래서 상세페이지를
  반드시 조회해야 doc_type을 정할 수 있다(list_type 우선 최적화 불가).
- **본문 전문이 없다** — 상세페이지는 메타데이터(행정규칙일련번호/행정규칙명/
  행정규칙종류/발령일자/발령번호/소관부서명/현행연혁구분/제개정구분명)만 제공하고,
  실제 조문 본문은 law.go.kr의 인증 API(`DRF/lawService.do?OC=...`)로만 열람
  가능하다(OC 키는 전화 신청 필요 — 자동 발급 아님, 2026-07-14 확인). open_go_kr의
  `official_document`(공문) doc_type과 동일한 성격의 "메타데이터 전용" 어댑터로
  설계한다 — body_text/body_file_path는 항상 None.
- 발령일자는 목록 페이지에서 `YYYY-MM-DD` 형식으로 이미 나온다(상세페이지는
  `YYYYMMDD` 구분자 없는 형식이라 목록 값을 그대로 쓰는 게 더 쉽다).
- 이 게시판은 정부가 공식 제정·공포한 행정규칙이라 비공개/부분공개 개념이 없다
  — moe/korea_kr과 동일하게 disclosure_status/cso_classification은 항상 OPEN/O.
- 목록 종료 조건: 다음 페이지에 행이 0개면 멈춘다(다른 어댑터와 동일 원칙).
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import httpx
from bs4 import BeautifulSoup

from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.naming import (
    DOC_TYPE_DIRECTIVE,
    DOC_TYPE_NOTIFICATION,
    DOC_TYPE_REGULATION,
    SOURCE_ME,
)

BASE_URL = "https://me.go.kr"
LIST_URL = f"{BASE_URL}/home/web/law/list.do"
DETAIL_URL = f"{BASE_URL}/home/web/law/read.do"

MENU_ID = "71"
PAGE_SIZE = 10

# "행정규칙종류" 필드값(고시/훈령/예규) → doc_type 코드.
_ADMIN_RULE_TYPE_MAP = {
    "고시": DOC_TYPE_NOTIFICATION,
    "훈령": DOC_TYPE_DIRECTIVE,
    "예규": DOC_TYPE_REGULATION,
}

# 사이트 운영자가 이 요청이 무엇인지 알아볼 수 있도록 식별 가능한 UA를 명시한다
# (moe/molit/korea_kr과 동일한 결정 — 연락처는 넣지 않음).
USER_AGENT = "RD2-Crawler/1.0"
_HEADERS = {"User-Agent": USER_AGENT}

# 요청 사이 최소 지연(초).
_REQUEST_DELAY_SECONDS = 0.6


def _throttle() -> None:
    time.sleep(_REQUEST_DELAY_SECONDS)


def _fetch_list_html(offset: int) -> str:
    response = httpx.get(
        LIST_URL,
        params={
            "maxPageItems": PAGE_SIZE,
            "menuId": MENU_ID,
            "condition.typeCode": "admrul",
            "pagerOffset": offset,
        },
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(law_seq: str) -> str:
    response = httpx.get(
        DETAIL_URL,
        params={
            "menuId": MENU_ID,
            "condition.typeCode": "admrul",
            "typeCode": "admrul",
            "lawSeq": law_seq,
        },
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


class MeAdapter(SourceAdapter):
    source_name = SOURCE_ME

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 pagerOffset 단위(10씩)로 순회한다. 문서유형(고시/훈령/예규)은
        목록에 없어 parse_detail()에서 채운다."""
        yielded = 0
        seen = 0
        offset = 0
        while True:
            def _do_fetch(o: int = offset) -> str:
                return _fetch_list_html(o)

            html = with_retry(_do_fetch)
            _throttle()
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("tbody tr")

            found_row = False
            for row in rows:
                link = row.select_one('a[href*="law/read.do"]')
                if not link:
                    continue
                cells = row.select("td")
                if len(cells) < 5:
                    continue

                found_row = True
                seen += 1
                if seen <= skip:
                    continue

                href = link.get("href") or ""
                law_seq = None
                for part in href.split("?", 1)[-1].split("&"):
                    if part.startswith("lawSeq="):
                        law_seq = part.split("=", 1)[1]
                        break
                if not law_seq:
                    continue

                yield {
                    "_law_seq": law_seq,
                    "_title": link.get_text(strip=True),
                    "_date_text": cells[2].get_text(strip=True),
                    "_rule_number": cells[3].get_text(strip=True),
                    "_department": cells[4].get_text(strip=True),
                    "_detail_url": (
                        f"{DETAIL_URL}?menuId={MENU_ID}&condition.typeCode=admrul"
                        f"&typeCode=admrul&lawSeq={law_seq}"
                    ),
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            if not found_row:
                return
            offset += PAGE_SIZE

    def parse_detail(self, raw_item: dict) -> dict:
        law_seq = raw_item["_law_seq"]

        def _do_fetch() -> str:
            return _fetch_detail_html(law_seq)

        html = with_retry(_do_fetch)
        _throttle()
        soup = BeautifulSoup(html, "html.parser")

        detail = dict(raw_item)

        def _field(label: str) -> str | None:
            dt = soup.find("dt", string=label)
            if not dt:
                return None
            dd = dt.find_next_sibling("dd")
            return dd.get_text(strip=True) if dd else None

        if soup.find("dt", string="행정규칙명") is None:
            raise ValueError(f"상세페이지 파싱 실패(메타데이터 없음): lawSeq={law_seq}")

        detail["_rule_type"] = _field("행정규칙종류")
        detail["_serial_number"] = _field("행정규칙일련번호")
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        title = enriched_item["_title"]

        production_date: date | None = None
        date_text = enriched_item.get("_date_text")
        if date_text:
            try:
                production_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except ValueError:
                pass

        doc_type = _ADMIN_RULE_TYPE_MAP.get(enriched_item.get("_rule_type") or "", DOC_TYPE_NOTIFICATION)

        return Document(
            title=title,
            ordering_agency=enriched_item.get("_department") or "기후에너지환경부",
            department=None,
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            body_text=None,
            body_file_path=None,
            other_file_paths=[],
            cso_classification=CsoClassification.O,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=doc_type,
            is_synthetic=False,
        )
