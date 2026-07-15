from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import alio
from rd2.adapters.alio import AlioAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_AUDIT_RESULT, DOC_TYPE_DIRECTOR_ACTIVITY, SOURCE_ALIO

# 실제 사이트(alio.go.kr) /search/findTotalSearch.json 응답 구조를 그대로 축약한
# 샘플(2026-07-09 httpx 직접 요청으로 실사 확인 — commonMap/searchList 구조).
SAMPLE_PAGE_1 = {
    "status": "success",
    "data": {
        "commonMap": {"TOTAL_COUNT_attach": "3", "SECTION_NAME_attach": "attach"},
        "searchList": [
            {
                "SECTION_NAME": "attach",
                "DISCLOSURE_NO": "2026070903206963",
                "SUBMISSION_NO": "2026070810438186",
                "FILE_NO": "101",
                "IDATE": "2026.07.09",
                "TITLE": "내부·외부 <b>감사결과</b>",
                "APBA_NA": "한국전력공사",
            },
            {
                "SECTION_NAME": "attach",
                "DISCLOSURE_NO": "2026070803206226",
                "SUBMISSION_NO": "2026070710434608",
                "FILE_NO": "101",
                "IDATE": "2026.07.08",
                "TITLE": "내부·외부 <b>감사결과</b>",
                "APBA_NA": "한국전력거래소",
            },
            {
                "SECTION_NAME": "attach",
                "DISCLOSURE_NO": "2026061803197755",
                "SUBMISSION_NO": "2026061810422420",
                "FILE_NO": "105",
                "IDATE": "2026.06.18",
                "TITLE": "<b>감사결과</b> 처분요구서",
                "APBA_NA": "주택도시보증공사",
            },
        ],
    },
}

SAMPLE_PAGE_EMPTY = {"status": "success", "data": {"commonMap": {}, "searchList": []}}

# 실제 사이트(alio.go.kr) /upload/disclosure/.../doc.html 조각을 그대로 축약한 샘플
# (2026-07-09 httpx 직접 요청으로 실사 확인 — 제목/제출일/기관 공시 담당자 표 구조).
SAMPLE_DOC_HTML = """
<div id="doc-">
<p class="cover-title">38-2. 내부·외부 감사결과</p>
<table border="1" width="600">
<tbody>
<tr>
<td>제목</td>
<td>2026년도 종합감사결과(260626)</td>
</tr>
<tr>
<td>첨부자료</td>
<td><a href="javascript:report_attach_down('2026년도 종합감사결과(260626).pdf')">2026년도 종합감사결과(260626).pdf</a></td>
</tr>
</tbody>
</table>
<table border="1" width="599">
<tbody>
<tr>
<td>기준일</td>
<td>2026년 06월 26일</td>
<td>제출일</td>
<td>2026년 07월 08일</td>
</tr>
</tbody>
</table>
<table border="1" width="601">
<thead>
<tr><th>구분</th><th>담당자명</th><th>부서명</th><th>전화번호</th></tr>
</thead>
<tbody>
<tr><td>작성자</td><td>정지웅</td><td>감사실 2권역감사부</td><td>02-787-8661</td></tr>
<tr><td>감독자</td><td>박창률</td><td>기획처</td><td>02-345-3500</td></tr>
<tr><td>확인자</td><td>박정진</td><td>감사실</td><td>02-345-3200</td></tr>
</tbody>
</table>
</div>
"""

SAMPLE_TOC_HTML = """
<div>
<ul class="code_list">
<li><a href="#toc-122" title="38-2. 내부&middot;외부 감사결과">38-2. 내부&middot;외부 감사결과</a></li>
<li><a href="#toc-123" title="내부&middot;외부감사 결과">내부&middot;외부감사 결과</a></li>
</ul>
</div>
"""


