from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import moe
from rd2.adapters.moe import MoeAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_BUDGET_MATERIAL, SOURCE_MOE

# 실제 사이트(moe.go.kr, boardID=344) 원문 HTML 구조를 그대로 축약한 샘플
# (2026-07-13 curl 직접 요청으로 실사 확인). 페이지 안에 무관한 "소속기관별" 테이블이
# 먼저 나오고 그 뒤에 실제 게시판 목록 테이블이 나온다는 구조를 그대로 반영한다 —
# 어댑터가 div[data-type="list"] 테이블만 골라내는지 검증하는 게 핵심.
SAMPLE_LIST_HTML = """
<div data-table data-type="wrap">
  <table>
    <thead><tr><th>구분</th><th>부서</th><th>공개업무</th><th>내용</th><th>바로가기</th></tr></thead>
    <tbody>
      <tr>
        <td rowspan="6">소속기관</td>
        <td>국사편찬위원회</td>
        <td>예결산현황</td>
        <td>예결산현황</td>
        <td><a href="#">바로가기</a></td>
      </tr>
    </tbody>
  </table>
</div>
<div data-table="" data-type="list">
  <table>
    <caption>재정·예산 정보 목록</caption>
    <thead>
      <tr><th>번호</th><th>제목</th><th>담당부서</th><th>등록일</th><th>조회수</th></tr>
    </thead>
    <tbody>
      <tr>
        <td class="no">113</td>
        <td class="title left">
          <a href="#" onclick="javascript:goView('344', '106652', '0', null, 'W', '1', 'N', '');" title="사립학교교직원연금공단 2025년 기금결산보고서 및 2026년 사업운영계획·예산">
            사립학교교직원연금공단 2025년 기금결산보고서 및 2026년 사업운영계획·예산
          </a>
        </td>
        <td>교원양성연수과</td>
        <td>2026-07-07</td>
        <td>13</td>
      <tr>
      <tr>
        <td class="no">61</td>
        <td class="title left">
          <a href="#" onclick="javascript:goView('344', '60268', '0', null, 'C', '8', 'N', '');" title="2015년 7월 수입 현황 자료">
            2015년 7월 수입 현황 자료
          </a>
        </td>
        <td>예산담당관</td>
        <td>2015-08-03</td>
        <td>512</td>
      <tr>
    </tbody>
  </table>
</div>
"""

# boardSeq=106652 상세페이지 — 첨부파일 2개(실제 응답 구조 그대로 축약, 2026-07-13 캡처).
# 담당부서 셀에 전화번호가 <br><small>로 같이 들어있는 구조를 그대로 반영.
SAMPLE_DETAIL_HTML_MULTI_FILE = """
<div class="midd"><div id="txt"><section>
<div data-board="midd">
  <div data-table="" data-type="view" class="board-edit">
    <table>
      <tbody>
        <tr>
          <th scope="row">제목</th>
          <td colspan="5" class="left">사립학교교직원연금공단 2025년 기금결산보고서 및 2026년 사업운영계획·예산</td>
        </tr>
        <tr>
          <th scope="row">등록일</th>
          <td>2026-07-07</td>
          <th scope="row">담당부서</th>
          <td>교원양성연수과
            <br><small>(044-203-6496)</small>
          </td>
          <th scope="row">조회수</th>
          <td>13</td>
        </tr>
        <tr>
          <th scope="row">첨부파일</th>
          <td colspan="5" class="left">
            <ul>
              <li>
                2025년도 기금결산보고서.pdf
                [ 1.2 MB ]
                <a class="btnBorder" href="/boardCnts/fileDown.do?m=041203&amp;s=moe&amp;fileSeq=c719aab89f9ecd61fe5389125009dd20" title="다운로드"><span>다운로드</span></a>
                <a class="btnBorder" href="javascript:fncFilePreView('c719aab89f9ecd61fe5389125009dd20')" title="미리보기"><span>미리보기</span></a>
              </li>
              <li>
                2026년도 사업운영계획 및 예산.pdf
                [ 4.3 MB ]
                <a class="btnBorder" href="/boardCnts/fileDown.do?m=041203&amp;s=moe&amp;fileSeq=26b0e65eafa270d8ab6f74db7c70bcd6" title="다운로드"><span>다운로드</span></a>
              </li>
              <li class="attach-notice">*안정적인 시스템 운영을 위해 20MB 이상의 파일은 미리보기가 제한됩니다.</li>
            </ul>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="refWrap"><div class="left">
    <div data-content class="boardRenewArea">
      사립학교교직원연금공단의 "2025년 기금결산보고서" 및 "2026년 사업운영계획 및 예산"을 붙임과 같이 공개합니다.
    </div>
  </div></div>
</div>
</section></div></div>
"""

