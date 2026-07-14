"""고용노동부(moel.go.kr) 정책자료실 어댑터.

RD-2 O트랙 신규 출처 — `moel.py`(훈령·예규·고시)와 같은 사이트(moel.go.kr)지만
완전히 다른 게시판이다. 훈령·예규·고시는 법적 효력이 있는 행정규칙인 반면, 이
게시판은 정책 가이드북/업무매뉴얼/질의회시집처럼 **행정규칙이 아닌 정책 참고자료**를
다룬다(예: "26년 소규모 사업장을 위한 7가지 노른자 노동법", "중대재해처벌법
중대산업재해 질의회시집") — 그래서 별도 source(`moel_policy`)와 doc_type
(`policy_data`)으로 분리한다(기존 `SOURCE_*`가 어댑터 파일과 1:1 대응하는 관례를
유지 — `moel.py`에 이어붙이지 않음, `DOC_TYPES.md` "새 source/doc_type 추가하는 법"
참고).

실제 사이트 조사(httpx 직접 요청, 2026-07-14) 결과:
- `robots.txt`는 moel.py와 동일(`/policy/` 경로는 차단 목록에 없음 — 재확인 완료).
- 목록(`/policy/policydata/list.do`)도 moel.py와 동일하게 `?pageIndex=N` GET
  페이지네이션을 쓰지만, **컬럼 순서가 다르다**(이 게시판엔 "행정규칙번호" 컬럼이
  없다): 번호(0)/제목(1)/담당부서(2)/등록일(3)/첨부(4)/조회(5) — moel.py의
  번호(0)/행정규칙번호(1)/제목(2)/담당부서(3)/등록일(4)와 인덱스가 다르므로
  코드를 그대로 재사용하면 안 된다(moel.py 작성 때 이 컬럼 인덱스를 한 번
  잘못 짚어 테스트가 잡아낸 적이 있다 — 이번엔 처음부터 실제 HTML로 검증).
- 상세페이지 구조는 moel.py와 거의 같다(제목/등록일/담당부서/담당자/전화번호
  dt-dd, `div.b_content` 본문, `div.file` 첨부) — 단 **"유형"(고시/훈령/예규)
  필드가 없다** — 이 게시판은 문서유형이 애초에 하나뿐이라 doc_type을 고정값
  (`policy_data`)으로 둔다(moe.py와 같은 성격의 판단).
- 담당자 실명/전화번호는 moel.py와 동일한 원칙으로 Document 어디에도 남기지
  않는다.
- 첨부파일은 "다운로드"/"바로보기" 두 링크가 같은 파일을 가리켜 중복 등장한다
  (moel.py와 동일한 함정) — href 기준 dedup 필요.
- 목록 종료 조건: 다음 페이지에 행이 0개면 멈춘다(다른 어댑터와 동일 원칙).

**문서유형이 실제로는 하나가 아니다** — 처음엔 doc_type을 고정값으로 두려 했으나,
5페이지(50건) 실사 결과 이 게시판은 "정책자료실"이라는 이름과 달리 성격이 다른
문서가 섞여 있었다(mohw.py의 입찰공고 게시판과 같은 상황 — 이름만 보고 고정값을
쓰면 안 된다는 교훈이 여기서도 반복됨, 2026-07-14). `_infer_doc_type(title)`이
제목 키워드로 판정한다(우선순위는 아래 함수 참고).
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
    DOC_TYPE_GUIDE,
    DOC_TYPE_INTERPRETATION_COMPILATION,
    DOC_TYPE_NOTICE,
    DOC_TYPE_STATUS_REPORT,
    SOURCE_MOEL_POLICY,
)

BASE_URL = "https://www.moel.go.kr"
LIST_URL = f"{BASE_URL}/policy/policydata/list.do"
DETAIL_URL = f"{BASE_URL}/policy/policydata/view.do"

# 제목 키워드 → doc_type. 5페이지(50건) 실사(2026-07-14)로 확인한 우선순위 —
# 구체적인 키워드를 먼저 검사해야 한다(mohw.py의 "사전규격"을 "공고"보다 먼저
# 검사하는 원칙과 동일).
_GUIDE_KEYWORDS = ("가이드", "매뉴얼", "지침", "길잡이", "안내서", "사례집", "수첩")
_STATUS_KEYWORDS = ("현황", "보고서")


def _infer_doc_type(title: str) -> str:
    """제목에 "질의회시집" 포함? → interpretation_compilation (가장 먼저 검사)
    가이드/매뉴얼/지침/길잡이/안내서/사례집/수첩 포함? → guide
    현황/보고서 포함? → status_report
    그 외 → notice (최종 폴백 — 안내/공지/게시 등)"""
    if "질의회시집" in title:
        return DOC_TYPE_INTERPRETATION_COMPILATION
    if any(kw in title for kw in _GUIDE_KEYWORDS):
        return DOC_TYPE_GUIDE
    if any(kw in title for kw in _STATUS_KEYWORDS):
        return DOC_TYPE_STATUS_REPORT
    return DOC_TYPE_NOTICE

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
    """moel.py와 동일한 스트리밍 다운로드 패턴."""
    with httpx.stream(
        "GET", url, headers=_HEADERS, timeout=120, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


class MoelPolicyAdapter(SourceAdapter):
    source_name = SOURCE_MOEL_POLICY

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

                # 실제 컬럼 순서(실사 2026-07-14, moel.py와 다름 — 상단 독스트링
                # 참고): 번호(0)/제목(1)/담당부서(2)/등록일(3)/첨부(4)/조회(5).
                title = link.get_text(" ", strip=True)
                yield {
                    "_bbs_seq": bbs_seq,
                    "_title": title,
                    "_department_list": cells[2].get_text(strip=True) if len(cells) > 2 else None,
                    "_date_text": cells[3].get_text(strip=True) if len(cells) > 3 else None,
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
                SOURCE_MOEL_POLICY,
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
        # href 기준 dedup(moel.py와 동일한 함정·해법).
        seen_hrefs: set[str] = set()
        deduped_files: list[dict] = []
        for fm in file_list:
            if fm["href"] in seen_hrefs:
                continue
            seen_hrefs.add(fm["href"])
            deduped_files.append(fm)

        doc_type = _infer_doc_type(detail["_title"])
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
            doc_type=enriched_item.get("_doc_type") or _infer_doc_type(title),
            is_synthetic=False,
        )