# 실제 사이트(alio.go.kr) q="개별 비상임이사 활동내용"&section=attach 응답을 그대로
# 축약한 샘플(2026-07-15 httpx 직접 요청으로 실사 확인). 감사결과와 달리
# REPORT_FORM_NA는 section=attach 행에서 항상 null이라(실사 확인) 포함하지
# 않는다 — TITLE(하이라이트 태그 포함)이 매칭 검증에 쓰이는 필드다.
SAMPLE_PAGE_DIRECTOR_ACTIVITY = {
    "status": "success",
    "data": {
        "commonMap": {"TOTAL_COUNT_attach": "3490", "SECTION_NAME_attach": "attach"},
        "searchList": [
            {
                "SECTION_NAME": "attach",
                "DISCLOSURE_NO": "2026071503215451",
                "SUBMISSION_NO": "2026071410445697",
                "FILE_NO": "101",
                "IDATE": "2026.07.15",
                "TITLE": "<b>개별</b> <b>비상임이사</b> <b>활동내용</b>",
                "APBA_NA": "항공안전기술원",
            },
        ],
    },
}

# 실제 사이트(alio.go.kr) doc.html을 그대로 축약한 샘플(2026-07-15 httpx 직접
# 요청으로 실사 확인 — DISCLOSURE_NO=2026071503215451). 감사결과의 doc.html과
# 달리 "제목" 라벨-값 쌍 자체가 없다(회차/개최일/안건내용/활동현황 표 구조라
# 다르다) — 그래서 이 콘텐츠 유형은 title이 검색 API의 TITLE로 유지된다
# (parse_detail()의 "doc_fields.get('title')이 있을 때만 대체" 로직이 자연스럽게
# 처리한다, 별도 분기 불필요).
SAMPLE_DOC_HTML_DIRECTOR_ACTIVITY = """
<div id="doc-">
<p class="cover-title">
<a name="toc-122" class="toc" href="#toc-122" title="30-2. 개별 비상임이사 활동내용">30-2. 개별 비상임이사 활동내용</a>
</p>
<table class="nb" width="600">
<tbody>
<tr>
<td height="30" width="600" align="RIGHT" valign="TOP">항공안전기술원</td>
</tr>
</tbody>
</table>
<p class="SECTION-1">
<a name="toc-123" class="toc" href="#toc-123" title="개별 비상임이사 활동내용">개별 비상임이사 활동내용</a>
</p>
<table border="1" width="791">
<thead>
<tr>
<th height="30" width="151" align="CENTER" valign="MIDDLE">회차</th>
<th height="30" width="150" align="CENTER" valign="MIDDLE">개최일</th>
<th height="30" width="294" align="CENTER" valign="MIDDLE">안건내용</th>
<th height="30" width="196" align="CENTER" valign="MIDDLE">활동현황</th>
</tr>
</thead>
<tbody>
<tr>
<td height="76" width="151" align="CENTER" valign="MIDDLE">3회차</td>
<td height="76" width="150" align="CENTER" valign="MIDDLE">2026년 07월 01일</td>
<td height="76" width="294" align="CENTER" valign="MIDDLE">「직장 내 괴롭힘」 결과 보고<br/>제2026-1차 임시이사회 결과 보고<br/>「보수규정」 개정(안) 승인의 건</td>
<td height="76" width="196" align="CENTER" valign="MIDDLE">
<a href="javascript:report_attach_down('(공시) 2026년 비상임이사 활동내역 현황 3회차.xlsx')">(공시) 2026년 비상임이사 활동내역 현황 3회차.xlsx</a>
</td>
</tr>
</tbody>
</table>
<table border="1" width="599">
<tbody>
<tr>
<td height="30" width="150" align="CENTER" valign="MIDDLE">기준일</td>
<td height="30" width="150" align="CENTER" valign="MIDDLE">2026년 07월 01일</td>
<td height="30" width="150" align="CENTER" valign="TOP">제출일</td>
<td height="30" width="149" align="CENTER" valign="MIDDLE">2026년 07월 14일</td>
</tr>
</tbody>
</table>
<table border="1" width="601">
<thead>
<tr><th height="31" width="105" align="CENTER" valign="MIDDLE">구분</th><th height="31" width="120" align="CENTER" valign="MIDDLE">담당자명</th><th height="31" width="237" align="CENTER" valign="MIDDLE">부서명</th><th height="31" width="139" align="CENTER" valign="MIDDLE">전화번호</th></tr>
</thead>
<tbody>
<tr><td height="30" width="105" align="CENTER" valign="MIDDLE">작성자</td><td height="30" width="120" align="CENTER" valign="MIDDLE">신민균</td><td height="30" width="237" align="CENTER" valign="MIDDLE">기획전략실</td><td height="30" width="139" align="CENTER" valign="MIDDLE">032-727-5602</td></tr>
<tr><td height="30" width="105" align="CENTER" valign="MIDDLE">감독자</td><td height="30" width="120" align="CENTER" valign="MIDDLE">이엘리사</td><td height="30" width="237" align="CENTER" valign="MIDDLE">기획전략실</td><td height="30" width="139" align="CENTER" valign="MIDDLE">032-727-5610</td></tr>
<tr><td height="30" width="105" align="CENTER" valign="MIDDLE">확인자</td><td height="30" width="120" align="CENTER" valign="MIDDLE">임재현</td><td height="30" width="237" align="CENTER" valign="MIDDLE">감사실</td><td height="30" width="139" align="CENTER" valign="MIDDLE">032-727-5521</td></tr>
</tbody>
</table>
</div>
"""


