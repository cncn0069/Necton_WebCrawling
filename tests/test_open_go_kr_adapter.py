import json
from datetime import date

import pytest

from rd2.adapters import open_go_kr
from rd2.adapters.open_go_kr import OpenGoKrAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import SOURCE_OPEN_GO_KR

SAMPLE_LIST_ITEM = {
    "CHRG_DEPT_NM": "미래교육과",
    "INFO_SJ": "2026년 학교 디지털인프라 중점 개선 사업 현장 확인 결과 알림",
    "DOC_NO": "미래교육과-9000",
    "PRDCTN_DT": "20260705212608",
    "PROC_INSTT_NM": "강원특별자치도교육청",
    "UNIT_JOB_NM": "정보화교육지원",
    "P_DATE": "20260705",
    "PRDCTN_INSTT_REGIST_NO": "K10CB261853784277000",
    "INSTT_SE_CD": "E",
    "OTHBC_SE_CD": "3",
}

SAMPLE_DETAIL_HTML = """
<table class="gridTable">
<tbody>
<tr><td headers="infoDetlTableInfoSj" id="infoSj"><p><strong>2026년 학교 디지털인프라 중점 개선 사업 현장 확인 결과 알림</strong></p></td></tr>
<tr><td id="prcsNstNm"><p>강원특별자치도교육청</p></td><td id="chrgDeptNm"><p>미래교육과</p></td></tr>
<tr><td id="unitJobNm"><p>정보화교육지원</p></td><td id="dlsrCdNm"><p class="blue_text"><strong>공개</strong></p></td></tr>
<tr><td id="nstClNm"><p>교육 &gt; 정보화 &gt; 인프라 &gt; 정보화교육지원</p></td></tr>
<tr><th scope="row"><strong>본문파일</strong></th><td colspan="3"><p>결재문서의 원문공개 대상(국장급,부단체장 등 이상)이 아닙니다.<br>필요 시 정보공개 청구신청 하시기 바랍니다.</p></td></tr>
</tbody>
</table>
"""

SAMPLE_DETAIL_HTML_CLOSED = SAMPLE_DETAIL_HTML.replace(
    '<td id="dlsrCdNm"><p class="blue_text"><strong>공개</strong></p></td>',
    '<td id="dlsrCdNm"><p class="blue_text"><strong>비공개</strong></p></td>',
).replace(
    "결재문서의 원문공개 대상(국장급,부단체장 등 이상)이 아닙니다.<br>필요 시 정보공개 청구신청 하시기 바랍니다.",
    "본 문서는 비공개 문서이므로 열람이 불가능 합니다. 필요 시 청구신청 하시기 바랍니다.",
)

SAMPLE_DETAIL_HTML_PARTIAL = SAMPLE_DETAIL_HTML.replace(
    '<td id="dlsrCdNm"><p class="blue_text"><strong>공개</strong></p></td>',
    '<td id="dlsrCdNm"><p class="blue_text"><strong>부분공개</strong></p></td>',
)


@pytest.fixture
def adapter(monkeypatch):
    # 목록 조회, 상세 페이지 조회 둘 다 gstack browse(헤드리스 브라우저) 서브프로세스를
    # 거치므로, 유닛 테스트에서는 실제 브라우저를 띄우지 않도록 둘 다 패치한다.
    # 상세 페이지는 dlsrCdNm/nstClNm처럼 JS가 채워 넣는 값이 있어 정적 HTML로는
    # 검증할 수 없다 — SAMPLE_DETAIL_HTML은 렌더링 후(post-JS) HTML을 흉내낸다.
    call_count = {"n": 0}

    def _fake_fetch_list_page(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"result": {"rtnList": [SAMPLE_LIST_ITEM]}}
        return {"result": {"rtnList": []}}

    monkeypatch.setattr(
        open_go_kr.browse_client, "fetch_list_page", _fake_fetch_list_page
    )
    monkeypatch.setattr(
        open_go_kr.browse_client,
        "fetch_rendered_detail_html",
        lambda url: SAMPLE_DETAIL_HTML,
    )

    return OpenGoKrAdapter()


def test_fetch_list_returns_items(adapter):
    items = list(
        adapter.fetch_list(
            start_date=date(2026, 6, 7), end_date=date(2026, 7, 6), max_items=1
        )
    )
    assert len(items) == 1
    assert items[0]["INFO_SJ"] == SAMPLE_LIST_ITEM["INFO_SJ"]


def test_parse_detail_extracts_fields(adapter):
    detail = adapter.parse_detail(SAMPLE_LIST_ITEM)
    assert detail["_agency"] == "강원특별자치도교육청"
    assert detail["_department"] == "미래교육과"
    assert detail["_disclosure_text"] == "공개"
    assert "정보화" in detail["_subject_category"]
    assert "정보공개" in detail["_body_text_raw"]  # 본문 미제공 안내 메시지


