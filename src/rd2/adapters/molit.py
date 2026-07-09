"""국토교통부 정책정보 게시판(molit.go.kr, m_34681) 어댑터.

RD-2 O트랙 신규 출처. 실제 사이트 조사(httpx 직접 요청, 2026-07-09) 결과:
- 첫 요청이 항상 `307 + Set-Cookie: TMOSHCooKie=...`로 자기 자신에게 리다이렉트되는
  TMOS/F5 쿠키 챌린지가 있다. 쿠키 엔진 없이 요청하면(예: 헤더만 보는 요청) 무한
  리다이렉트에 빠지지만, `httpx.get(url, follow_redirects=True)`처럼 리다이렉트를
  따라가며 쿠키를 유지하는 클라이언트는 단일 호출 안에서 자동으로 통과한다 —
  헤드리스 브라우저(browse_client) 없이도 mohw와 동일하게 순수 httpx로 충분하다.
- robots.txt(`www.molit.go.kr/robots.txt`)는 기본 `Allow: /`이고, 이 어댑터가 쓰는
  `/USR/policyData/m_34681/` 경로는 개별 disallow 목록에 없음 — 코드 작성 전에
  실사로 확인 완료.
- 목록 페이지의 "번호"(표시 순번) 컬럼과 상세링크의 `id` 쿼리파라미터는 서로 다른
  값 공간이다(번호는 게시판 내 순번, id는 CMS 전역 콘텐츠ID) — 상세 URL은 반드시
  `<a href>`에서 파싱한 `id` 값을 써야 하고, 번호로 유추하면 안 된다(실사 중 실제로
  이 착오로 엉뚱한 게시물을 받은 적 있음).
- 게시판 성격상 전부 국토교통부가 배포하는 공개 정책자료라 비공개·부분공개
  개념이 없다 — disclosure_status/cso_classification은 항상 OPEN/O.
- doc_type은 기본적으로 "정책정보" 단일 유형이지만, 제목에 "회의록"이 있으면
  별도 doc_type(meeting_minutes)으로 분리한다 — 저장 폴더가 source/doc_type
  기준이라(storage/files.py) 이 값만 바꾸면 회의록이 자동으로 별도 디렉토리에
  쌓인다(2026-07-09 사용자 요청).
- 첨부파일 다운로드 응답 헤더의 Content-Type이 실제로는 PDF인데도
  `application/x-msdownload`로 오는 경우를 확인했다 — Content-Type을 신뢰하지
  말고 요청 시 넘긴 FileName(확장자 포함)만 신뢰한다.
- 샘플 중 단일 첨부파일이 62.8MB인 경우가 있었다 — mohw의 `_download_file_bytes`
  패턴(짧은 타임아웃 + 전체를 메모리에 로드)을 그대로 쓰면 위험해, 이 어댑터는
  스트리밍으로 받아 디스크에 직접 쓰고 타임아웃도 넉넉히 잡는다.
- mohw는 커스텀 User-Agent도 요청 간 delay도 없이 요청했다(2026-07-09 리뷰에서
  지적됨) — 이 어댑터는 식별 가능한 UA와 요청 사이 최소 지연을 둔다.
"""

from __future__ import annotations

import time
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
from rd2.storage.files import resolve_body_file_path
from rd2.storage.naming import DOC_TYPE_MEETING_MINUTES, DOC_TYPE_POLICY_MATERIAL, SOURCE_MOLIT

BASE_URL = "https://www.molit.go.kr"
LIST_URL = f"{BASE_URL}/USR/policyData/m_34681/lst.jsp"
DETAIL_URL = f"{BASE_URL}/USR/policyData/m_34681/dtl.jsp"

# 사이트 운영자가 이 요청이 무엇인지 알아볼 수 있도록 식별 가능한 UA를 명시한다
# (연락처는 넣지 않기로 결정 — 2026-07-09).
USER_AGENT = "RD2-Crawler/1.0"
_HEADERS = {"User-Agent": USER_AGENT}

# 요청 사이 최소 지연(초). mohw는 이게 없어 서버 로그에 연타로 남는다는 지적을 받았다.
_REQUEST_DELAY_SECONDS = 0.6


def _throttle() -> None:
    time.sleep(_REQUEST_DELAY_SECONDS)


def _infer_doc_type(title: str) -> str:
    """제목에 "회의록"이 있으면 별도 doc_type으로 분리한다(저장 폴더도 자동으로
    나뉜다, storage/files.py의 source/doc_type 기준 저장 규칙 참고). 그 외에는
    이 게시판의 기본 유형인 정책정보로 취급한다."""
    if "회의록" in title:
        return DOC_TYPE_MEETING_MINUTES
    return DOC_TYPE_POLICY_MATERIAL