def test_fetch_list_extracts_items_and_cleans_title(monkeypatch):
    monkeypatch.setattr(alio, "_fetch_search_page", lambda query, page, **kw: SAMPLE_PAGE_1)
    adapter = AlioAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_title"] == "내부·외부 감사결과"
    assert items[0]["_disclosure_no"] == "2026070903206963"
    assert items[0]["_file_no"] == "101"
    assert items[1]["_agency"] == "한국전력거래소"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_fetch(query: str, page: int, **kw) -> dict:
        calls.append(page)
        return SAMPLE_PAGE_1

    monkeypatch.setattr(alio, "_fetch_search_page", _fake_fetch)
    adapter = AlioAdapter()
    items = list(adapter.fetch_list(skip=2, max_items=1))
    assert len(items) == 1
    assert items[0]["_disclosure_no"] == "2026061803197755"
    assert calls == [1]  # 페이지 재조회 없이 1페이지 안에서 skip이 처리됨


def test_fetch_list_stops_on_empty_page(monkeypatch):
    pages = [SAMPLE_PAGE_1, SAMPLE_PAGE_EMPTY]

    def _fake_fetch(query: str, page: int, **kw) -> dict:
        return pages[page - 1]

    monkeypatch.setattr(alio, "_fetch_search_page", _fake_fetch)
    adapter = AlioAdapter()
    items = list(adapter.fetch_list())
    assert len(items) == 3  # 다음 페이지가 비어있으면 그대로 종료


def _raw_item() -> dict:
    return {
        "_title": "내부·외부 감사결과",
        "_agency": "한국전력공사",
        "_idate": "2026.07.09",
        "_disclosure_no": "2026070903206963",
        "_submission_no": "2026070810438186",
        "_file_no": "101",
    }


def test_parse_detail_and_to_schema_maps_open_track(monkeypatch, tmp_path):
    downloaded: list[tuple[str, str, str]] = []

    def _fake_download(disclosure_no: str, file_no: str, submission_no: str) -> tuple[bytes, str]:
        downloaded.append((disclosure_no, file_no, submission_no))
        return b"pdf-bytes", "2026년도 종합감사결과(260626).pdf"

    monkeypatch.setattr(alio, "_download_file", _fake_download)
    monkeypatch.setattr(alio, "_fetch_doc_html", lambda disclosure_no: SAMPLE_DOC_HTML)
    monkeypatch.setattr(alio, "_fetch_toc_html", lambda disclosure_no: SAMPLE_TOC_HTML)

    adapter = AlioAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item())
    doc = adapter.to_schema(detail)

    assert downloaded == [("2026070903206963", "101", "2026070810438186")]
    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "한국전력공사"
    # 상세 조각(doc.html)의 실제 보고서명이 검색 API의 일반화된 TITLE을 대체해야 한다.
    assert doc.title == "2026년도 종합감사결과(260626)"
    assert doc.department == "감사실 2권역감사부"
    # 제출일(doc.html)이 있으면 검색 API의 IDATE보다 우선한다.
    assert doc.production_date == date(2026, 7, 8)
    # 기준일→start_date, 제출일→end_date로 매핑된다(2026-07-09 사용자 결정 —
    # 감사 시점과 공개 시점 사이의 간격을 "공개 판단에 걸린 시일"로 본다).
    assert doc.start_date == date(2026, 6, 26)
    assert doc.end_date == date(2026, 7, 8)
    assert doc.table_of_contents == "38-2. 내부·외부 감사결과\n내부·외부감사 결과"
    assert "정지웅" in doc.body_text
    assert doc.doc_type == DOC_TYPE_AUDIT_RESULT
    assert doc.source == SOURCE_ALIO
    # source_url은 다운로드 엔드포인트가 아니라 게시글 상세페이지 주소다(2026-07-09
    # 사용자 요청) — fileNo를 덧붙여 한 공시의 여러 첨부파일이 dedup_key를
    # 공유하지 않게 한다.
    assert doc.source_url == (
        "https://alio.go.kr/item/itemReport.do?seq=2026070903206963"
        "&disclosureNo=2026070903206963&fileNo=101"
    )
    assert doc.non_disclosure_reason is None  # OPEN이라 검증 통과해야 함
    assert doc.body_file_path == str(
        Path(SOURCE_ALIO)
        / DOC_TYPE_AUDIT_RESULT
        / "1-500"
        / "2026070903206963_101_2026년도 종합감사결과(260626).pdf"
    )