# boardSeq=12452(2008년 게시물) 상세페이지 — 실사로 확인된 파일명 결함 재현: 표시
# 파일명 자체가 "/2008/12/01/2008.zip"처럼 경로 형태로 깨져 있다(원문 HTML 그대로,
# 우리 파싱 버그가 아니라 사이트 자체가 그렇게 등록해둔 것).
SAMPLE_DETAIL_HTML_BROKEN_FILENAME = """
<div data-board="midd">
  <div data-table="" data-type="view" class="board-edit">
    <table>
      <tbody>
        <tr><th scope="row">제목</th><td colspan="5" class="left">2008회계년도 교육비특별회계 당초예산 통계자료</td></tr>
        <tr>
          <th scope="row">등록일</th><td>2008-12-01</td>
          <th scope="row">담당부서</th><td>예산담당관</td>
          <th scope="row">조회수</th><td>99</td>
        </tr>
        <tr>
          <th scope="row">첨부파일</th>
          <td colspan="5" class="left">
            <ul>
              <li>
                /2008/12/01/2008.zip
                [ 6.7 MB ]
                <a class="btnBorder" href="/boardCnts/fileDown.do?m=041203&amp;s=moe&amp;fileSeq=3fd3129dabed036635fb29e372f87cf3" title="다운로드"><span>다운로드</span></a>
              </li>
            </ul>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="refWrap"><div class="left">
    <div data-content class="boardRenewArea">2008회계년도 교육비특별회계 당초예산 통계자료입니다.</div>
  </div></div>
</div>
"""

# boardSeq=104006 상세페이지 — 첨부파일 없는 케이스(구조는 확인된 selector 그대로 씀).
SAMPLE_DETAIL_HTML_NO_FILES = """
<div data-board="midd">
  <div data-table="" data-type="view" class="board-edit">
    <table>
      <tbody>
        <tr><th scope="row">제목</th><td colspan="5" class="left">2026년 교육부 지출구조조정 사업 내역</td></tr>
        <tr>
          <th scope="row">등록일</th><td>2026-01-13</td>
          <th scope="row">담당부서</th><td>예산담당관</td>
          <th scope="row">조회수</th><td>358</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="refWrap"><div class="left">
    <div data-content class="boardRenewArea">2026년 교육부 지출구조조정 사업 내역을 공개합니다.</div>
  </div></div>
</div>
"""

# 존재하지 않는/삭제된 boardSeq 접근 시 실제로 받은 응답(원문 그대로, 2026-07-13 캡처).
SAMPLE_DETAIL_HTML_NOT_FOUND = """
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ko" lang="ko">
<head><title>요청하신 페이지를 찾을수 없습니다</title></head>
<body>
  <div class="warning">
    <p class="p_title">요청하신 페이지를 찾을수가 없습니다.</p>
    <p><strong>죄송합니다. 유효하지 않은 요청입니다.</strong></p>
  </div>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle)은 테스트에서 필요 없다."""
    monkeypatch.setattr(moe, "_throttle", lambda: None)


