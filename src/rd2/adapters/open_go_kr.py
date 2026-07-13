"""정보공개포털(open.go.kr) 사전정보공개 목록 어댑터.

RD-2 v1.1 최상위 4개 출처 중 하나. 실제 사이트 조사(gstack browse) 결과:
- 목록: POST /othicInfo/infoList/infoList.ajax (JSON)
- 상세: GET /othicInfo/infoList/infoListDetl2.do?prdnNstRgstNo=...&prdnDt=...&nstSeCd=...
- 국장급/부단체장 이상 결재문서가 아니면 본문(body_text)은 제공되지 않고
  정보공개청구 안내 메시지만 반환된다 — 그 경우 body_text=None으로 저장한다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterator
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from rd2.adapters import browse_client
from rd2.adapters.base import SourceAdapter
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_BUDGET_EXECUTION,
    DOC_TYPE_BUSINESS_TRIP,
    DOC_TYPE_NOTICE,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
    SOURCE_OPEN_GO_KR,
)

BASE_URL = "https://www.open.go.kr"
LIST_ENDPOINT = f"{BASE_URL}/othicInfo/infoList/infoList.ajax"
DETAIL_ENDPOINT = f"{BASE_URL}/othicInfo/infoList/infoListDetl2.do"

_DISCLOSURE_TEXT_MAP = {
    "공개": DisclosureStatus.OPEN,
    "부분공개": DisclosureStatus.PARTIAL,
    "비공개": DisclosureStatus.CLOSED,
}

# 본문 미제공 안내 문구는 케이스마다 문구가 달라 마커 하나로는 못 잡는다(실사로 확인):
# "필요 시 정보공개 청구신청 하시기 바랍니다"(국장급 미만), "비공개 문서이므로 열람이
# 불가능"(비공개), "YYYY.MM.DD까지 열람이 제한"(열람 제한 기간, 2026-07-07 발견 —
# "청구신청"이 없어 이전 마커로는 안 걸러지고 그대로 body_text에 저장되고 있었음).
_NO_BODY_MARKERS = ("청구신청", "열람이 불가능", "열람이 제한")


def _infer_doc_type(title: str) -> str:
    """사전정보공개 목록은 절차상 전부 "결재문서"라 doc_type을 공문 하나로
    고정해뒀었는데, 실제 제목을 보면 문서 성격 자체는 여러 갈래로 갈린다
    (molit.py의 회의록 분리와 동일한 문제, 2026-07-13 사용자 지적으로 추가).

    버킷·키워드는 처음엔 눈에 띄는 사례 몇 건으로 추측해서 시작했는데(인사/승인/
    회신·통보/보고/공고 5개), 실제 500건 표본(최근 3년치, 목록 조회만으로 충분 —
    parse_detail 없이도 INFO_SJ 제목만 보면 됨)으로 검증해보니 그 5개가 전체의
    25%밖에 못 잡았다. 압도적 1위는 지급/지출/카드/원인행위/품의 등 예산집행
    계열(52%)이었는데 처음 추측엔 아예 없었던 카테고리다 — 표본 없이 감으로
    분류 체계를 짜면 이렇게 실제 분포와 어긋난다는 걸 보여주는 사례.
    최종 9개 버킷으로 표본의 91%를 커버(2026-07-13 재검증). 나머지 9%는 철도
    운영 로그처럼 기관별로 완전히 이질적인 소수 항목이라 규칙을 더 늘리는 게
    비효율적이라 판단해 공문(기본값)으로 남겨둔다.

    분류 순서: 더 구체적인 카테고리를 먼저 검사해 "승인 요청 보고" 같이 여러
    키워드가 섞인 제목에서도 더 구체적인 분류가 이긴다. 예산집행 키워드가
    "승인"보다 먼저 검사되는 이유: 이 포털에서 "품의"/"지급" 문서에 "승인요청"
    문구가 같이 나오는 경우가 실제로 있는데, 그런 문서는 시민 대상 인허가
    승인이 아니라 내부 예산 결재 절차이므로 예산집행으로 분류하는 게 맞다."""
    if any(
        keyword in title
        for keyword in ("인사발령", "인사 발령", "발령", "휴직", "복직", "임용", "채용", "호봉")
    ):
        return DOC_TYPE_PERSONNEL
    if any(
        keyword in title
        for keyword in (
            "지급", "지출", "카드", "원인행위", "품의", "계약방법결정", "구입", "교부",
            "강사비", "구매", "경비", "환불", "반납", "지불",
        )
    ):
        return DOC_TYPE_BUDGET_EXECUTION
    if "승인" in title:
        return DOC_TYPE_APPROVAL
    if any(keyword in title for keyword in ("회신", "통보", "통지")):
        return DOC_TYPE_REPLY_NOTIFICATION
    if "계획" in title:
        return DOC_TYPE_PLAN
    if any(keyword in title for keyword in ("보고", "제출", "결과", "송부", "접수")):
        return DOC_TYPE_REPORT
    if any(keyword in title for keyword in ("공고", "안내", "알림", "협조", "공모")):
        return DOC_TYPE_NOTICE
    if "출장" in title:
        return DOC_TYPE_BUSINESS_TRIP
    return DOC_TYPE_OFFICIAL_DOCUMENT


class OpenGoKrAdapter(SourceAdapter):
    source_name = SOURCE_OPEN_GO_KR

    def fetch_list(
        self,
        *,
        start_date: date,
        end_date: date,
        max_items: int | None = None,
        row_page: int = 10,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록 조회.

        주의: 정보공개포털의 목록 AJAX 엔드포인트는 순수 HTTP 클라이언트를
        봇으로 차단한다 (세션 쿠키·Referer·Origin·User-Agent를 다 맞춰도
        빈 결과만 반환됨 — 구조 조사 단계에서 확인). 따라서 목록 조회는
        gstack 헤드리스 브라우저(browse_client)를 거쳐 같은 오리진에서
        fetch()를 실행한다. 상세 페이지(parse_detail)는 봇 탐지가 없어
        httpx로 충분하다.

        skip: 대량 수집 체크포인트 재개용. 이 목록은 페이지당 순수 JSON AJAX
        호출 한 번뿐이라(PRISM처럼 행마다 브라우저 클릭이 필요 없음) 건너뛸
        페이지까지는 그냥 요청 자체를 안 보내고, 시작 페이지 안에서 남는
        건수만 파이썬에서 슬라이싱한다 — PRISM의 "건너뛰는 행도 비용이 든다"
        문제(TODOS.md)가 이 어댑터엔 애초에 해당하지 않는다.
        """
        page = (skip // row_page) + 1
        remaining_skip = skip % row_page
        yielded = 0
        while True:
            def _do_request(p: int = page) -> dict:
                return browse_client.fetch_list_page(
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    view_page=p,
                    row_page=row_page,
                )

            payload = with_retry(_do_request)
            items = payload.get("result", {}).get("rtnList", [])
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

    def parse_detail(self, raw_item: dict) -> dict:
        params = {
            "prdnNstRgstNo": raw_item["PRDCTN_INSTT_REGIST_NO"],
            "prdnDt": raw_item["PRDCTN_DT"],
            "nstSeCd": raw_item["INSTT_SE_CD"],
        }
        url = f"{DETAIL_ENDPOINT}?{urlencode(params)}"

        def _do_request() -> str:
            # 공개여부(dlsrCdNm)는 JS가 AJAX 응답으로 채워 넣는 값이라 순수 HTTP로는
            # 항상 빈 값으로 온다 — 헤드리스 브라우저로 렌더링해야 실제 값을 읽는다
            # (browse_client.fetch_rendered_detail_html 독스트링 참고).
            return browse_client.fetch_rendered_detail_html(url)

        html = with_retry(_do_request)
        soup = BeautifulSoup(html, "html.parser")

        def _text(elem_id: str) -> str | None:
            el = soup.find(id=elem_id)
            return el.get_text(strip=True) if el else None

        detail = dict(raw_item)
        detail["_title"] = _text("infoSj")
        detail["_agency"] = _text("prcsNstNm")
        detail["_department"] = _text("chrgDeptNm")
        detail["_unit_task"] = _text("unitJobNm")
        detail["_disclosure_text"] = _text("dlsrCdNm")
        detail["_subject_category"] = _text("nstClNm")

        # 본문파일 행: id 없이 마지막 <tr>에 있음 — "본문파일" 라벨의 형제 <td> 텍스트를 찾는다.
        body_label = soup.find("th", string="본문파일")
        body_text_raw = None
        if body_label:
            body_td = body_label.find_next_sibling("td")
            if body_td:
                body_text_raw = body_td.get_text(strip=True)
        detail["_body_text_raw"] = body_text_raw
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        # 공개여부를 확인 못했다고 "공개"로 간주하면 실제 비공개 문서가 공개로 오분류될
        # 위험이 있다(2026-07-07 실사로 발견) — 확인 불가 시 예외를 던져 quarantine 처리한다.
        disclosure_text = enriched_item.get("_disclosure_text")
        if not disclosure_text:
            raise ValueError("공개여부(dlsrCdNm)를 확인할 수 없음 — 공개로 간주하지 않고 격리 처리")
        if disclosure_text not in _DISCLOSURE_TEXT_MAP:
            raise ValueError(f"알 수 없는 공개여부 값: {disclosure_text!r}")
        disclosure_status = _DISCLOSURE_TEXT_MAP[disclosure_text]

        body_raw = enriched_item.get("_body_text_raw")
        body_text = None
        if body_raw and not any(marker in body_raw for marker in _NO_BODY_MARKERS):
            body_text = body_raw

        production_date = None
        p_date_str = enriched_item.get("P_DATE")
        if p_date_str:
            production_date = datetime.strptime(p_date_str, "%Y%m%d").date()

        # 이 소스(사전정보공개 정보목록) 상세페이지는 공개여부(공개/부분공개/비공개)만
        # 제공하고, 어떤 법적 근거 조항(제1~8호)으로 비공개인지는 구조적으로 제공하지
        # 않는다(2026-07-07 실사로 확인 — 페이지 어디에도 비공개사유 필드가 없음).
        # 따라서 정확한 C/S 조항 분류는 이 어댑터로 할 수 없고, disclosure_status의
        # 심각도만으로 근사 매핑한다: 비공개(완전 비공개)→C, 부분공개→S.
        # 실제 조항 근거가 있는 분류는 원문정보 어댑터(TODOS.md P1)의 몫이다.
        non_disclosure_reason = None
        if disclosure_status == DisclosureStatus.CLOSED:
            cso_classification = CsoClassification.C
            non_disclosure_reason = "비공개 — 구체적 법적 근거 조항은 이 소스(정보목록)에서 확인 불가"
        elif disclosure_status == DisclosureStatus.PARTIAL:
            cso_classification = CsoClassification.S
            non_disclosure_reason = "부분공개 — 구체적 법적 근거 조항은 이 소스(정보목록)에서 확인 불가"
        else:
            cso_classification = CsoClassification.O

        doc_no = enriched_item.get("DOC_NO", "")
        source_url = (
            f"{DETAIL_ENDPOINT}?prdnNstRgstNo={enriched_item['PRDCTN_INSTT_REGIST_NO']}"
            f"&prdnDt={enriched_item['PRDCTN_DT']}&nstSeCd={enriched_item['INSTT_SE_CD']}"
        )
        title = enriched_item.get("_title") or enriched_item["INFO_SJ"]

        return Document(
            title=title,
            ordering_agency=enriched_item.get("_agency") or enriched_item["PROC_INSTT_NM"],
            department=enriched_item.get("_department") or enriched_item.get("CHRG_DEPT_NM"),
            unit_task=enriched_item.get("_unit_task") or enriched_item.get("UNIT_JOB_NM"),
            production_date=production_date,
            disclosure_status=disclosure_status,
            subject_category=enriched_item.get("_subject_category"),
            content_summary=doc_no or None,
            body_text=body_text,
            non_disclosure_reason=non_disclosure_reason,
            cso_classification=cso_classification,
            source=self.source_name,
            source_url=source_url,
            doc_type=_infer_doc_type(title),
            is_synthetic=False,
        )