def test_parse_detail_and_to_schema_maps_director_activity(monkeypatch, tmp_path):
    """query/doc_type 파라미터화(2026-07-15)가 실제로 두 호출부(save_body_file,
    Document 생성) 모두에 self.doc_type을 흘려보내는지 검증한다 — 하나만
    검증하면 나머지가 여전히 DOC_TYPE_AUDIT_RESULT를 하드코딩해도 테스트가
    통과할 수 있다(plan-eng-review 지적)."""
    downloaded: list[tuple[str, str, str]] = []

    def _fake_download(disclosure_no: str, file_no: str, submission_no: str) -> tuple[bytes, str]:
        downloaded.append((disclosure_no, file_no, submission_no))
        return b"xlsx-bytes", "(공시) 2026년 비상임이사 활동내역 현황 3회차.xlsx"

    monkeypatch.setattr(alio, "_download_file", _fake_download)
    monkeypatch.setattr(alio, "_fetch_doc_html", lambda disclosure_no: SAMPLE_DOC_HTML_DIRECTOR_ACTIVITY)
    monkeypatch.setattr(alio, "_fetch_toc_html", lambda disclosure_no: None)

    adapter = AlioAdapter(
        query="개별 비상임이사 활동내용",
        doc_type=DOC_TYPE_DIRECTOR_ACTIVITY,
        files_root=tmp_path,
    )
    raw_item = {
        "_title": "개별 비상임이사 활동내용",  # _clean_title이 이미 하이라이트 태그 제거한 상태
        "_agency": "항공안전기술원",
        "_idate": "2026.07.15",
        "_disclosure_no": "2026071503215451",
        "_submission_no": "2026071410445697",
        "_file_no": "101",
    }
    detail = adapter.parse_detail(raw_item)
    doc = adapter.to_schema(detail)

    assert downloaded == [("2026071503215451", "101", "2026071410445697")]
    # doc_type이 Document 레코드(DB 컬럼)에 흘러간다 — 하드코딩된 AUDIT_RESULT가
    # 아니라 self.doc_type이어야 한다.
    assert doc.doc_type == DOC_TYPE_DIRECTOR_ACTIVITY
    # doc_type이 파일 저장 경로(save_body_file 호출부)에도 흘러간다 —
    # 두 호출부 중 하나만 고치고 다른 하나를 놓치는 실수를 여기서 잡는다.
    assert doc.body_file_path == str(
        Path(SOURCE_ALIO)
        / DOC_TYPE_DIRECTOR_ACTIVITY
        / "1-500"
        / "2026071503215451_101_(공시) 2026년 비상임이사 활동내역 현황 3회차.xlsx"
    )
    # doc.html에 "제목" 라벨-값 쌍이 없는 콘텐츠 유형이라(회차/개최일/안건내용
    # 표 구조) title이 검색 API의 TITLE로 유지된다 — 감사결과처럼 doc.html의
    # 실제 보고서명으로 대체되지 않는다(이 콘텐츠 유형의 실제 사이트 구조,
    # 2026-07-15 실사 확인).
    assert doc.title == "개별 비상임이사 활동내용"
    assert doc.department == "기획전략실"
    assert doc.start_date == date(2026, 7, 1)
    assert doc.end_date == date(2026, 7, 14)
    assert "3회차" in doc.body_text
    assert "직장 내 괴롭힘" in doc.body_text
    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O


