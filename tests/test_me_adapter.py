from datetime import date

import pytest

from rd2.adapters import me
from rd2.adapters.me import MeAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_DIRECTIVE, DOC_TYPE_NOTIFICATION, DOC_TYPE_REGULATION

# 실제 사이트(me.go.kr/home/web/law/list.do, menuId=71) 원문 HTML 구조를 그대로
# 축약한 샘플(2026-07-14 curl 직접 요청으로 실사 확인). 목록엔 순번/제목/발령일자/
# 발령번호/소관부서명 5개 컬럼만 있고 문서유형(고시/훈령/예규) 구분이 없다는 게
# 핵심 — 그래서 doc_type은 반드시 상세페이지에서 채워야 한다.
SAMPLE_LIST_HTML = """
<table>
  <thead><tr><th>순번</th><th>행정규칙명</th><th>발령일자</th><th>발령번호</th><th>소관부서명</th></tr></thead>
  <tbody>
    <tr style="text-align: center;">
      <td>1907</td>
      <td class="al"><a href="/home/web/law/read.do;jsessionid=abc?pagerOffset=0&maxPageItems=10&menuId=71&condition.typeCode=admrul&typeCode=admrul&lawSeq=1901" title="제작자동차 인증 및 검사 방법과 절차 등에 관한 규정">제작자동차 인증 및 검사 방법과 절차 등에 관한 규정</a></td>
      <td>2026-06-30</td>
      <td>2026-160</td>
      <td>기후에너지환경부</td>
    </tr>
    <tr style="text-align: center;">
      <td>1906</td>
      <td class="al"><a href="/home/web/law/read.do;jsessionid=abc?pagerOffset=0&maxPageItems=10&menuId=71&condition.typeCode=admrul&typeCode=admrul&lawSeq=1902" title="2026년도 회수의무량 산출에 필요한 전년도의 총 매입량 및 인구수">2026년도 회수의무량 산출에 필요한 전년도의 총 매입량 및 인구수</a></td>
      <td>2026-06-28</td>
      <td>2026-159</td>
      <td>기후에너지환경부</td>
    </tr>
  </tbody>
</table>
"""

SAMPLE_LIST_HTML_EMPTY = "<table><tbody></tbody></table>"

# lawSeq=1901 상세페이지 — 고시 유형(실제 응답 구조 그대로 축약, 2026-07-14 캡처).
SAMPLE_DETAIL_HTML_NOTIFICATION = """
<div id="content_body">
  <div class="view_info01_1"><ul><li><dl><dt>행정규칙일련번호</dt><dd>2100000281832</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>행정규칙명</dt><dd>제작자동차 인증 및 검사 방법과 절차 등에 관한 규정</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>행정규칙종류</dt><dd>고시</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>발령일자</dt><dd>20260630</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>발령번호</dt><dd>2026-160</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>소관부서명</dt><dd>기후에너지환경부</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>현행연혁구분</dt><dd>현행</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>제개정구분명</dt><dd>일부개정</dd></dl></li></ul></div>
</div>
"""

# 훈령 유형 샘플(같은 구조, 종류만 다름).
SAMPLE_DETAIL_HTML_DIRECTIVE = """
<div id="content_body">
  <div class="view_info02_1"><ul><li><dl><dt>행정규칙명</dt><dd>환경부 훈령 예시</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>행정규칙종류</dt><dd>훈령</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>소관부서명</dt><dd>기후에너지환경부</dd></dl></li></ul></div>
</div>
"""

# 예규 유형 샘플.
SAMPLE_DETAIL_HTML_REGULATION = """
<div id="content_body">
  <div class="view_info02_1"><ul><li><dl><dt>행정규칙명</dt><dd>환경부 예규 예시</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>행정규칙종류</dt><dd>예규</dd></dl></li></ul></div>
  <div class="view_info02_1"><ul><li><dl><dt>소관부서명</dt><dd>기후에너지환경부</dd></dl></li></ul></div>
</div>
"""

