"""고용노동부(moel.go.kr) 훈령·예규·고시 어댑터.

RD-2 O트랙 신규 출처 — me.py(기후에너지환경부 행정규칙)와 같은 문서유형
카테고리(고시/훈령/예규)를 다루지만, me.py와 달리 **본문 전문과 첨부파일을 둘 다
제공**한다(me.py는 law.go.kr 인증 API 없이는 본문을 못 받아 메타데이터 전용으로
설계했었다 — TODOS.md/ARCHITECTURE.md 참고). 같은 doc_type 상수(notification/
directive/regulation, `naming.py`)를 재사용해 소스만 다르게 등록한다 — mohw/molit
등 여러 소스가 doc_type을 공유하는 기존 설계와 동일한 패턴.

실제 사이트 조사(httpx 직접 요청, 2026-07-14) 결과:
- `robots.txt`(moel.go.kr/robots.txt)는 `/info/defaulter/`, `/portal/`,
  `/v2024/` 등만 금지하고 `/info/lawinfo/`, `/common/downloadFile.do`는
  막지 않는다 — 코드 작성 전 실사로 확인 완료(2026-07-13에 이미 한 번 확인,
  오늘 재확인).
- 목록(`/info/lawinfo/instruction/list.do`)은 `?pageIndex=N`(1부터) GET
  파라미터로 페이지네이션한다 — 세션/폼 제출 없이 단순 GET으로 동작(실사 확인).
- **목록 페이지 자체에 문서유형(고시/훈령/예규)이 제목 앞 대괄호로 이미
  표시된다**(예: "[훈령] 고용노동부 성희롱..."). 다만 이 어댑터는 me.py와 동일하게
  상세페이지의 "유형" 필드(dt/dd 구조, 대괄호 파싱보다 안정적)로 doc_type을
  최종 결정한다 — 대괄호 파싱은 제목 자체에 대괄호가 포함된 예외 케이스에
  취약할 수 있어 더 신뢰할 수 있는 소스를 우선한다.
- 상세페이지엔 "담당자"(실명)와 "전화번호" 필드가 있다 — moe.py가 담당부서
  셀의 전화번호를 제외했던 것과 같은 원칙으로, 이 어댑터는 담당부서만 취하고
  담당자 실명/전화번호는 Document 어디에도 남기지 않는다.
- 본문은 `div.b_content`에 있고, 발령기관·발령일자·법적근거 등이 자유 텍스트로
  섞여 있어(예: "고용노동부고시 제2025-119호" 같은 줄) 별도로 구조화하지 않고
  본문 그대로 저장한다.
- 첨부파일 다운로드 링크는 `/common/downloadFile.do?file_seq=...&bbs_seq=...
  &bbs_id=...&file_ext=...` 형태 — 파일명은 아이콘 옆 `<a>` 텍스트에 그대로 있어
  moe.py 같은 "첫 줄만 취하기" 보정이 필요 없다(실사로 확인, 항상 순수 파일명).
- 목록 종료 조건: 다음 페이지에 행이 0개면 멈춘다(다른 어댑터와 동일 원칙).
"""

from __future__ import annotations

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
from rd2.storage.naming import (
    DOC_TYPE_DIRECTIVE,
    DOC_TYPE_NOTIFICATION,
    DOC_TYPE_REGULATION,
    SOURCE_MOEL,
)

BASE_URL = "https://www.moel.go.kr"
LIST_URL = f"{BASE_URL}/info/lawinfo/instruction/list.do"
DETAIL_URL = f"{BASE_URL}/info/lawinfo/instruction/view.do"

# 상세페이지 "유형" 필드값 → doc_type 코드. me.py와 동일한 매핑을 공유한다.
_ADMIN_RULE_TYPE_MAP = {
    "고시": DOC_TYPE_NOTIFICATION,
    "훈령": DOC_TYPE_DIRECTIVE,
    "예규": DOC_TYPE_REGULATION,
}

# 사이트 운영자가 이 요청이 무엇인지 알아볼 수 있도록 식별 가능한 UA를 명시한다.
USER_AGENT = "RD2-Crawler/1.0"
_HEADERS = {"User-Agent": USER_AGENT}

# 요청 사이 최소 지연(초).
_REQUEST_DELAY_SECONDS = 0.6


def _throttle() -> None:
    time.sleep(_REQUEST_DELAY_SECONDS)