def _fetch_list_html(page: int) -> str:
    """목록 페이지 요청. TMOS 쿠키 챌린지는 follow_redirects=True인 단일 호출
    안에서 자동으로 해결된다(모듈 독스트링 참고)."""
    response = httpx.get(
        LIST_URL,
        params={"lcmspage": page},
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(item_id: str) -> str:
    response = httpx.get(
        DETAIL_URL,
        params={"id": item_id},
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _download_file_streaming(url: str, dest: Path) -> None:
    """대용량 첨부파일(실측 최대 62.8MB) 대비 스트리밍 다운로드 — 전체를
    메모리에 올리지 않고 청크 단위로 디스크에 직접 쓴다. 타임아웃도 mohw의
    30초보다 넉넉한 120초로 잡는다."""
    with httpx.stream(
        "GET", url, headers=_HEADERS, timeout=120, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


class MolitAdapter(SourceAdapter):
    source_name = SOURCE_MOLIT

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 페이지 단위(lcmspage)로 순회한다."""
        yielded = 0
        seen = 0
        page = 1
        while True:
            def _do_fetch(p: int = page) -> str:
                return _fetch_list_html(p)

            html = with_retry(_do_fetch)
            _throttle()
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table tr")

            found_row = False
            for row in rows:
                tds = row.find_all("td")
                if len(tds) < 4:
                    continue
                title_cell = tds[1]
                link = title_cell.find("a")
                if not link or not link.get("href"):
                    continue

                found_row = True
                seen += 1
                if seen <= skip:
                    continue

                href = link["href"]
                # 상세 URL의 id는 반드시 href에서 파싱한다 — 표의 "번호"(tds[0])
                # 컬럼과는 다른 값 공간이므로 절대 번호로 유추하지 않는다
                # (실사 중 실제로 이 착오로 엉뚱한 게시물을 받은 적 있음).
                item_id = parse_qs(urlparse(href).query).get("id", [None])[0]
                if not item_id:
                    continue

                yield {
                    "_item_id": item_id,
                    "_title": link.get_text(strip=True),
                    "_detail_url": urljoin(BASE_URL, f"dtl.jsp?id={item_id}"),
                    "_category": tds[2].get_text(strip=True),
                    "_list_date": tds[3].get_text(strip=True),
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            if not found_row:
                return
            page += 1

    def _download_and_save_files(
        self, item_id: str, title: str, doc_type: str, file_list: list[dict]
    ) -> tuple[str | None, list[str]]:
        if not file_list:
            return None, []

        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            # save_body_file()은 바이트를 인자로 받아 메모리에 전체를 올리는 걸
            # 전제하는데(대용량 파일엔 위험, 모듈 독스트링 참고), 경로 결정 규칙만
            # resolve_body_file_path()로 공유하고 실제 쓰기는 스트리밍으로 한다.
            final_path = resolve_body_file_path(
                self.files_root,
                SOURCE_MOLIT,
                doc_type,
                item_id,
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
        item_id = raw_item["_item_id"]

        def _do_fetch() -> str:
            return _fetch_detail_html(item_id)

        html = with_retry(_do_fetch)
        _throttle()
        soup = BeautifulSoup(html, "html.parser")
        view = soup.select_one("div.bd_view")

        detail = dict(raw_item)
        if view is None:
            # 실사로 확인된 edge case: 잘못된/만료된 id로 접근하면 "페이지 이동중"
            # 안내 스크립트만 있는 짧은 페이지가 온다(실제 응답 캡처, 2026-07-09).
            # 조용히 빈 문서로 만들지 않고 예외를 던져 호출부(collect_molit.py)가
            # quarantine 테이블로 격리하도록 한다 — 다른 어댑터들과 동일한 원칙
            # ("스키마 검증 실패 레코드는 드롭하지 않고 격리 저장").
            raise ValueError(f"상세페이지 파싱 실패(div.bd_view 없음): id={item_id}")

        h4 = view.find("h4")
        detail["_title"] = h4.get_text(strip=True) if h4 else raw_item.get("_title")

        def _info(label: str) -> str | None:
            strong = view.find("strong", string=label)
            if not strong:
                return None
            span = strong.find_next_sibling("span")
            return span.get_text(strip=True) if span else None

        # 담당자(공무원 실명)/전화번호는 의도적으로 파싱하지 않는다 — Document
        # 스키마에 대응 필드가 없고, 2026-07-09 결정으로 수집 대상에서 제외했다.
        detail["_department"] = _info("담당부서")
        detail["_production_date_text"] = _info("등록일")
        detail["_category"] = _info("분류") or raw_item.get("_category")

        cont = view.select_one(".bd_view_cont")
        detail["_body_text"] = cont.get_text(" ", strip=True) if cont else None

        file_list: list[dict] = []
        file_li = view.select_one("li.file")
        if file_li:
            for a in file_li.find_all("a", href=True):
                href = a["href"]
                if "DownloadMltm2.jsp" not in href:
                    continue
                query = parse_qs(urlparse(href).query)
                filename = query.get("FileName", [None])[0]
                # 실사로 확인된 source-side 결함: 일부 게시물(예: id=4899)은 이
                # 앵커를 href='...'(홑따옴표)로 감싸는데, 파일명 자체에 홑따옴표가
                # 들어있으면(예: "('26.07.01) ...pdf") 어떤 표준 파서(브라우저 포함)로
                # 봐도 그 지점에서 속성값이 끊긴다 — 우리 파싱 버그가 아니라 사이트
                # HTML 자체가 깨져 있는 것. FileName이 확장자 없이 잘려있으면
                # FilePath의 서버 생성 파일명(항상 확장자 포함)으로 대체한다.
                if not filename or "." not in filename:
                    file_path = query.get("FilePath", [None])[0]
                    filename = Path(file_path).name if file_path else filename
                if not filename:
                    continue
                file_list.append({"filename": filename, "href": urljoin(BASE_URL, href)})

        detail["_doc_type"] = _infer_doc_type(detail["_title"] or "")

        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files and file_list:
            body_file_path, other_file_paths = self._download_and_save_files(
                item_id, detail["_title"] or "", detail["_doc_type"], file_list
            )
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        title = enriched_item["_title"]

        production_date: date | None = None
        date_text = enriched_item.get("_production_date_text")
        if date_text:
            try:
                production_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except ValueError:
                pass

        return Document(
            title=title,
            ordering_agency="국토교통부",
            department=enriched_item.get("_department"),
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            subject_category=enriched_item.get("_category"),
            body_text=enriched_item.get("_body_text") or None,
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            cso_classification=CsoClassification.O,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=enriched_item.get("_doc_type") or _infer_doc_type(title),
            is_synthetic=False,
        )
