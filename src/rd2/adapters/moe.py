"""교육부 재정·예산 정보 게시판(moe.go.kr, boardID=344) 어댑터.

RD-2 O트랙 신규 출처. 실제 사이트 조사(httpx/curl 직접 요청, 2026-07-13) 결과:
- robots.txt(`www.moe.go.kr/robots.txt`)는 `/search`, `/search/`만 금지하고
  `/boardCnts/` 경로는 허용 — 코드 작성 전 실사로 확인 완료.
- 목록/상세/파일다운로드 전부 쿠키 없이 단발 GET으로 200이 온다 — molit의 TMOS 쿠키
  챌린지 같은 게 없어 순수 httpx로 충분하다(browse_client 불필요).
- 목록 페이지 안에는 테이블이 두 개 있다 — 상단의 "소속기관별" 예결산 안내 테이블(무관한
  다른 데이터)과 실제 게시판 목록 테이블. 반드시 `div[data-table][data-type="list"] table`로
  범위를 좁혀야 한다. molit처럼 `table tr` 전체를 스캔하면 엉뚱한 테이블의 행을 주워온다.
- 상세 링크가 `href`가 아니라
  `onclick="javascript:goView('344','<boardSeq>','0',null,'<statusYN>','<page>','N','');"`
  형태다. `boardSeq`(식별자)뿐 아니라 `statusYN`(값이 'W' 또는 'C'로 섞여 있음 — 실측
  113건 중 77건 W, 36건 C)도 이 onclick에서 그대로 파싱해야 한다 — molit의 "번호(표시용)
  vs id(href의 실제 식별자)" 함정과 같은 성격으로, statusYN을 'W'로 하드코딩하면 안 된다.
- 상세 URL의 `page` 파라미터는 응답 내용에 영향이 없다(임의로 99를 넣어도 동일 응답
  확인) — 항상 1로 고정한다. 반면 statusYN은 목록에서 파싱한 값을 그대로 넘겨야 한다.
- 목록 종료 조건은 molit과 동일: 다음 페이지에 행이 0개면 멈춘다(실사 시점 13페이지
  요청 시 0건 확인).
- **실측 중 발견한 파싱 함정**: 목록 행의 `<tr>`가 `</tr>`로 닫히지 않은 채 바로 다음
  `<tr>`가 이어진다(원문 HTML 자체의 결함, 실사로 확인). `html.parser`는 이걸 형제가
  아니라 중첩(nested) 구조로 복구해버려서, `soup.select("tbody tr")`로 행을 순회하면
  같은 게시물이 중첩 깊이만큼 중복으로 잡힌다(실제로 collect_moe.py 첫 실행에서
  같은 문서가 연속으로 "stored" 다음 "dup-skip"으로 찍히는 걸로 발견함). 그래서 이
  어댑터는 `<tr>`를 순회하지 않고, 문서당 정확히 하나만 존재하는 `goView` 앵커를
  `list_table.select('a[onclick*="goView"]')`로 직접 선택한 뒤 `find_parent("td")`/
  `find_next_siblings("td")`로 형제 셀을 찾는다 — 이 방식은 바깥 `<tr>` 중첩 구조가
  어떻든 영향받지 않는다.
- 상세 페이지의 담당부서 셀에는 전화번호가 같이 들어있다
  (`교원양성연수과<br><small>(044-203-6496)</small>`) — molit에서 담당자 실명/전화번호를
  수집 대상에서 뺀 것과 같은 원칙으로, 이 어댑터도 셀의 첫 텍스트 노드(부서명)만 취하고
  전화번호는 Document 어디에도 남기지 않는다.
- 존재하지 않는/삭제된 boardSeq로 접근하면 완전히 다른 정적 에러 페이지("요청하신
  페이지를 찾을수 없습니다")가 200으로 온다 — molit의 "페이지 이동중" 처리와 동일하게
  예외를 던져 호출부(collect_moe.py)가 quarantine 테이블로 격리하도록 한다.
- 첨부파일 파일명은 `<a>` 안이 아니라 `<li>`의 첫 텍스트 노드에 있다(molit/mohw와 다른
  구조): `"파일명.pdf\n\t...[ 1.2 MB ]\n..."` 형태라 첫 줄만 잘라 써야 한다.
- Content-Type이 항상 `application/octet-stream`이라 신뢰할 수 없다 — molit과 동일하게
  파일명만 신뢰한다. 다만 오래된 게시물(2008년, boardSeq=12452)에서 표시 파일명 자체가
  `/2008/12/01/2008.zip`처럼 경로 형태로 깨진 사례를 실사로 확인했다 — `Path(...).name`
  으로 정규화해 `2008.zip`을 취한다.
- 실측 최대 파일 크기는 6.7MB로 molit(62.8MB)보다 작지만, 이미 검증된 더 안전한 패턴이라
  mohw식 전체 메모리 로드 대신 molit식 스트리밍 다운로드를 그대로 채택한다.
- 게시판 성격상 전부 교육부가 배포하는 사전정보공표(재정·예산 정보)라 비공개·부분공개
  개념이 없다 — disclosure_status/cso_classification은 항상 OPEN/O.
- doc_type은 단일 유형(budget_material)으로 고정한다. 113건 전체 제목을 실사해 분리
  가능성을 검토했으나(2026-07-13), "결산"과 "예산"이 한 제목에 같이 들어간 문서가 많아
  (예: "기금결산보고서 및 사업운영계획·예산") 키워드 우선순위 분리가 오분류를 유발할
  소지가 크다고 판단해 단일 유형으로 유지한다(mohw의 입찰/사전규격/공모처럼 실제로
  다른 행정 절차가 아니라 전부 균질한 "재정·예산 정보 공개" 성격이라는 점도 근거).
- 이 게시판엔 molit의 "분류(subject_category)" 같은 필드가 없다 — mohw와 같은 성격.
- mohw는 커스텀 User-Agent도 요청 간 delay도 없이 요청했다(2026-07-09 리뷰에서
  지적됨) — 이 어댑터는 molit과 동일하게 식별 가능한 UA와 요청 사이 최소 지연을 둔다.
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
from rd2.storage.naming import DOC_TYPE_BUDGET_MATERIAL, SOURCE_MOE

BASE_URL = "https://www.moe.go.kr"
LIST_URL = f"{BASE_URL}/boardCnts/listRenew.do"
DETAIL_URL = f"{BASE_URL}/boardCnts/viewRenew.do"

BOARD_ID = "344"
MENU_ID = "041203"

# 사이트 운영자가 이 요청이 무엇인지 알아볼 수 있도록 식별 가능한 UA를 명시한다
# (molit과 동일한 결정 — 연락처는 넣지 않음).
USER_AGENT = "RD2-Crawler/1.0"
_HEADERS = {"User-Agent": USER_AGENT}

# 요청 사이 최소 지연(초). mohw는 이게 없어 서버 로그에 연타로 남는다는 지적을 받았다.
_REQUEST_DELAY_SECONDS = 0.6

# 목록 행의 onclick="javascript:goView('344','<boardSeq>','0',null,'<statusYN>',...);"
# 에서 boardSeq(식별자)와 statusYN(W/C — 목록 파싱 시 그대로 보존해야 하는 값)을 추출한다.
_GOVIEW_RE = re.compile(
    r"goView\('" + re.escape(BOARD_ID) + r"',\s*'(?P<seq>\d+)',\s*'0',\s*null,\s*'(?P<status>\w)'"
)


def _throttle() -> None:
    time.sleep(_REQUEST_DELAY_SECONDS)


def _fetch_list_html(page: int) -> str:
    response = httpx.get(
        LIST_URL,
        params={"type": "default", "page": page, "m": MENU_ID, "renew": BOARD_ID, "s": "moe", "boardID": BOARD_ID},
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _fetch_detail_html(board_seq: str, status_yn: str) -> str:
    # page 파라미터는 응답 내용에 영향이 없어(실사로 확인) 1로 고정한다.
    response = httpx.get(
        DETAIL_URL,
        params={
            "boardID": BOARD_ID,
            "boardSeq": board_seq,
            "lev": "0",
            "searchType": "null",
            "statusYN": status_yn,
            "page": 1,
            "s": "moe",
            "m": MENU_ID,
            "opType": "N",
        },
        headers=_HEADERS,
        timeout=15,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _download_file_streaming(url: str, dest: Path) -> None:
    """molit과 동일한 스트리밍 다운로드 패턴 — 전체를 메모리에 올리지 않고 청크
    단위로 디스크에 직접 쓴다(mohw의 전체 로드 방식보다 안전한, 이미 검증된 패턴)."""
    with httpx.stream(
        "GET", url, headers=_HEADERS, timeout=120, follow_redirects=True
    ) as response:
        response.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


class MoeAdapter(SourceAdapter):
    source_name = SOURCE_MOE

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 페이지 단위(page)로 순회한다. 페이지 안에 무관한 테이블이 하나 더
        있어(상단 "소속기관별" 안내 테이블) 반드시 게시판 목록 테이블로 범위를 좁힌다."""
        yielded = 0
        seen = 0
        page = 1
        while True:
            def _do_fetch(p: int = page) -> str:
                return _fetch_list_html(p)

            html = with_retry(_do_fetch)
            _throttle()
            soup = BeautifulSoup(html, "html.parser")
            list_table = soup.select_one('div[data-table][data-type="list"] table')
            # 실제 목록 HTML은 각 행의 <tr>를 닫지 않는다(</tr> 없이 바로 다음 <tr>가
            # 옴, 실사 2026-07-13 원문 그대로) — html.parser가 이걸 형제가 아니라
            # 중첩(nested) 구조로 복구해버려 `tbody tr`로 셀렉트하면 같은 행이 여러
            # 번(중첩 깊이만큼) 중복으로 잡힌다. 대신 goView 앵커(문서당 정확히 하나만
            # 존재하는 실제 DOM 노드)를 직접 선택하고, 거기서 `find_parent`/
            # `find_next_siblings`로 형제 td를 찾으면 바깥 tr 중첩 구조와 무관하게
            # 안전하다.
            anchors = list_table.select('a[onclick*="goView"]') if list_table else []

            found_row = False
            for link in anchors:
                onclick = link.get("onclick") or ""
                match = _GOVIEW_RE.search(onclick)
                if not match:
                    continue

                found_row = True
                seen += 1
                if seen <= skip:
                    continue

                title_td = link.find_parent("td")
                sibling_tds = title_td.find_next_siblings("td") if title_td else []
                board_seq = match.group("seq")
                yield {
                    "_board_seq": board_seq,
                    "_status_yn": match.group("status"),
                    "_title": link.get("title") or link.get_text(strip=True),
                    "_detail_url": (
                        f"{DETAIL_URL}?boardID={BOARD_ID}&boardSeq={board_seq}&lev=0"
                        f"&searchType=null&statusYN={match.group('status')}&page=1"
                        f"&s=moe&m={MENU_ID}&opType=N"
                    ),
                    "_department_list": sibling_tds[0].get_text(strip=True) if len(sibling_tds) > 0 else None,
                    "_list_date": sibling_tds[1].get_text(strip=True) if len(sibling_tds) > 1 else None,
                }
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            if not found_row:
                return
            page += 1

    def _download_and_save_files(
        self, board_seq: str, title: str, file_list: list[dict]
    ) -> tuple[str | None, list[str]]:
        if not file_list:
            return None, []

        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            final_path = resolve_body_file_path(
                self.files_root,
                SOURCE_MOE,
                DOC_TYPE_BUDGET_MATERIAL,
                board_seq,
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
        board_seq = raw_item["_board_seq"]
        status_yn = raw_item["_status_yn"]

        def _do_fetch() -> str:
            return _fetch_detail_html(board_seq, status_yn)

        html = with_retry(_do_fetch)
        _throttle()
        soup = BeautifulSoup(html, "html.parser")
        view = soup.select_one('div[data-table][data-type="view"]')

        detail = dict(raw_item)
        if view is None:
            # 실사로 확인된 edge case: 삭제/만료된 boardSeq로 접근하면 "요청하신
            # 페이지를 찾을수 없습니다" 안내 페이지가 200으로 온다(실제 응답 캡처,
            # 2026-07-13). 조용히 빈 문서로 만들지 않고 예외를 던져 호출부
            # (collect_moe.py)가 quarantine 테이블로 격리하도록 한다.
            raise ValueError(f"상세페이지 파싱 실패(view 컨테이너 없음): boardSeq={board_seq}")

        def _info(label: str) -> str | None:
            th = view.find("th", string=label)
            if not th:
                return None
            td = th.find_next_sibling("td")
            if not td:
                return None
            # 담당부서 셀에는 전화번호가 <br><small>(...)</small>로 같이 들어있다 —
            # 첫 텍스트 노드(부서명)만 취해 전화번호가 어디에도 남지 않게 한다.
            first_text = td.contents[0] if td.contents else None
            return first_text.strip() if isinstance(first_text, str) else td.get_text(strip=True)

        title_th = view.find("th", string="제목")
        title_td = title_th.find_next_sibling("td") if title_th else None
        detail["_title"] = title_td.get_text(strip=True) if title_td else raw_item.get("_title")
        detail["_department"] = _info("담당부서")
        detail["_production_date_text"] = _info("등록일")

        content = soup.select_one("div[data-content].boardRenewArea")
        detail["_body_text"] = content.get_text(" ", strip=True) if content else None

        file_list: list[dict] = []
        file_th = view.find("th", string="첨부파일")
        file_td = file_th.find_next_sibling("td") if file_th else None
        if file_td:
            for li in file_td.select("li"):
                a = li.select_one('a[href*="fileDown.do"]')
                if not a:
                    continue
                raw_text = li.contents[0] if li.contents else None
                if not isinstance(raw_text, str):
                    continue
                # 파일명은 li의 첫 텍스트 노드에 있고("파일명.pdf\n\t...[ 크기 ]\n..."),
                # 첫 줄만 취한다. Path(...).name으로 정규화해 오래된 게시물의 경로형
                # 파일명 결함(예: "/2008/12/01/2008.zip")도 함께 처리한다.
                first_line = raw_text.strip().splitlines()[0].strip() if raw_text.strip() else ""
                filename = Path(first_line).name if first_line else None
                if not filename:
                    continue
                file_list.append({"filename": filename, "href": urljoin(BASE_URL, a["href"])})

        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files and file_list:
            body_file_path, other_file_paths = self._download_and_save_files(
                board_seq, detail["_title"] or "", file_list
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
            ordering_agency="교육부",
            department=enriched_item.get("_department"),
            production_date=production_date,
            disclosure_status=DisclosureStatus.OPEN,
            body_text=enriched_item.get("_body_text") or None,
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            cso_classification=CsoClassification.O,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=DOC_TYPE_BUDGET_MATERIAL,
            is_synthetic=False,
        )