def test_fetch_list_only_reads_board_list_table_not_affiliated_org_table(monkeypatch):
    """핵심 함정 회귀 테스트: 목록 페이지엔 무관한 "소속기관별" 테이블이 먼저 나온다 —
    div[data-type="list"] 테이블만 읽어야지 첫 번째 table을 무작정 스캔하면 안 된다."""
    monkeypatch.setattr(moe, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = MoeAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_board_seq"] == "106652"
    assert items[0]["_title"] == "사립학교교직원연금공단 2025년 기금결산보고서 및 2026년 사업운영계획·예산"


def test_fetch_list_extracts_status_yn_alongside_board_seq(monkeypatch):
    """핵심 함정 회귀 테스트: statusYN이 'W'/'C'로 섞여 있고, 목록에서 파싱한 값을
    그대로 보존해야 상세 URL이 올바르게 구성된다(하드코딩하면 안 됨)."""
    monkeypatch.setattr(moe, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = MoeAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert items[0]["_status_yn"] == "W"
    assert items[1]["_board_seq"] == "60268"
    assert items[1]["_status_yn"] == "C"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(moe, "_fetch_list_html", _fake_list)
    adapter = MoeAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_board_seq"] == "60268"
    assert calls == [1]


def test_parse_detail_and_to_schema_multi_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        moe, "_fetch_detail_html", lambda board_seq, status_yn: SAMPLE_DETAIL_HTML_MULTI_FILE
    )
    downloaded: list[tuple[str, Path]] = []

    def _fake_download(url: str, dest: Path) -> None:
        downloaded.append((url, dest))
        dest.write_bytes(f"content-of-{url}".encode())

    monkeypatch.setattr(moe, "_download_file_streaming", _fake_download)

    adapter = MoeAdapter(files_root=tmp_path)
    raw = {
        "_board_seq": "106652",
        "_status_yn": "W",
        "_title": "사립학교교직원연금공단 2025년 기금결산보고서 및 2026년 사업운영계획·예산",
        "_detail_url": "https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=344&boardSeq=106652",
        "_department_list": "교원양성연수과",
        "_list_date": "2026-07-07",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "교육부"
    assert doc.department == "교원양성연수과"
    assert doc.production_date == date(2026, 7, 7)
    assert doc.doc_type == DOC_TYPE_BUDGET_MATERIAL
    assert doc.subject_category is None
    assert len(downloaded) == 2
    # pick_primary_file(제목 유사도 기반, file_select.py)이 실제로 고르는 파일 —
    # 두 파일 다 제목과 관련 있어 대표 선정이 결정적으로 갈리진 않지만, 공용
    # 유틸리티의 실측 동작을 그대로 회귀 테스트로 고정한다.
    assert doc.body_file_path == str(
        Path(SOURCE_MOE) / DOC_TYPE_BUDGET_MATERIAL / "106652_2026년도 사업운영계획 및 예산.pdf"
    )
    assert doc.other_file_paths == [
        str(Path(SOURCE_MOE) / DOC_TYPE_BUDGET_MATERIAL / "106652_2025년도 기금결산보고서.pdf")
    ]
    # 담당부서 셀에 같이 있던 전화번호는 department/body_text 어디에도 남으면 안 된다.
    assert "044-203-6496" not in (doc.department or "")
    assert "044-203-6496" not in (doc.body_text or "")


def test_parse_detail_recovers_filename_from_broken_path_like_name(monkeypatch, tmp_path):
    """실사로 확인한 오래된 게시물의 결함(2008년, boardSeq=12452): 표시 파일명 자체가
    "/2008/12/01/2008.zip"처럼 경로 형태로 깨져 있다 — Path(...).name으로 정규화해
    "2008.zip"을 취해야 한다."""
    monkeypatch.setattr(
        moe, "_fetch_detail_html", lambda board_seq, status_yn: SAMPLE_DETAIL_HTML_BROKEN_FILENAME
    )
    monkeypatch.setattr(moe, "_download_file_streaming", lambda url, dest: dest.write_bytes(b"x"))

    adapter = MoeAdapter(files_root=tmp_path)
    raw = {
        "_board_seq": "12452",
        "_status_yn": "W",
        "_title": "2008회계년도 교육비특별회계 당초예산 통계자료",
        "_detail_url": "https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=344&boardSeq=12452",
        "_department_list": "예산담당관",
        "_list_date": "2008-12-01",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path == str(Path(SOURCE_MOE) / DOC_TYPE_BUDGET_MATERIAL / "12452_2008.zip")


def test_parse_detail_without_attachments_has_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        moe, "_fetch_detail_html", lambda board_seq, status_yn: SAMPLE_DETAIL_HTML_NO_FILES
    )

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("첨부파일이 없는데 다운로드가 호출됨")

    monkeypatch.setattr(moe, "_download_file_streaming", _fail_if_called)

    adapter = MoeAdapter(files_root=tmp_path)
    raw = {
        "_board_seq": "104006",
        "_status_yn": "W",
        "_title": "2026년 교육부 지출구조조정 사업 내역",
        "_detail_url": "https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=344&boardSeq=104006",
        "_department_list": "예산담당관",
        "_list_date": "2026-01-13",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert doc.department == "예산담당관"


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(
        moe, "_fetch_detail_html", lambda board_seq, status_yn: SAMPLE_DETAIL_HTML_MULTI_FILE
    )

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(moe, "_download_file_streaming", _fail_if_called)

    adapter = MoeAdapter(files_root=tmp_path)
    raw = {
        "_board_seq": "106652",
        "_status_yn": "W",
        "_title": "사립학교교직원연금공단 2025년 기금결산보고서 및 2026년 사업운영계획·예산",
        "_detail_url": "https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=344&boardSeq=106652",
        "_department_list": "교원양성연수과",
        "_list_date": "2026-07-07",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert list(tmp_path.iterdir()) == []


def test_parse_detail_raises_on_page_not_found_response(monkeypatch):
    """실사로 확인된 edge case(삭제/만료된 boardSeq 접근 시 "요청하신 페이지를
    찾을수 없습니다" 응답)는 조용히 빈 문서를 만들지 않고 예외를 던져 호출부가
    quarantine 처리하게 해야 한다 — molit과 동일한 원칙."""
    monkeypatch.setattr(
        moe, "_fetch_detail_html", lambda board_seq, status_yn: SAMPLE_DETAIL_HTML_NOT_FOUND
    )
    adapter = MoeAdapter()
    raw = {
        "_board_seq": "999999999",
        "_status_yn": "W",
        "_title": "존재하지 않는 게시물",
        "_detail_url": "https://www.moe.go.kr/boardCnts/viewRenew.do?boardID=344&boardSeq=999999999",
        "_department_list": None,
        "_list_date": None,
    }
    with pytest.raises(ValueError, match="상세페이지 파싱 실패"):
        adapter.parse_detail(raw)