def test_parse_detail_quarantines_when_title_does_not_match_query(tmp_path):
    """alio.go.kr의 q 파라미터는 느슨한 텍스트 검색이라 무관한 문서가 섞일 수
    있다 — TITLE이 self.query를 포함하지 않으면 quarantine(예외)돼야 한다
    (plan-eng-review outside voice 지적, REPORT_FORM_NA는 section=attach
    행에서 항상 null이라 TITLE로 검증)."""
    adapter = AlioAdapter(
        query="개별 비상임이사 활동내용",
        doc_type=DOC_TYPE_DIRECTOR_ACTIVITY,
        files_root=tmp_path,
    )
    raw_item = {
        "_title": "지역산업 종합정보시스템 구축 사업",  # 실제 노이즈 매치 사례(2026-07-15 실사 확인)
        "_agency": "한국지역난방공사",
        "_idate": "2026.07.15",
        "_disclosure_no": "2026070803206250",
        "_submission_no": "2026070710436257",
        "_file_no": "3060531",
    }
    with pytest.raises(ValueError, match="검색어 불일치"):
        adapter.parse_detail(raw_item)


def test_parse_detail_accepts_title_containing_query_as_substring(monkeypatch, tmp_path):
    """기존 감사결과 어댑터의 TITLE도 query와 정확히 일치하지 않고(예:
    "내부·외부 감사결과" vs "감사결과") 부분포함 관계다 — exact match였다면
    이 검증 로직 자체가 기존 5,622건 수집 경로를 전부 quarantine시켰을 것."""
    monkeypatch.setattr(alio, "_fetch_doc_html", lambda disclosure_no: None)
    monkeypatch.setattr(alio, "_fetch_toc_html", lambda disclosure_no: None)

    adapter = AlioAdapter(query="감사결과", files_root=tmp_path)
    # "내부·외부 <b>감사결과</b>" 를 _clean_title로 정리한 값 — query와 정확히
    # 일치하지 않지만 부분포함 관계라 quarantine되면 안 된다.
    raw_item = {**_raw_item(), "_title": "내부·외부 감사결과"}
    detail = adapter.parse_detail(raw_item, download_files=False)  # 예외가 나면 이 줄에서 실패
    assert detail["_title"] == "내부·외부 감사결과"


def test_source_url_stays_unique_across_files_sharing_one_disclosure(monkeypatch, tmp_path):
    """한 공시(DISCLOSURE_NO)에 첨부파일이 여러 개면(실사 확인: 최대 3개) fileNo가
    달라야 dedup_key(source+source_url)가 겹치지 않는다 — 상세페이지 자체는
    DISCLOSURE_NO 단위라 fileNo 없이는 준복분으로 스킵된다(2026-07-09 사용자 지적)."""
    monkeypatch.setattr(alio, "_download_file", lambda d, f, s: (b"pdf-bytes", "file.pdf"))
    monkeypatch.setattr(alio, "_fetch_doc_html", lambda disclosure_no: None)
    monkeypatch.setattr(alio, "_fetch_toc_html", lambda disclosure_no: None)

    adapter = AlioAdapter(files_root=tmp_path)
    raw_a = {**_raw_item(), "_file_no": "105"}
    raw_b = {**_raw_item(), "_file_no": "106"}
    doc_a = adapter.to_schema(adapter.parse_detail(raw_a))
    doc_b = adapter.to_schema(adapter.parse_detail(raw_b))

    assert doc_a.source_url != doc_b.source_url
    assert "fileNo=105" in doc_a.source_url
    assert "fileNo=106" in doc_b.source_url


