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
from rd2.storage.naming import DOC_TYPE_OFFICIAL_DOCUMENT, SOURCE_OPEN_GO_KR

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


class OpenGoKrAdapter(SourceAdapter):
    source_name = SOURCE_OPEN_GO_KR

    def fetch_list(
        self,
        *,
        start_date: date,
        end_date: date,
        max_items: int | None = None,
        row_page: int = 10,
    ) -> Iterator[dict]:
        """목록 조회.

        주의: 정보공개포털의 목록 AJAX 엔드포인트는 순수 HTTP 클라이언트를
        봇으로 차단한다 (세션 쿠키·Referer·Origin·User-Agent를 다 맞춰도
        빈 결과만 반환됨 — 구조 조사 단계에서 확인). 따라서 목록 조회는
        gstack 헤드리스 브라우저(browse_client)를 거쳐 같은 오리진에서
        fetch()를 실행한다. 상세 페이지(parse_detail)는 봇 탐지가 없어
        httpx로 충분하다.
        """
        page = 1
        yielded = 0
        while True:
            def _do_request() -> dict:
                return browse_client.fetch_list_page(
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    view_page=page,
                    row_page=row_page,
                )

            payload = with_retry(_do_request)
            items = payload.get("result", {}).get("rtnList", [])
            if not items:
                return
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

        return Document(
            title=enriched_item.get("_title") or enriched_item["INFO_SJ"],
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
            doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            is_synthetic=False,
        )