# 존재하지 않는/삭제된 lawSeq 접근 시 메타데이터 없는 응답(가정 — 다른 어댑터와
# 동일한 방어 패턴 검증용).
SAMPLE_DETAIL_HTML_NOT_FOUND = "<div id=\"content_body\"><p>데이터가 없습니다.</p></div>"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle)은 테스트에서 필요 없다."""
    monkeypatch.setattr(me, "_throttle", lambda: None)


def test_fetch_list_extracts_law_seq_and_columns(monkeypatch):
    monkeypatch.setattr(me, "_fetch_list_html", lambda offset: SAMPLE_LIST_HTML)
    adapter = MeAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_law_seq"] == "1901"
    assert items[0]["_title"] == "제작자동차 인증 및 검사 방법과 절차 등에 관한 규정"
    assert items[0]["_date_text"] == "2026-06-30"
    assert items[0]["_rule_number"] == "2026-160"
    assert items[0]["_department"] == "기후에너지환경부"
    assert items[1]["_law_seq"] == "1902"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_list(offset: int) -> str:
        calls.append(offset)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(me, "_fetch_list_html", _fake_list)
    adapter = MeAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_law_seq"] == "1902"
    assert calls == [0]


def test_fetch_list_stops_on_empty_page(monkeypatch):
    calls: list[int] = []

    def _fake_list(offset: int) -> str:
        calls.append(offset)
        return SAMPLE_LIST_HTML if offset == 0 else SAMPLE_LIST_HTML_EMPTY

    monkeypatch.setattr(me, "_fetch_list_html", _fake_list)
    adapter = MeAdapter()
    items = list(adapter.fetch_list())
    assert len(items) == 2
    assert calls == [0, 10]


def test_parse_detail_and_to_schema_maps_notification_type(monkeypatch):
    """핵심 함정 회귀 테스트: 목록엔 문서유형이 없어 상세페이지의 "행정규칙종류"
    필드로 doc_type을 결정해야 한다 — 고시는 DOC_TYPE_NOTIFICATION."""
    monkeypatch.setattr(me, "_fetch_detail_html", lambda law_seq: SAMPLE_DETAIL_HTML_NOTIFICATION)
    adapter = MeAdapter()
    raw = {
        "_law_seq": "1901",
        "_title": "제작자동차 인증 및 검사 방법과 절차 등에 관한 규정",
        "_date_text": "2026-06-30",
        "_rule_number": "2026-160",
        "_department": "기후에너지환경부",
        "_detail_url": "https://me.go.kr/home/web/law/read.do?menuId=71&lawSeq=1901",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "기후에너지환경부"
    assert doc.department is None
    assert doc.production_date == date(2026, 6, 30)
    assert doc.doc_type == DOC_TYPE_NOTIFICATION
    assert doc.body_text is None
    assert doc.body_file_path is None


def test_parse_detail_and_to_schema_maps_directive_type(monkeypatch):
    monkeypatch.setattr(me, "_fetch_detail_html", lambda law_seq: SAMPLE_DETAIL_HTML_DIRECTIVE)
    adapter = MeAdapter()
    raw = {
        "_law_seq": "2001",
        "_title": "환경부 훈령 예시",
        "_date_text": "2026-05-01",
        "_rule_number": "2026-1",
        "_department": "기후에너지환경부",
        "_detail_url": "https://me.go.kr/home/web/law/read.do?menuId=71&lawSeq=2001",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.doc_type == DOC_TYPE_DIRECTIVE


def test_parse_detail_and_to_schema_maps_regulation_type(monkeypatch):
    monkeypatch.setattr(me, "_fetch_detail_html", lambda law_seq: SAMPLE_DETAIL_HTML_REGULATION)
    adapter = MeAdapter()
    raw = {
        "_law_seq": "3001",
        "_title": "환경부 예규 예시",
        "_date_text": "2026-04-01",
        "_rule_number": "2026-2",
        "_department": "기후에너지환경부",
        "_detail_url": "https://me.go.kr/home/web/law/read.do?menuId=71&lawSeq=3001",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.doc_type == DOC_TYPE_REGULATION


def test_parse_detail_raises_when_metadata_missing(monkeypatch):
    monkeypatch.setattr(me, "_fetch_detail_html", lambda law_seq: SAMPLE_DETAIL_HTML_NOT_FOUND)
    adapter = MeAdapter()
    raw = {
        "_law_seq": "999999",
        "_title": "존재하지 않는 행정규칙",
        "_date_text": None,
        "_rule_number": None,
        "_department": None,
        "_detail_url": "https://me.go.kr/home/web/law/read.do?menuId=71&lawSeq=999999",
    }
    with pytest.raises(ValueError, match="상세페이지 파싱 실패"):
        adapter.parse_detail(raw)