def test_to_schema_builds_valid_document_without_body(adapter):
    detail = adapter.parse_detail(SAMPLE_LIST_ITEM)
    doc = adapter.to_schema(detail)
    assert doc.title == SAMPLE_LIST_ITEM["INFO_SJ"]
    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.is_synthetic is False
    assert doc.body_text is None  # 안내 메시지였으므로 본문 없음으로 처리
    assert doc.production_date.isoformat() == "2026-07-05"


def test_end_to_end_fetch_parse_to_schema(adapter):
    items = list(
        adapter.fetch_list(
            start_date=date(2026, 6, 7), end_date=date(2026, 7, 6), max_items=1
        )
    )
    detail = adapter.parse_detail(items[0])
    doc = adapter.to_schema(detail)
    assert doc.source == SOURCE_OPEN_GO_KR
    assert doc.cso_classification.value == "O"


def test_closed_document_maps_to_c_track(adapter, monkeypatch):
    # 이 소스는 비공개 사유 조항(제1~8호)을 알 수 없으므로, 비공개는 C로 근사 매핑한다
    # (2026-07-07 office-hours 결정).
    monkeypatch.setattr(
        open_go_kr.browse_client,
        "fetch_rendered_detail_html",
        lambda url: SAMPLE_DETAIL_HTML_CLOSED,
    )
    detail = adapter.parse_detail(SAMPLE_LIST_ITEM)
    doc = adapter.to_schema(detail)
    assert doc.disclosure_status == DisclosureStatus.CLOSED
    assert doc.cso_classification == CsoClassification.C
    assert doc.is_synthetic is False
    assert doc.body_text is None  # "비공개 문서이므로 열람 불가" 안내가 본문으로 새지 않아야 함
    assert doc.non_disclosure_reason is not None


def test_fetch_list_skip_skips_within_and_across_pages(monkeypatch):
    """skip은 페이지 경계를 넘나들며 정확한 위치부터 재개해야 한다(체크포인트 재개용).
    row_page=10일 때 skip=12는 2페이지(0-based index 2, 전체 12번째 항목)부터 시작."""
    pages = {
        1: [{"INFO_SJ": f"item-{i}"} for i in range(10)],
        2: [{"INFO_SJ": f"item-{i}"} for i in range(10, 20)],
    }

    def _fake_fetch_list_page(*, view_page, **kwargs):
        return {"result": {"rtnList": pages.get(view_page, [])}}

    monkeypatch.setattr(open_go_kr.browse_client, "fetch_list_page", _fake_fetch_list_page)

    adapter = OpenGoKrAdapter()
    items = list(
        adapter.fetch_list(
            start_date=date(2026, 6, 7), end_date=date(2026, 7, 6), max_items=3, skip=12
        )
    )
    assert [i["INFO_SJ"] for i in items] == ["item-12", "item-13", "item-14"]


@pytest.mark.parametrize(
    "title,expected",
    [
        ("2026년 상반기 정기 인사발령 알림", "personnel"),
        ("2026년 8월 복직예정 교육공무원 호봉재획정 요청", "personnel"),
        ("[품의] 2026학년도 제2회 심의위원회 기타수당 지급", "budget_execution"),
        ("[카드] 연수원 보도블록 청소(이끼제거)", "budget_execution"),
        ("궤도재료(이음매PCT) 운송비용 지불결의", "budget_execution"),
        ("[협조] 신청서 승인요청 안내", "approval"),
        ("[회신] 2026년 상반기 해외특화사업 실적 제출요청", "reply_notification"),
        ("성희롱 성폭력 사안 발생 사실 통보서 제출", "reply_notification"),
        ("2026 파주 AI-ROAD 찾아가는 맞춤형 교원 연수 운영 계획(안)", "plan"),
        ("(알제리) 공용차량 보험료 납부 결과 보고", "report"),
        ("길 위의 인문학 참가자 접수 현황", "report"),
        ("[공모] 2026 학생주도 학교특색스포츠클럽활동 지원 신청 알림", "notice"),
        ("제75회 학교폭력대책심의위원회 참석 출장 요청", "business_trip"),
        ("아무 키워드도 없는 일반 공문 제목", "official_document"),
    ],
)
def test_infer_doc_type_from_title_keywords(title, expected):
    assert open_go_kr._infer_doc_type(title) == expected


def test_to_schema_sets_doc_type_from_title(adapter):
    detail = adapter.parse_detail(SAMPLE_LIST_ITEM)
    detail["_title"] = "[회신] 실적 제출요청"
    doc = adapter.to_schema(detail)
    assert doc.doc_type == "reply_notification"


def test_partial_document_maps_to_s_track(adapter, monkeypatch):
    monkeypatch.setattr(
        open_go_kr.browse_client,
        "fetch_rendered_detail_html",
        lambda url: SAMPLE_DETAIL_HTML_PARTIAL,
    )
    detail = adapter.parse_detail(SAMPLE_LIST_ITEM)
    doc = adapter.to_schema(detail)
    assert doc.disclosure_status == DisclosureStatus.PARTIAL
    assert doc.cso_classification == CsoClassification.S
    assert doc.is_synthetic is False
