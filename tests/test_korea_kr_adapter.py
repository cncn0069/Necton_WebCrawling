from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import korea_kr
from rd2.adapters.korea_kr import KoreaKrAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_PRESS_RELEASE, SOURCE_KOREA_KR

# 실제 사이트(korea.kr/briefing/pressReleaseList.do) 원문 HTML 구조를 그대로 축약한
# 샘플(2026-07-13 curl 직접 요청으로 실사 확인, User-Agent 필수 — 기본 curl UA는
# WAF에 걸릴 수 있다). div.list_type 앞에 검색 필터 UI가 있고, 그 안에도 무관한
# newsId 링크가 섞여 있을 수 있어 반드시 div.list_type 안쪽만 파싱해야 한다는 걸
# 검증하는 게 핵심.
SAMPLE_LIST_HTML = """
<div class="sch_result">
  <div class="filter_ui">
    <a href="/briefing/pressReleaseView.do?newsId=999999999&pageIndex=1">무관한 추천 링크</a>
  </div>
</div>
<div class="list_type">
  <ul>
    <li>
      <a href="/briefing/pressReleaseView.do?newsId=156770659&amp;pageIndex=1&amp;repCodeType=&amp;repCode=&amp;startDate=2025-07-13&amp;endDate=2026-07-13&srchWord=&amp;period=">
        <span class="text">
          <strong>2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결(7.13.월)</strong>
          <span class="lead">질병관리청(청장 임승관)은 2026-2027절기 코로나19 예방접종사업에 필요한 백신의 조달계약이 체결되었다고 밝혔다.</span>
          <span class="source">
            <span>2026.07.13</span>
            <span>질병관리청</span>
          </span>
        </span>
      </a>
    </li>
    <li>
      <a href="/briefing/pressReleaseView.do?newsId=156770607&amp;pageIndex=1&amp;repCodeType=&amp;repCode=&amp;startDate=2025-07-13&amp;endDate=2026-07-13&srchWord=&amp;period=">
        <span class="text">
          <strong>재난안전관리본부장, 오송 참사 3주기 지하차도 안전 현장에서 살핀다</strong>
          <span class="lead">행정안전부(장관 윤호중) 김광용 재난안전관리본부장은 오송 참사 3주기를 앞둔 7월 13일 추모식 준비상황을 사전점검했다.</span>
          <span class="source">
            <span>2026.07.13</span>
            <span>행정안전부</span>
          </span>
        </span>
      </a>
    </li>
  </ul>
</div>
"""

SAMPLE_LIST_HTML_EMPTY = """
<div class="list_type"><ul></ul></div>
"""

# newsId=156770659 상세페이지 — 첨부파일 2개(PDF+HWPX, 같은 문서의 다른 포맷 —
# 실제 응답 구조 그대로 축약, 2026-07-13 캡처). "바로보기"/"내려받기" 두 링크가
# 같은 fileId를 가리켜 dedup이 필요하다는 걸 검증하는 게 핵심.
SAMPLE_DETAIL_HTML_MULTI_FILE = """
<div class="article_wrap">
  <div class="filedown">
    <dl>
      <dt>첨부파일</dt>
      <dd>
        <p>
          <span><a href="/common/download.do?fileId=198507299&amp;tblKey=GMN">
            <img src="/images/icon/icon_ipdf.gif" alt="PDF파일">2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결.pdf
            </a></span>
          <span>
            <a class="view" href="/common/docViewer.do?fileId=198507299&amp;tblKey=GMN" target="_Blank">바로보기</a>
            <a class="down" href="/common/download.do?fileId=198507299&amp;tblKey=GMN">내려받기</a>
          </span>
        </p>
        <p>
          <span><a href="/common/download.do?fileId=198507298&amp;tblKey=GMN">
            <img src="/images/icon/icon_ihangul.gif" alt="한글파일">2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결.hwpx
            </a></span>
          <span>
            <a class="view" href="/common/docViewer.do?fileId=198507298&amp;tblKey=GMN" target="_Blank">바로보기</a>
            <a class="down" href="/common/download.do?fileId=198507298&amp;tblKey=GMN">내려받기</a>
          </span>
        </p>
      </dd>
    </dl>
  </div>
</div>
"""

# newsId=156770607 상세페이지 — 첨부파일 없는 케이스.
SAMPLE_DETAIL_HTML_NO_FILES = """
<div class="article_wrap"></div>
"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle)은 테스트에서 필요 없다."""
    monkeypatch.setattr(korea_kr, "_throttle", lambda: None)


def test_fetch_list_only_reads_list_type_container_not_filter_ui(monkeypatch):
    """핵심 함정 회귀 테스트: div.list_type 앞에 무관한 검색 필터 UI가 있고 거기도
    newsId 링크가 섞여 있을 수 있다 — div.list_type 안쪽만 읽어야 한다."""
    monkeypatch.setattr(korea_kr, "_fetch_list_html", lambda page, start, end: SAMPLE_LIST_HTML)
    adapter = KoreaKrAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_news_id"] == "156770659"
    assert items[1]["_news_id"] == "156770607"
    assert all(item["_news_id"] != "999999999" for item in items)


