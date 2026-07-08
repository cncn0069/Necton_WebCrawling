from datetime import date
from pathlib import Path

from rd2.adapters import mohw
from rd2.adapters.mohw import MohwAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus

# 실제 사이트(mohw.go.kr, bid=0025) 원문 HTML 구조를 그대로 축약한 샘플
# (2026-07-08 httpx 직접 요청으로 실사 확인 — tbody/tr[data-label] 구조).
SAMPLE_LIST_HTML = """
<table>
<tbody>
<tr>
  <td class="m_hidden" data-label="번호">1420</td>
  <td class="txt_left" data-label="제목">
    <a href="/board.es?mid=a10502000000&amp;bid=0025&amp;act=view&amp;list_no=1485628&amp;tag=&amp;nPage=1" class="txt_title">전자바우처 통합카드사업자 모집 공고</a>
  </td>
  <td data-label="등록일">2025-04-29</td>
  <td data-label="첨부파일"><img alt="4 첨부파일"></td>
  <td class="m_hidden" data-label="조회수">12869</td>
</tr>
<tr>
  <td class="m_hidden" data-label="번호">1418</td>
  <td class="txt_left" data-label="제목">
    <a href="/board.es?mid=a10502000000&amp;bid=0025&amp;act=view&amp;list_no=1480268&amp;tag=&amp;nPage=1" class="txt_title">2024년 장애인거주시설 인권실태조사 수행기관 공모</a>
  </td>
  <td data-label="등록일">2024-02-14</td>
  <td data-label="첨부파일"><img alt="2 첨부파일"></td>
  <td class="m_hidden" data-label="조회수">40204</td>
</tr>
</tbody>
</table>
"""

SAMPLE_DETAIL_HTML = """
<article class="board_view">
  <h2 class="title">전자바우처 통합카드사업자 모집 공고</h2>
  <ul class="info">
    <li class="date"><strong>작성일</strong><span>2025-04-29 09:31</span></li>
    <li class="hit"><strong>조회수</strong><span>12,870</span></li>
    <li class="date"><strong>담당자</strong><span>유지은</span></li>
    <li class="date"><strong>담당부서</strong><span>차세대사회서비스정보시스템구축추진단</span></li>
    <li class="period"><strong>제안서제출기간</strong><span>2025-04-29 ~ 2025-06-09</span></li>
    <li><strong>입찰공고번호</strong><span>제2025-344호</span></li>
  </ul>
  <div class="contents">
    <p>전자바우처 통합카드사업자 모집을 위해 붙임과 같이 공고합니다.</p>
    <p>2025년 4월 29일 보건복지부장관</p>
  </div>
  <div class="file">
    <strong class="title">첨부파일</strong>
    <ul class="list">
      <li>
        입찰공고서(전자바우처 통합카드사업).hwpx
        <span class="link">
          <a class="btn_line" href="/boardDownload.es?bid=0025&amp;list_no=1485628&amp;seq=1" title="입찰공고서(전자바우처 통합카드사업).hwpx">다운로드</a>
        </span>
      </li>
      <li>
        전자바우처 통합카드사업자 모집 공고.pdf
        <span class="link">
          <a class="btn_line" href="/boardDownload.es?bid=0025&amp;list_no=1485628&amp;seq=2" title="전자바우처 통합카드사업자 모집 공고.pdf">다운로드</a>
        </span>
      </li>
    </ul>
  </div>
</article>
"""

SAMPLE_DETAIL_HTML_NO_FILES = """
<article class="board_view">
  <h2 class="title">2024년 장애인거주시설 인권실태조사 수행기관 공모</h2>
  <ul class="info">
    <li class="date"><strong>작성일</strong><span>2024-02-14 09:28</span></li>
    <li class="date"><strong>담당부서</strong><span>장애인권익지원과</span></li>
    <li class="period"><strong>제안서제출기간</strong><span>2024-02-14 ~ 2024-02-29</span></li>
  </ul>
  <div class="contents">
    <p>위탁기관을 공모합니다.</p>
  </div>
</article>
"""


