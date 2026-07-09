"""정책연구관리시스템(PRISM, prism.go.kr) 어댑터.

RD-2 v1.1 최상위 4개 출처 중 하나, 우선순위 2위(`안순현-수집계획수립-20260706.md`) —
비공개/부분공개 상태와 실제 법적 근거 조항(공개제한근거)이 상세페이지에 명시되어
있어, cso_classification을 근사치가 아니라 실제 조항 기반으로 정확히 매길 수 있는
유일한 소스다(정보공개포털은 이 근거를 제공하지 않아 disclosure_status 심각도로
근사 매핑했다 — open_go_kr.py 참고).

실제 사이트 조사(gstack browse, 2026-07-07) 결과:
- React SPA라 목록의 행 링크가 전부 href="javascript:void(0)"이고 DOM에 항목 ID를
  담은 data-* 속성이 없다 — 실제 상세페이지 URL은 행을 클릭해야만 알 수 있다.
- 페이지네이션도 URL 쿼리파라미터가 아니라 클라이언트 상태(스핀버튼+이동 버튼)다.
- 목록 자체에 공개구분(공개/비공개/부분공개)이 이미 표시되어 있어, 정보공개포털의
  disclosure_status 버그(JS가 나중에 채우는 값을 정적 HTML로 못 읽던 문제)는 여기선
  해당 없다.
- 상세페이지의 "공개제한근거"(예: "5호")가 실제 정보공개법 제9조 비공개 사유 호수다.

읽는 순서 제안: 이 파일의 `fetch_list()`부터 읽되, 그 안에서 페이지 단위로 버퍼링하는
이유(가장 위험한 암묵적 계약)는 `browse_client.py`의 "읽는 순서" 노트와
`ARCHITECTURE.md`에 설명되어 있다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from bs4 import BeautifulSoup

from rd2.adapters import browse_client
from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.file_select import pick_primary_file
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.files import save_body_file
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_PRISM

_DOC_TYPE = DOC_TYPE_RESEARCH_REPORT

_DISCLOSURE_TEXT_MAP = {
    "공개": DisclosureStatus.OPEN,
    "부분공개": DisclosureStatus.PARTIAL,
    "비공개": DisclosureStatus.CLOSED,
}

# 정보공개법 제9조 비공개 대상 정보: 제1~4호=기밀(C), 제5~8호=민감(S).
# 설계 문서(안순현-design-20260706-133640.md) "C/S 트랙" 섹션 참고.
_CLAUSE_TO_CSO: dict[int, CsoClassification] = {
    1: CsoClassification.C,
    2: CsoClassification.C,
    3: CsoClassification.C,
    4: CsoClassification.C,
    5: CsoClassification.S,
    6: CsoClassification.S,
    7: CsoClassification.S,
    8: CsoClassification.S,
}


def _parse_clause_numbers(clause_text: str) -> list[int]:
    """"5호", "5호 6호 7호"(nbsp로 구분됨, 실사로 확인) 같은 텍스트에서 조항 번호
    전부를 뽑는다. 한 문서가 여러 호에 동시 해당하는 경우가 실제로 있다."""
    import re as _re

    return [int(m) for m in _re.findall(r"\d+", clause_text)]


class PrismAdapter(SourceAdapter):
    source_name = SOURCE_PRISM

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        max_items: int | None = None,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록을 페이지 단위로 순회하며, 각 행을 클릭해 상세페이지 URL을 미리 알아낸다
        (행에 href/데이터 속성이 없어 클릭 전에는 URL을 알 수 없음 — 모듈 독스트링 참고).

        중요: 한 페이지의 모든 행을 클릭까지 끝낸 뒤에야 yield한다(페이지 단위 버퍼링).
        제너레이터가 각 행마다 바로 yield하면, 호출부가 parse_detail()로 상세페이지에
        네비게이션한 뒤 제너레이터를 재개할 때 브라우저가 이미 목록 페이지를 벗어나
        있어 다음 행 클릭이 ROW_NOT_FOUND로 실패한다(실사로 재현·확인된 버그) —
        fetch_list와 parse_detail이 같은 브라우저 세션을 공유해서 생기는 상호작용
        버그라, 한 페이지 분량을 다 처리해서 목록 브라우징이 끝난 뒤에 넘겨야 한다.

        skip: 이 개수만큼 앞에서 건너뛴다. 건너뛰는 행은 상세 URL을 알아내는 클릭
        (행당 서브프로세스 호출 5~6회, browse_client.fetch_prism_detail_url 참고)
        자체를 하지 않는다 — 목록 페이지 HTML만 읽어 유효 행 수를 세고 넘어간다.
        (plan-eng-review 2026-07-08 발견: 이전엔 caller가
        fetch_list(max_items=skip+count)로 받은 뒤 앞부분을 파이썬에서 버렸는데,
        그 버려지는 행도 fetch_list 안에서 이미 클릭까지 다 끝난 뒤였다 — 체크포인트
        재개 위치가 커질수록(예: 8,000번째 재개) 매번 그만큼을 처음부터 다시
        클릭하는 O(n) 재작업이었다.)
        """
        yielded = 0
        seen = 0
        page = 1
        while True:

            def _do_fetch_page(p: int = page) -> str:
                return browse_client.fetch_prism_list_page(p)

            html = with_retry(_do_fetch_page)
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("tbody tr")
            if not rows:
                return

            page_items: list[dict] = []
            for row_index, row in enumerate(rows):
                cells = {
                    td.get("aria-label"): td.get_text(strip=True)
                    for td in row.find_all("td")
                    if td.get("aria-label")
                }
                if not cells.get("연구과제명") and not cells:
                    continue

                seen += 1
                if seen <= skip:
                    continue  # 건너뛸 행 — 클릭(상세 URL 조회) 자체를 하지 않는다.

                def _do_click(idx: int = row_index, total: int = len(rows), p: int = page) -> str:
                    return browse_client.fetch_prism_detail_url(idx, total, page_number=p)

                detail_url = with_retry(_do_click)

                page_items.append(
                    {
                        "_title": cells.get("연구과제명"),
                        "_subject_category": cells.get("연구분야") or None,
                        "_disclosure_text": cells.get("공개구분"),
                        "_performing_agency": cells.get("연구수행기관") or None,
                        "_ordering_agency": cells.get("관리기관"),
                        "_period_text": cells.get("연구기간") or None,
                        "_detail_url": detail_url,
                    }
                )
                if max_items is not None and len(page_items) + yielded >= max_items:
                    break

            for item in page_items:
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            page += 1

    def _download_and_save_files(self, asmt_id: str, title: str) -> tuple[str | None, list[str]]:
        """asmt_id의 파일을 클릭 검증 후 저장한다. 파일이 없거나 전부 클릭 검증에서
        막히면 (None, [])를 반환한다. parse_detail(download_files=True)와
        download_files_for() 양쪽이 공유하는 실제 다운로드 로직."""

        def _do_fetch_file_list() -> list[dict]:
            return browse_client.fetch_prism_file_list(asmt_id)

        file_list = with_retry(_do_fetch_file_list)
        if not file_list:
            return None, []

        def _do_probe_and_download() -> list[dict]:
            return browse_client.probe_and_download_prism_files(file_list)

        downloaded = with_retry(_do_probe_and_download)
        if not downloaded:
            return None, []

        saved: list[tuple[str, dict]] = []
        for file_meta in downloaded:
            path = save_body_file(
                self.files_root,
                self.source_name,
                _DOC_TYPE,
                asmt_id,
                file_meta["fileNm"],
                file_meta["_raw_bytes"],
            )
            saved.append((path, file_meta))
        primary_meta = pick_primary_file(downloaded, title)
        body_file_path = next(path for path, fm in saved if fm is primary_meta)
        other_file_paths = [path for path, fm in saved if fm is not primary_meta]
        return body_file_path, other_file_paths

    def download_files_for(self, source_url: str, title: str) -> tuple[str | None, list[str]]:
        """메타데이터 수집(download_files=False) 때 미뤄둔 파일 다운로드를 나중에
        별도로 실행하기 위한 진입점(2-pass 백필용). 상세페이지를 다시 열어야
        "다운로드" 링크를 클릭 검증할 수 있으므로 새로 navigate한다."""

        def _do_goto() -> str:
            return browse_client.fetch_rendered_detail_html(source_url)

        with_retry(_do_goto)
        asmt_id = source_url.rstrip("/").rsplit("/", 1)[-1]
        return self._download_and_save_files(asmt_id, title)

    def parse_detail(self, raw_item: dict, *, download_files: bool = True) -> dict:
        url = raw_item["_detail_url"]

        def _do_request() -> str:
            return browse_client.fetch_rendered_detail_html(url)

        html = with_retry(_do_request)
        soup = BeautifulSoup(html, "html.parser")

        def _cell_after_rowheader(label: str) -> str | None:
            th = soup.find("th", string=label)
            if not th:
                return None
            td = th.find_next_sibling("td")
            return td.get_text(strip=True) if td else None

        detail = dict(raw_item)
        detail["_department"] = _cell_after_rowheader("관리부서")
        detail["_clause_text"] = _cell_after_rowheader("공개제한근거")
        detail["_non_disclosure_reason"] = _cell_after_rowheader("비공개사유")
        detail["_abstract"] = _cell_after_rowheader("초록")
        detail["_toc"] = _cell_after_rowheader("목차")
        detail["_contract_performing_agency"] = _cell_after_rowheader("수행기관")

        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        detail["_files_pending"] = False
        # 실사(2026-07-07)로 확인: disclosure_status 라벨이 아니라 상세페이지의 실제
        # "다운로드" 링크를 클릭했을 때 사이트가 막는지가 진짜 기준이다 — 비공개
        # 문서는 클릭 시 alert("비공개 연구보고서입니다.")로 막고 네트워크 요청 자체를
        # 안 보내고, 부분공개 문서는 일부 파일(예: 활용결과보고서)만 링크가 있어 그것만
        # 정상 통과한다. entire/info API가 나열하는 파일을 무조건 받으면 사이트가
        # 의도적으로 막은 접근을 우회하는 것이므로, 반드시 클릭 검증을 거친다.
        #
        # download_files=False일 때는 이 느린 클릭 검증 루프(파일당 최대 15초 대기)를
        # 건너뛰고 메타데이터만 저장한다 — 실제 다운로드는 큐에 등록해 나중에
        # download_files_for()로 별도 실행한다(수집 처리량이 다운로드 속도에
        # 발목잡히지 않도록 분리).
        asmt_id = url.rstrip("/").rsplit("/", 1)[-1]

        if download_files:
            title = raw_item.get("_title") or ""
            body_file_path, other_file_paths = self._download_and_save_files(asmt_id, title)
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        else:
            def _do_fetch_file_list() -> list[dict]:
                return browse_client.fetch_prism_file_list(asmt_id)

            file_list = with_retry(_do_fetch_file_list)
            detail["_files_pending"] = bool(file_list)
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        disclosure_text = enriched_item.get("_disclosure_text")
        if not disclosure_text or disclosure_text not in _DISCLOSURE_TEXT_MAP:
            raise ValueError(f"알 수 없거나 비어있는 공개구분: {disclosure_text!r}")
        disclosure_status = _DISCLOSURE_TEXT_MAP[disclosure_text]

        cso_sub_clause = None
        non_disclosure_reason = None
        if disclosure_status == DisclosureStatus.OPEN:
            cso_classification = CsoClassification.O
        elif disclosure_status == DisclosureStatus.PARTIAL:
            # 실사로 확인(2026-07-07): 부분공개 항목은 상세페이지에 "공개제한근거"
            # 자체가 없다(비공개 항목만 제공됨 — rowheader도 "연구보고서"가 아니라
            # "부분공개 연구보고서"로 다르게 나옴). 정보공개포털과 같은 원칙으로
            # 근사 매핑한다: 부분공개는 완전 비공개보다 덜 제한적이므로 S로 근사.
            cso_classification = CsoClassification.S
            non_disclosure_reason = (
                "부분공개 — PRISM은 이 상태에 대해 공개제한근거(조항)를 제공하지 않음"
            )
        else:
            clause_text = enriched_item.get("_clause_text")
            clause_nums = _parse_clause_numbers(clause_text) if clause_text else []
            valid_nums = [n for n in clause_nums if n in _CLAUSE_TO_CSO]
            if not valid_nums:
                raise ValueError(
                    f"공개제한근거를 확인할 수 없음(clause_text={clause_text!r}) — "
                    "C/S 분류 불가로 격리 처리"
                )
            # 한 문서가 여러 호에 동시 해당할 수 있음(실사로 확인, 예: "5호 6호 7호").
            # 기밀(제1~4호)과 민감(제5~8호)이 섞여 있으면 더 restrictive한 C를 택한다 —
            # disclosure_status 기본값과 같은 원칙: 불확실하면 더 안전한(제한적인) 쪽으로.
            cso_classification = (
                CsoClassification.C
                if any(n <= 4 for n in valid_nums)
                else CsoClassification.S
            )
            cso_sub_clause = ",".join(str(n) for n in valid_nums)
            non_disclosure_reason = enriched_item.get("_non_disclosure_reason")

        start_date_val = None
        end_date_val = None
        period_text = enriched_item.get("_period_text")
        if period_text and "~" in period_text:
            start_str, end_str = (p.strip() for p in period_text.split("~", 1))
            try:
                start_date_val = datetime.strptime(start_str, "%Y-%m-%d").date()
                end_date_val = datetime.strptime(end_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        # 비공개 연구의 초록은 "본 과제는 비공개 연구입니다..." 안내문이지 실제
        # 내용이 아니다 — 실제 본문처럼 저장되지 않도록 공개 트랙일 때만 사용한다
        # (open_go_kr.py의 body_text 오염 버그와 같은 패턴을 여기서는 처음부터 피함).
        body_text = None
        if disclosure_status == DisclosureStatus.OPEN:
            body_text = enriched_item.get("_abstract") or None

        return Document(
            title=enriched_item["_title"],
            ordering_agency=enriched_item["_ordering_agency"],
            department=enriched_item.get("_department"),
            production_date=None,
            disclosure_status=disclosure_status,
            subject_category=enriched_item.get("_subject_category"),
            content_summary=enriched_item.get("_abstract") if body_text is None else None,
            body_text=body_text,
            table_of_contents=enriched_item.get("_toc"),
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            non_disclosure_reason=non_disclosure_reason,
            cso_classification=cso_classification,
            cso_sub_clause=cso_sub_clause,
            performing_agency=enriched_item.get("_contract_performing_agency")
            or enriched_item.get("_performing_agency"),
            start_date=start_date_val,
            end_date=end_date_val,
            source=self.source_name,
            source_url=enriched_item["_detail_url"],
            doc_type=_DOC_TYPE,
            is_synthetic=False,
        )