def test_fetch_list_extracts_title_body_date_agency_from_list_page(monkeypatch):
    """목록 페이지 안에 본문 전문(span.lead)이 이미 있어 title/body_text/date/agency를
    이 단계에서 전부 채워야 한다 — 상세페이지 재조회가 필요 없다."""
    monkeypatch.setattr(korea_kr, "_fetch_list_html", lambda page, start, end: SAMPLE_LIST_HTML)
    adapter = KoreaKrAdapter()
    items = list(adapter.fetch_list(max_items=1))
    item = items[0]
    assert item["_title"] == "2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결(7.13.월)"
    assert "질병관리청" in item["_body_text"]
    assert item["_date_text"] == "2026.07.13"
    assert item["_agency"] == "질병관리청"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int, start: str, end: str) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(korea_kr, "_fetch_list_html", _fake_list)
    adapter = KoreaKrAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_news_id"] == "156770607"
    assert calls == [1]


def test_fetch_list_stops_on_empty_page(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int, start: str, end: str) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML if page == 1 else SAMPLE_LIST_HTML_EMPTY

    monkeypatch.setattr(korea_kr, "_fetch_list_html", _fake_list)
    adapter = KoreaKrAdapter()
    items = list(adapter.fetch_list())
    assert len(items) == 2
    assert calls == [1, 2]


def test_parse_detail_and_to_schema_multi_file_dedupes_view_and_download_links(monkeypatch, tmp_path):
    """핵심 함정 회귀 테스트: 같은 파일이 "바로보기"/"내려받기" 두 <a>로 중복 등장한다
    — href 기준 dedup 없이 그대로 쓰면 같은 파일을 두 번 다운로드하게 된다."""
    monkeypatch.setattr(korea_kr, "_fetch_detail_html", lambda news_id: SAMPLE_DETAIL_HTML_MULTI_FILE)
    downloaded: list[tuple[str, Path]] = []

    def _fake_download(url: str, dest: Path) -> None:
        downloaded.append((url, dest))
        dest.write_bytes(f"content-of-{url}".encode())

    monkeypatch.setattr(korea_kr, "_download_file_streaming", _fake_download)

    adapter = KoreaKrAdapter(files_root=tmp_path)
    raw = {
        "_news_id": "156770659",
        "_title": "2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결(7.13.월)",
        "_body_text": "질병관리청은 조달계약이 체결되었다고 밝혔다.",
        "_date_text": "2026.07.13",
        "_agency": "질병관리청",
        "_detail_url": "https://www.korea.kr/briefing/pressReleaseView.do?newsId=156770659",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "질병관리청"
    assert doc.department is None
    assert doc.production_date == date(2026, 7, 13)
    assert doc.doc_type == DOC_TYPE_PRESS_RELEASE
    assert doc.body_text == "질병관리청은 조달계약이 체결되었다고 밝혔다."
    # 2개 파일만 실제로 다운로드됐어야 한다(바로보기+내려받기 중복 제거 확인).
    assert len(downloaded) == 2
    assert doc.body_file_path == str(
        Path(SOURCE_KOREA_KR)
        / DOC_TYPE_PRESS_RELEASE
        / "1-500"
        / "156770659_2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결.pdf"
    )
    assert doc.other_file_paths == [
        str(
            Path(SOURCE_KOREA_KR)
            / DOC_TYPE_PRESS_RELEASE
            / "1-500"
            / "156770659_2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결.hwpx"
        )
    ]


def test_parse_detail_without_attachments_has_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(korea_kr, "_fetch_detail_html", lambda news_id: SAMPLE_DETAIL_HTML_NO_FILES)

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("첨부파일이 없는데 다운로드가 호출됨")

    monkeypatch.setattr(korea_kr, "_download_file_streaming", _fail_if_called)

    adapter = KoreaKrAdapter(files_root=tmp_path)
    raw = {
        "_news_id": "156770607",
        "_title": "재난안전관리본부장, 오송 참사 3주기 지하차도 안전 현장에서 살핀다",
        "_body_text": "행정안전부는 오송 참사 3주기를 앞두고 준비상황을 점검했다.",
        "_date_text": "2026.07.13",
        "_agency": "행정안전부",
        "_detail_url": "https://www.korea.kr/briefing/pressReleaseView.do?newsId=156770607",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert doc.ordering_agency == "행정안전부"


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(korea_kr, "_fetch_detail_html", lambda news_id: SAMPLE_DETAIL_HTML_MULTI_FILE)

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(korea_kr, "_download_file_streaming", _fail_if_called)

    adapter = KoreaKrAdapter(files_root=tmp_path)
    raw = {
        "_news_id": "156770659",
        "_title": "2026-2027절기 코로나19 백신 484만 도즈 조달계약 체결(7.13.월)",
        "_body_text": "본문",
        "_date_text": "2026.07.13",
        "_agency": "질병관리청",
        "_detail_url": "https://www.korea.kr/briefing/pressReleaseView.do?newsId=156770659",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert list(tmp_path.iterdir()) == []
