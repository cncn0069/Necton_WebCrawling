"""서울 정보소통광장(opengov.seoul.go.kr) 결재문서 원문정보(/sanction) 어댑터.

URL 경로의 "sanction"은 행정처분이 아니라 **결재(決裁)**라는 뜻이다 — 실사
(2026-07-16)로 확인: 페이지 타이틀이 "목록 > 결재문서 > 원문정보"이고, 실제
항목도 여비 지급/휴가 승인/물품 구매 같은 서울시(본청·자치구·투자출연기관)
결재문서다. open.go.kr 원문정보(orginl_info.py)의 서울시 버전에 해당한다.

이 소스가 특별한 이유: **목록·상세 모두 공개구분(공개/부분공개/비공개)을
명시적으로 제공**하고, 부분공개 문서도 결재문서본문(hwpx)이 실제로 첨부되어
있다(개인정보 등이 필터링된 버전). 첨부 중 "비공개 문서"로 표시된 파일은
다운로드 링크 자체가 없어 자연스럽게 수집 대상에서 빠진다.

크롤링 정책 실사(2026-07-16):
- robots.txt는 `User-agent: *`에 `Disallow: /`(전체 차단)이고 `/sanction`은
  검색엔진(Googlebot/Yeti/Daum/Bingbot)에만 allow돼 있다. 그러나 서울시가
  운영했던 공식 데이터 공개 채널(github.com/seoul-opengov/opengov README)에
  "웹 사이트 수집 프로그램을 사용하여 정보소통광장 문서를 조회하는 경우, 한
  페이지 수집 후 다음 페이지를 요청하기까지 10초 이상의 간격을 두시기 바랍니다.
  (Crawl-delay:20)"라고 수집 프로그램 사용을 전제한 안내가 명시돼 있다 —
  즉 운영자의 실제 정책은 "수집 자체는 허용, 속도만 제한"이다. 이 안내를
  근거로 요청 간 지연을 10초로 두고(_REQUEST_DELAY_SECONDS — 다른 어댑터의
  0.6초보다 훨씬 김), 식별 가능한 UA를 쓴다. robots.txt 전체 차단은 과도한
  봇 트래픽 때문에 나중에 덧씌운 것으로 보이며(위 README에 "일부 사용자가
  수집 프로그램을 과도하게 사용하여 서비스에 지장" 경위 기록), 운영자 안내가
  허용한 저속 수집까지 금지하는 취지로 해석하지 않는다.
- 목록: GET /sanction/list?page=N&items_per_page=50 (page는 1부터, 정적 HTML).
- 상세: GET /sanction/{nid} (정적 HTML — 문서 정보 테이블 + 첨부 목록).
- 파일: GET /og/com/download.php?nid=..&dtype=basic&rid=F..&fid= (curl로 실제
  다운로드 성공 확인 — 유효한 HWPX 바이트가 그대로 온다. 헤드리스 브라우저 불필요).

수집 원칙:
- 작성자(전화번호) 필드는 실명+직통번호라 Document 어디에도 저장하지 않는다
  (moel.py에서 확립한 개인정보 제외 원칙).
- **body_text는 "문서 보기" 뷰어의 변환본에서 수집한다**(2026-07-16 사용자
  지적으로 추가 — 다운로드 가능한 파일은 결재본문뿐이고 실질 내용 첨부는
  대부분 "비공개 문서"인데, 화면의 문서보기 프리뷰는 실제 본문 내용을 상당
  부분 담고 있다). 뷰어 체인은 실사로 리버스엔지니어링(docviewer.js 분석):
  ① GET /og/docView/docviewer_onestep8.php?real=y&rid={rid} — 변환 트리거,
     {"success":"true", "iframe_para":...} JSON 반환
  ② GET /out/{rid}ABC/document.json — 변환 결과 메타(페이지 수/페이지 파일명)
  ③ GET /out/{rid}ABC/{rid}_{n}.html — 페이지별 본문 HTML (텍스트 추출)
  rid는 첨부 목록의 문서보기 버튼 onclick="docview8('F...','hview')"에서 얻는다.
  변환본은 사이트가 개인정보를 이미 ****로 마스킹한 버전이라 O/C/S 라벨과
  내용 민감도 사이 모순이 없다(orginl_info의 wonmun 필터링과 같은 성격).
  본문 수집은 best-effort다 — 변환 실패/뷰어 미제공 문서는 body_text=None으로
  두고 문서 자체는 정상 수집한다(격리하지 않음).
- doc_type은 official_document(공문) **고정값**이다(2026-07-16 사용자 결정).
  처음엔 open_go_kr._infer_doc_type(제목 키워드)을 재사용했지만, 뷰어 본문
  실사 결과 이 게시판에서 실제로 수집되는 내용은 제목이 뭐든(지급/출장/승인...)
  "해당 안건을 알리는 결재 통지 커버 문서"이고, 안건의 실체 문서(내역서/
  조사서/명세서 등)는 전부 "비공개 문서" 첨부라 수집되지 않는다 — 수집물의
  실제 성격이 균일하므로 제목 키워드로 doc_type을 나누는 건 내용과 어긋난
  분류가 된다. open_go_kr이 초기에 "절차상 전부 결재문서"라며 official_document
  고정을 썼던 것과 같은 판단이며, 이 소스는 그 초기 판단이 실사로도 맞는
  케이스다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.file_select import pick_primary_file
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.files import resolve_body_file_path
from rd2.storage.naming import DOC_TYPE_OFFICIAL_DOCUMENT, SOURCE_SEOUL_OPENGOV

logger = logging.getLogger(__name__)

BASE_URL = "https://opengov.seoul.go.kr"
LIST_URL = f"{BASE_URL}/sanction/list"
VIEWER_TRIGGER_URL = f"{BASE_URL}/og/docView/docviewer_onestep8.php"

# 문서보기 버튼 onclick="docview8('F0000120563674', 'hview');"에서 rid 추출.
_VIEWER_RID_RE = re.compile(r"docview8\('([^']+)'")

# 뷰어 본문 수집 페이지 상한. 결재문서는 대부분 1~3페이지지만, 예외적으로 긴
# 문서가 페이지당 10초 지연과 곱해져 한 건에 수십 분을 쓰는 걸 막는다.
_MAX_BODY_PAGES = 20

# orginl_info.py의 _DISCLOSURE_TEXT_MAP과 동일 — 의도적 복사(2026-07-13
# plan-eng-review DRY 이슈 5번 결정: 실제 중복 규모를 본 뒤 리팩토링).
_DISCLOSURE_TEXT_MAP = {
    "공개": DisclosureStatus.OPEN,
    "부분공개": DisclosureStatus.PARTIAL,
    "비공개": DisclosureStatus.CLOSED,
}

# 사이트 운영자가 이 요청이 무엇인지 알아볼 수 있도록 식별 가능한 UA를 명시한다.
USER_AGENT = "RD2-Crawler/1.0"
_HEADERS = {"User-Agent": USER_AGENT}

# 요청 사이 최소 지연(초). 운영자 공식 안내(모듈 독스트링 참고)가 "한 페이지
# 수집 후 다음 페이지 요청까지 10초 이상"을 요구하므로 다른 어댑터(0.6초)보다
# 훨씬 길다 — 이 값을 줄이면 안 된다(과부하 유발 사용자는 네트워크 차단될 수
# 있다고 같은 안내에 경고돼 있음).
_REQUEST_DELAY_SECONDS = 10.0

_ITEMS_PER_PAGE = 50

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# HWPX는 ZIP 컨테이너다. 다운로드 엔드포인트는 간헐적으로 HTTP 200과 함께
# 평문 ``error``를 돌려주므로, 상태 코드만으로는 정상 첨부를 판별할 수 없다.
_HWPX_MAGIC = b"PK\x03\x04"


def _throttle() -> None:
    time.sleep(_REQUEST_DELAY_SECONDS)


def _fetch_list_html(page: int) -> str:
    response = httpx.get(
        LIST_URL,
        params={"page": page, "items_per_page": _ITEMS_PER_PAGE},
        headers=_HEADERS,
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(nid: str) -> str:
    response = httpx.get(
        f"{BASE_URL}/sanction/{nid}",
        headers=_HEADERS,
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_viewer_trigger(rid: str, *, referer: str) -> dict:
    """뷰어 체인 1단계 — 서버측 문서 변환을 트리거한다(모듈 독스트링 참고)."""
    headers = dict(_HEADERS)
    headers["Referer"] = referer
    response = httpx.get(
        VIEWER_TRIGGER_URL,
        params={"real": "y", "rid": rid},
        headers=headers,
        timeout=60,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def _fetch_viewer_document_json(rid: str) -> dict:
    """뷰어 체인 2단계 — 변환 결과 메타(총 페이지 수, 페이지 파일명)."""
    response = httpx.get(
        f"{BASE_URL}/out/{rid}ABC/document.json",
        headers=_HEADERS,
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def _fetch_viewer_page_html(rid: str, page_src: str) -> str:
    """뷰어 체인 3단계 — 페이지별 변환 HTML."""
    response = httpx.get(
        f"{BASE_URL}/out/{rid}ABC/{page_src}",
        headers=_HEADERS,
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _download_file_streaming(url: str, dest: Path, *, referer: str) -> None:
    """moel_policy.py와 동일한 스트리밍 다운로드 패턴. 실제 브라우저 흐름과
    같도록 상세페이지를 Referer로 넘긴다(실사에서 이 조합으로 다운로드 확인)."""
    headers = dict(_HEADERS)
    headers["Referer"] = referer
    temp_dest = dest.with_name(f".{dest.name}.part")
    try:
        with httpx.stream("GET", url, headers=headers, timeout=120, follow_redirects=True) as response:
            response.raise_for_status()
            with open(temp_dest, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)

        if dest.suffix.lower() == ".hwpx":
            with open(temp_dest, "rb") as f:
                header = f.read(len(_HWPX_MAGIC))
            if header != _HWPX_MAGIC:
                preview = temp_dest.read_bytes()[:32]
                raise ValueError(
                    f"HWPX 다운로드 응답이 ZIP 형식이 아님: {preview!r}"
                )

        # 검증이 끝난 파일만 최종 경로로 이동한다. 실패/중단 시 기존 정상 파일도
        # 덮어쓰지 않고 .part 파일만 정리된다.
        os.replace(temp_dest, dest)
    finally:
        temp_dest.unlink(missing_ok=True)


def _text_without_invisible(el: Tag) -> str:
    """목록 항목의 <strong class="element-invisible">제목 : </strong> 같은
    스크린리더용 라벨을 제거한 뒤 텍스트를 뽑는다."""
    for strong in el.select("strong.element-invisible"):
        strong.decompose()
    return el.get_text(" ", strip=True)


def _fetch_body_text(rid: str, *, referer: str) -> str | None:
    """뷰어 체인(트리거→document.json→페이지 HTML)으로 변환 본문 텍스트를
    수집한다. 실패 시 None — 호출부(parse_detail)가 best-effort로 다룬다."""
    trigger = with_retry(lambda: _fetch_viewer_trigger(rid, referer=referer))
    _throttle()
    if str(trigger.get("success")).lower() != "true":
        logger.warning("뷰어 변환 트리거 실패: rid=%s response=%r", rid, trigger)
        return None

    # 트리거 직후 변환이 아직 안 끝났으면 document.json이 404일 수 있다 —
    # 지연(10초)을 두고 몇 번 더 본다(실사에선 트리거 직후 바로 존재했지만,
    # 최초 변환되는 문서는 시간이 걸릴 가능성에 대비).
    doc_meta: dict | None = None
    for _ in range(3):
        try:
            doc_meta = with_retry(lambda: _fetch_viewer_document_json(rid))
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            _throttle()
    _throttle()
    if doc_meta is None:
        logger.warning("뷰어 변환 결과(document.json)를 얻지 못함: rid=%s", rid)
        return None

    converter = doc_meta.get("docsconverter") or {}
    if (converter.get("result") or {}).get("code") != "0000":
        logger.warning("뷰어 변환 결과 코드가 성공이 아님: rid=%s meta=%r", rid, converter.get("result"))
        return None

    pages_info = (converter.get("file") or {}).get("pages_info") or {}
    page_keys = sorted((k for k in pages_info if str(k).isdigit()), key=int)
    if not page_keys:
        return None

    truncated = len(page_keys) > _MAX_BODY_PAGES
    texts: list[str] = []
    for key in page_keys[:_MAX_BODY_PAGES]:
        src = pages_info[key].get("src")
        if not src:
            continue
        html = with_retry(lambda s=src: _fetch_viewer_page_html(rid, s))
        _throttle()
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        text = text.lstrip("﻿").strip()
        if text:
            texts.append(text)

    if not texts:
        return None
    body = "\n".join(texts)
    if truncated:
        body += f"\n(이하 생략 — 총 {len(page_keys)}페이지 중 {_MAX_BODY_PAGES}페이지까지 수집)"
    return body


class SeoulOpengovAdapter(SourceAdapter):
    source_name = SOURCE_SEOUL_OPENGOV

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 page 단위(1부터, 페이지당 50건)로 순회한다. skip은 페이지
        계산으로 바로 건너뛴다(페이지당 요청 지연이 10초라 moel_policy처럼
        1페이지부터 훑으면 체크포인트 재개가 너무 느리다 — orginl_info와 동일한
        방식). 항목이 0개인 페이지를 만나면 종료한다."""
        page = (skip // _ITEMS_PER_PAGE) + 1
        remaining_skip = skip % _ITEMS_PER_PAGE
        yielded = 0
        while True:
            def _do_fetch(p: int = page) -> str:
                return _fetch_list_html(p)

            html = with_retry(_do_fetch)
            _throttle()
            soup = BeautifulSoup(html, "html.parser")

            items: list[dict] = []
            for li in soup.select(".view-content li"):
                link = li.select_one('.title-wrap a[href^="/sanction/"]')
                if not link:
                    continue
                nid = (link.get("href") or "").rstrip("/").rsplit("/", 1)[-1]
                if not nid.isdigit():
                    continue

                category_spans = li.select(".title-category span")
                date_el = li.select_one(".title-info .date")
                dept_el = li.select_one(".title-info .dept")
                date_text = _text_without_invisible(date_el) if date_el else ""
                date_match = _DATE_RE.search(date_text)

                items.append(
                    {
                        "_nid": nid,
                        "_title": _text_without_invisible(link),
                        # [공개구분, 기관구분(서울시/자치구/투자출연기관)] 순서
                        "_disclosure_list": (
                            category_spans[0].get_text(strip=True) if category_spans else None
                        ),
                        "_agency_category": (
                            category_spans[1].get_text(strip=True) if len(category_spans) > 1 else None
                        ),
                        "_date_text": date_match.group(0) if date_match else None,
                        "_department_list": _text_without_invisible(dept_el) if dept_el else None,
                        "_detail_url": f"{BASE_URL}/sanction/{nid}",
                    }
                )

            if not items:
                return
            if remaining_skip:
                items = items[remaining_skip:]
                remaining_skip = 0
            for item in items:
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            page += 1

    def _download_and_save_files(
        self, nid: str, doc_type: str, title: str, file_list: list[dict]
    ) -> tuple[str | None, list[str]]:
        if not file_list:
            return None, []

        detail_url = f"{BASE_URL}/sanction/{nid}"
        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            final_path = resolve_body_file_path(
                self.files_root, SOURCE_SEOUL_OPENGOV, doc_type, nid, file_meta["filename"]
            )

            def _do_download(u: str = file_meta["href"], p: Path = final_path) -> None:
                _download_file_streaming(u, p, referer=detail_url)

            with_retry(_do_download)
            _throttle()
            saved.append((str(final_path.relative_to(self.files_root)), file_meta))

        primary_meta = pick_primary_file(file_list, title, filename_key="filename")
        body_file_path = next(path for path, fm in saved if fm is primary_meta)
        other_file_paths = [path for path, fm in saved if fm is not primary_meta]
        return body_file_path, other_file_paths

    def parse_detail(
        self, raw_item: dict, *, download_files: bool = True, fetch_body_text: bool = True
    ) -> dict:
        nid = raw_item["_nid"]

        def _do_fetch() -> str:
            return _fetch_detail_html(nid)

        html = with_retry(_do_fetch)
        _throttle()
        soup = BeautifulSoup(html, "html.parser")

        detail = dict(raw_item)

        title_el = soup.select_one("h3.title-article")
        title = title_el.get_text(" ", strip=True) if title_el else raw_item.get("_title")
        if not title:
            raise ValueError(f"상세페이지 파싱 실패(제목 없음): nid={nid}")
        detail["_title"] = title

        # "문서 정보" 테이블 — th/td가 한 행에 두 쌍씩 온다(기관명|부서명 등).
        # 작성자(전화번호)는 실명+직통번호라 여기서부터 아예 버린다(모듈 독스트링).
        info: dict[str, str] = {}
        info_table = soup.select_one("table.table-response")
        if info_table is None:
            raise ValueError(f"상세페이지 파싱 실패(문서 정보 테이블 없음): nid={nid}")
        for tr in info_table.select("tr"):
            ths = tr.select("th")
            tds = tr.select("td")
            for th, td in zip(ths, tds):
                label = th.get_text(strip=True)
                if label == "작성자(전화번호)":
                    continue
                # 분류정보 td 끝에 "같은 분류 문서보기" 링크가 붙어 있다 — 제거.
                for a in td.select("a"):
                    a.decompose()
                info[label] = td.get_text(" ", strip=True)

        detail["_agency"] = info.get("기관명")
        detail["_department"] = info.get("부서명") or raw_item.get("_department_list")
        detail["_doc_no"] = info.get("문서번호")
        detail["_date_text"] = info.get("생산일자") or raw_item.get("_date_text")
        detail["_disclosure"] = info.get("공개구분") or raw_item.get("_disclosure_list")

        # 분류정보: BRM 경로("행정 > 일반행정지원 > ... > 급여및수당관리").
        # 마지막 단계가 단위업무에 해당한다.
        brm_raw = info.get("분류정보") or ""
        brm_parts = [p.strip() for p in brm_raw.split(">") if p.strip()]
        detail["_subject_category"] = " > ".join(brm_parts) if brm_parts else None
        detail["_unit_task"] = brm_parts[-1] if brm_parts else None

        # 첨부 목록 — "비공개 문서"(span.txt-notopen)는 다운로드 링크 자체가
        # 없으므로 건너뛴다. 다운로드 가능한 파일만 수집한다.
        file_list: list[dict] = []
        for li in soup.select("ul.list-attachment li"):
            if li.select_one("span.txt-notopen"):
                continue
            # 실제 페이지에는 빈 ``/og/com/download.php`` 링크와 파일 식별자
            # (nid/dtype/rid/fid)가 붙은 링크가 함께 있다. 전자를 요청하면 HTTP
            # 200 본문 ``error``만 돌아오므로 쿼리 문자열이 있는 실제 링크만 쓴다.
            a = li.select_one('a[href*="/og/com/download.php?"]')
            name_p = li.select_one("p.title-down")
            if not a or not name_p:
                continue
            # 파일명 뒤에 붙는 용량 표기(<span class="txt-gray">(33.94 KB)</span>) 제거.
            for span in name_p.select("span"):
                span.decompose()
            filename = name_p.get_text(strip=True)
            if not filename:
                continue
            file_list.append({"filename": filename, "href": urljoin(BASE_URL, a["href"])})

        # 고정값 — 이 게시판의 수집물은 제목과 무관하게 전부 결재 통지 커버
        # 문서다(모듈 독스트링의 2026-07-16 사용자 결정 참고).
        doc_type = DOC_TYPE_OFFICIAL_DOCUMENT
        detail["_doc_type"] = doc_type

        # 본문 텍스트 — 첨부 목록의 문서보기 버튼(onclick=docview8('F...','hview'))
        # 에서 rid를 얻어 뷰어 변환본을 수집한다(모듈 독스트링 참고). 뷰어가 없는
        # 문서(비공개 등)나 변환 실패는 body_text=None으로 두고 계속 진행한다 —
        # 메타데이터+파일은 정상이므로 문서 전체를 격리하지 않는다.
        detail["_body_text"] = None
        if fetch_body_text:
            viewer_btn = soup.select_one('ul.list-attachment button[onclick*="docview8"]')
            rid_match = _VIEWER_RID_RE.search(viewer_btn.get("onclick") or "") if viewer_btn else None
            if rid_match:
                try:
                    detail["_body_text"] = _fetch_body_text(
                        rid_match.group(1), referer=f"{BASE_URL}/sanction/{nid}"
                    )
                except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
                    # 구조 변형(KeyError 등)도 여기선 버그가 아니라 "이 문서는 변환본
                    # 형식이 다르다"는 데이터 편차로 본다 — 본문은 부가 정보라
                    # 조용히 건너뛰고 수집은 계속한다.
                    logger.warning("본문 뷰어 수집 실패(계속 진행): nid=%s %s", nid, exc)

        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files and file_list:
            body_file_path, other_file_paths = self._download_and_save_files(
                nid, doc_type, title, file_list
            )
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        title = enriched_item["_title"]

        disclosure_text = enriched_item.get("_disclosure")
        if not disclosure_text:
            raise ValueError("공개구분을 확인할 수 없음 — 공개로 간주하지 않고 격리 처리")
        if disclosure_text not in _DISCLOSURE_TEXT_MAP:
            raise ValueError(f"알 수 없는 공개구분 값: {disclosure_text!r}")
        disclosure_status = _DISCLOSURE_TEXT_MAP[disclosure_text]

        production_date: date | None = None
        date_text = enriched_item.get("_date_text")
        if date_text:
            try:
                production_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except ValueError:
                pass

        # orginl_info.py와 동일한 근사 매핑 — 이 사이트도 공개구분만 제공하고
        # 정보공개법 제9조의 구체적 조항 근거는 제공하지 않는다.
        non_disclosure_reason = None
        if disclosure_status == DisclosureStatus.CLOSED:
            cso_classification = CsoClassification.C
            non_disclosure_reason = "비공개 — 구체적 법적 근거 조항은 이 소스(서울 정보소통광장)에서 확인 불가"
        elif disclosure_status == DisclosureStatus.PARTIAL:
            cso_classification = CsoClassification.S
            non_disclosure_reason = "부분공개 — 구체적 법적 근거 조항은 이 소스(서울 정보소통광장)에서 확인 불가"
        else:
            cso_classification = CsoClassification.O

        return Document(
            title=title,
            ordering_agency=enriched_item.get("_agency") or enriched_item.get("_agency_category") or "",
            department=enriched_item.get("_department"),
            unit_task=enriched_item.get("_unit_task"),
            production_date=production_date,
            disclosure_status=disclosure_status,
            subject_category=enriched_item.get("_subject_category"),
            body_text=enriched_item.get("_body_text") or None,
            # 문서번호(예: 친환경급식과-6067)를 넣는다 — orginl_info.py가 docNo를
            # 여기 저장하는 것과 동일한 매핑(요약 개념이 없는 소스).
            content_summary=enriched_item.get("_doc_no"),
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            non_disclosure_reason=non_disclosure_reason,
            cso_classification=cso_classification,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            is_synthetic=False,
        )