def test_fetch_list_extracts_list_no_from_static_links(monkeypatch):
    monkeypatch.setattr(mohw, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = MohwAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_list_no"] == "1485628"
    assert items[0]["_title"] == "전자바우처 통합카드사업자 모집 공고"
    assert items[1]["_list_no"] == "1480268"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    """skip된 행은 목록 파싱 단계에서만 건너뛰고, 상세 요청(비용이 드는 네트워크
    호출)은 fetch_list 단계에서 아예 발생하지 않아야 한다 — PRISM의 skip 설계와
    같은 원칙(목록 페이지 조회 자체는 skip과 무관하게 필요하므로 호출 자체는
    허용하되, 몇 번 호출됐는지만 확인)."""
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(mohw, "_fetch_list_html", _fake_list)
    adapter = MohwAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_list_no"] == "1480268"
    assert calls == [1]  # 페이지 재조회 없이 1페이지 안에서 skip이 처리됨


def test_parse_detail_and_to_schema_maps_open_track(monkeypatch, tmp_path):
    monkeypatch.setattr(mohw, "_fetch_detail_html", lambda url: SAMPLE_DETAIL_HTML)
    downloaded_urls: list[str] = []

    def _fake_download(url: str) -> bytes:
        downloaded_urls.append(url)
        return f"content-of-{url}".encode()

    monkeypatch.setattr(mohw, "_download_file_bytes", _fake_download)

    adapter = MohwAdapter(files_root=tmp_path)
    raw = {
        "_list_no": "1485628",
        "_title": "전자바우처 통합카드사업자 모집 공고",
        "_detail_url": "https://www.mohw.go.kr/board.es?mid=a10502000000&bid=0025&act=view&list_no=1485628",
        "_list_date": "2025-04-29",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "보건복지부"
    assert doc.department == "차세대사회서비스정보시스템구축추진단"
    assert doc.production_date == date(2025, 4, 29)
    assert doc.start_date == date(2025, 4, 29)
    assert doc.end_date == date(2025, 6, 9)
    assert doc.doc_type == "입찰공고"
    assert doc.non_disclosure_reason is None  # OPEN이라 검증 통과해야 함
    assert len(downloaded_urls) == 2
    # 제목과 가장 비슷한 파일명("모집 공고.pdf")이 대표 파일로 선정돼야 한다.
    assert doc.body_file_path == str(
        Path("보건복지부") / "입찰공고" / "1485628_전자바우처 통합카드사업자 모집 공고.pdf"
    )
    assert doc.other_file_paths == [
        str(Path("보건복지부") / "입찰공고" / "1485628_입찰공고서(전자바우처 통합카드사업).hwpx")
    ]


def test_parse_detail_without_attachments_has_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(mohw, "_fetch_detail_html", lambda url: SAMPLE_DETAIL_HTML_NO_FILES)

    def _fail_if_called(url: str) -> bytes:
        raise AssertionError("첨부파일이 없는데 다운로드가 호출됨")

    monkeypatch.setattr(mohw, "_download_file_bytes", _fail_if_called)

    adapter = MohwAdapter(files_root=tmp_path)
    raw = {
        "_list_no": "1480268",
        "_title": "2024년 장애인거주시설 인권실태조사 수행기관 공모",
        "_detail_url": "https://www.mohw.go.kr/board.es?mid=a10502000000&bid=0025&act=view&list_no=1480268",
        "_list_date": "2024-02-14",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert doc.doc_type == "공모"
    assert doc.department == "장애인권익지원과"


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(mohw, "_fetch_detail_html", lambda url: SAMPLE_DETAIL_HTML)

    def _fail_if_called(url: str) -> bytes:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(mohw, "_download_file_bytes", _fail_if_called)

    adapter = MohwAdapter(files_root=tmp_path)
    raw = {
        "_list_no": "1485628",
        "_title": "전자바우처 통합카드사업자 모집 공고",
        "_detail_url": "https://www.mohw.go.kr/board.es?mid=a10502000000&bid=0025&act=view&list_no=1485628",
        "_list_date": "2025-04-29",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert list(tmp_path.iterdir()) == []


def test_infer_doc_type_priority():
    assert mohw._infer_doc_type("[사전규격공개] 전자바우처 통합카드사업") == "사전규격공개"
    assert mohw._infer_doc_type("지자체 사회보장사업 실태조사 입찰 재공고") == "입찰재공고"
    assert mohw._infer_doc_type("2024년 장애인거주시설 인권실태조사 수행기관 공모") == "공모"
    assert mohw._infer_doc_type("전자바우처 통합카드사업자 모집 공고") == "입찰공고"
    assert mohw._infer_doc_type("2021년도 취학 전 아동 실명예방 사업 민간경상보조사업") == "공고"