def test_parse_detail_falls_back_to_search_fields_when_doc_html_missing(monkeypatch, tmp_path):
    """상세 조각을 못 가져와도(404 등) 검색 API 필드만으로 계속 진행해야 한다 —
    전체 건을 격리(quarantine)시키지 않는다(모듈 독스트링 참고)."""
    monkeypatch.setattr(alio, "_download_file", lambda d, f, s: (b"pdf-bytes", "file.pdf"))
    monkeypatch.setattr(alio, "_fetch_doc_html", lambda disclosure_no: None)
    monkeypatch.setattr(alio, "_fetch_toc_html", lambda disclosure_no: None)

    adapter = AlioAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item())
    doc = adapter.to_schema(detail)

    assert doc.title == "내부·외부 감사결과"  # 검색 API 제목 그대로 유지
    assert doc.department is None
    assert doc.production_date == date(2026, 7, 9)  # IDATE로 폴백
    assert doc.table_of_contents is None
    assert doc.body_text is None


def test_download_files_false_skips_network_download_but_still_fetches_detail(monkeypatch, tmp_path):
    def _fail_if_called(disclosure_no: str, file_no: str, submission_no: str):
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(alio, "_download_file", _fail_if_called)
    monkeypatch.setattr(alio, "_fetch_doc_html", lambda disclosure_no: SAMPLE_DOC_HTML)
    monkeypatch.setattr(alio, "_fetch_toc_html", lambda disclosure_no: SAMPLE_TOC_HTML)

    adapter = AlioAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item(), download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert doc.title == "2026년도 종합감사결과(260626)"  # 상세 조회는 다운로드와 독립적으로 여전히 일어남
    assert list(tmp_path.iterdir()) == []


def test_clean_title_strips_bold_tags():
    assert alio._clean_title("내부·외부 <b>감사결과</b>") == "내부·외부 감사결과"
    assert alio._clean_title("<b>감사결과</b> 처분요구서") == "감사결과 처분요구서"
    assert alio._clean_title("감사결과") == "감사결과"


def test_parse_idate():
    assert alio._parse_idate("2026.07.09") == date(2026, 7, 9)
    assert alio._parse_idate(None) is None
    assert alio._parse_idate("") is None
    assert alio._parse_idate("garbage") is None


def test_parse_korean_date():
    assert alio._parse_korean_date("2026년 07월 08일") == date(2026, 7, 8)
    assert alio._parse_korean_date(None) is None
    assert alio._parse_korean_date("") is None
    assert alio._parse_korean_date("garbage") is None


def test_parse_content_disposition_filename_strips_doubled_quotes():
    # 실제 응답 헤더 형태(2026-07-09 실사 확인): 이중 따옴표로 감싸여 있다.
    header = 'attachment; filename=""2026년도 종합감사결과(260626).pdf"";'
    assert (
        alio._parse_content_disposition_filename(header, fallback="x")
        == "2026년도 종합감사결과(260626).pdf"
    )
    assert alio._parse_content_disposition_filename(None, fallback="fallback.pdf") == "fallback.pdf"


def test_doc_upload_base_url_derives_date_from_disclosure_no_prefix():
    # 실사 확인(2026-07-09): 상세 조각 URL의 날짜 폴더는 검색 API의 IDATE가 아니라
    # DISCLOSURE_NO 앞 8자리(YYYYMMDD)와 항상 일치한다(최대 하루 어긋나는 IDATE와 달리).
    assert alio._doc_upload_base_url("2026070403204842") == (
        "https://alio.go.kr/upload/disclosure/2026/07/04/2026070403204842"
    )


def test_parse_doc_detail_extracts_title_department_and_submission_date():
    fields = alio._parse_doc_detail(SAMPLE_DOC_HTML)
    assert fields["title"] == "2026년도 종합감사결과(260626)"
    assert fields["department"] == "감사실 2권역감사부"
    assert fields["submission_date_text"] == "2026년 07월 08일"
    assert fields["reference_date_text"] == "2026년 06월 26일"
    assert "정지웅" in fields["body_text"]


def test_parse_toc_joins_list_items():
    assert alio._parse_toc(SAMPLE_TOC_HTML) == "38-2. 내부·외부 감사결과\n내부·외부감사 결과"


def test_parse_toc_returns_none_when_empty():
    assert alio._parse_toc("<div></div>") is None