def _fetch_list_html(page: int) -> str:
    response = httpx.get(
        LIST_URL,
        params={"pageIndex": page},
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(bbs_seq: str) -> str:
    response = httpx.get(
        DETAIL_URL,
        params={"bbs_seq": bbs_seq},
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _download_file_streaming(url: str, dest: Path) -> None:
    """moe/molit/korea_kr과 동일한 스트리밍 다운로드 패턴."""
    with httpx.stream(
        "GET", url, headers=_HEADERS, timeout=120, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


class MoelAdapter(SourceAdapter):
    source_name = SOURCE_MOEL

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 pageIndex 단위(1부터)로 순회한다."""
        yielded = 0
        seen = 0
        page = 1
        while True:
            def _do_fetch(p: int = page) -> str:
                return _fetch_list_html(p)

            html = with_retry(_do_fetch)
            _throttle()
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("tbody tr")

            found_row = False
            for row in rows:
                link = row.select_one('a[href*="view.do?bbs_seq="]')
                if not link:
                    continue
                cells = row.select("td")
                if len(cells) < 4:
                    continue

                found_row = True
                seen += 1
                if seen <= skip:
                    continue

                href = link.get("href") or ""
                bbs_seq = None
                for part in href.split("?", 1)[-1].split("&"):
                    if part.startswith("bbs_seq="):
                        bbs_seq = part.split("=", 1)[1]
                        break
                if not bbs_seq:
                    continue

                # 실제 컬럼 순서(실사 2026-07-14): 번호(0)/행정규칙번호(1)/제목(2)/
                # 담당부서(3)/등록일(4)/첨부(5)/조회(6).
                title = link.get_text(" ", strip=True)
                yield {
                    "_bbs_seq": bbs_seq,
                    "_title": title,
                    "_department_list": cells[3].get_text(strip=True) if len(cells) > 3 else None,
                    "_date_text": cells[4].get_text(strip=True) if len(cells) > 4 else None,
                    "_detail_url": f"{DETAIL_URL}?bbs_seq={bbs_seq}",
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            if not found_row:
                return
            page += 1

    def _download_and_save_files(
        self, bbs_seq: str, doc_type: str, title: str, file_list: list[dict]
    ) -> tuple[str | None, list[str]]:
        if not file_list:
            return None, []

        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            final_path = resolve_body_file_path(
                self.files_root,
                SOURCE_MOEL,
                doc_type,
                bbs_seq,
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
        bbs_seq = raw_item["_bbs_seq"]

        def _do_fetch() -> str:
            return _fetch_detail_html(bbs_seq)

        html = with_retry(_do_fetch)
        _throttle()
        soup = BeautifulSoup(html, "html.parser")

        detail = dict(raw_item)

        def _field(label: str) -> str | None:
            dt = soup.find("dt", string=lambda s: bool(s and s.strip() == label))
            if not dt:
                return None
            dd = dt.find_next_sibling("dd")
            return dd.get_text(strip=True) if dd else None

        title_val = _field("제목")
        if title_val is None:
            raise ValueError(f"상세페이지 파싱 실패(메타데이터 없음): bbs_seq={bbs_seq}")

        detail["_title"] = title_val
        detail["_rule_type"] = _field("유형")
        detail["_department"] = _field("담당부서")
        detail["_date_text"] = _field("등록일") or detail.get("_date_text")

        content = soup.select_one("div.b_content")
        detail["_body_text"] = content.get_text(" ", strip=True) if content else None

        file_list: list[dict] = []
        file_div = soup.select_one("div.file")
        if file_div:
            for a in file_div.select('a[href*="/common/downloadFile.do"]'):
                filename = a.get_text(strip=True)
                if not filename:
                    continue
                file_list.append({"filename": filename, "href": urljoin(BASE_URL, a["href"])})

        # "다운로드"/"바로보기" 두 링크가 같은 파일을 가리켜 중복 등장하므로
        # href 기준 dedup(korea_kr.py와 동일한 함정·해법).
        seen_hrefs: set[str] = set()
        deduped_files: list[dict] = []
        for fm in file_list:
            if fm["href"] in seen_hrefs:
                continue
            seen_hrefs.add(fm["href"])
            deduped_files.append(fm)

        doc_type = _ADMIN_RULE_TYPE_MAP.get(detail.get("_rule_type") or "", DOC_TYPE_NOTIFICATION)
        detail["_doc_type"] = doc_type

        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files and deduped_files:
            body_file_path, other_file_paths = self._download_and_save_files(
                bbs_seq, doc_type, detail["_title"] or "", deduped_files
            )
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        title = enriched_item["_title"]

        production_date: date | None = None
        date_text = enriched_item.get("_date_text")
        if date_text:
            for fmt in ("%Y-%m-%d", "%Y.%m.%d"):
                try:
                    production_date = datetime.strptime(date_text, fmt).date()
                    break
                except ValueError:
                    continue

        return Document(
            title=title,
            ordering_agency="고용노동부",
            department=enriched_item.get("_department"),
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            body_text=enriched_item.get("_body_text") or None,
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            cso_classification=CsoClassification.O,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=enriched_item.get("_doc_type") or DOC_TYPE_NOTIFICATION,
            is_synthetic=False,
        )
