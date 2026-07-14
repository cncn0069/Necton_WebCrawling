from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import moel_policy
from rd2.adapters.moel_policy import MoelPolicyAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import (
    DOC_TYPE_GUIDE,
    DOC_TYPE_INTERPRETATION_COMPILATION,
    DOC_TYPE_NOTICE,
    DOC_TYPE_STATUS_REPORT,
    SOURCE_MOEL_POLICY,
)

# 실제 사이트(moel.go.kr/policy/policydata/list.do) 원문 HTML 구조를 그대로
# 축약한 샘플(2026-07-14 curl 직접 요청으로 실사 확인). moel.py(훈령·예규·고시)와
# 컬럼 순서가 다르다는 게 핵심 함정 — 이 게시판엔 "행정규칙번호" 컬럼이 없어
# 번호(0)/제목(1)/담당부서(2)/등록일(3)이다(moel.py는 제목이 2번 인덱스).
SAMPLE_LIST_HTML = """
<table>
  <thead><tr><th>번호</th><th>제목</th><th>담당부서</th><th>등록일</th><th>첨부</th><th>조회</th></tr></thead>
  <tbody>
    <tr>
      <td class="m_hidden">4352</td>
      <td class="txt_left"><strong class="b_tit"><a href="view.do?bbs_seq=20260700446" onclick="scEventListener.fnView('20260700446');return false;" title="26년 소규모 사업장을 위한 7가지 노른자 노동법">26년 소규모 사업장을 위한 7가지 노른자 노동법</a></strong></td>
      <td><span class="ellipsis">근로감독협력과</span></td>
      <td>2026.07.13</td>
      <td><i class="ri-attachment-2"><span class="sr_only">첨부파일 있음</span></i></td>
      <td class="txt_right">581</td>
    </tr>
    <tr>
      <td class="m_hidden">4351</td>
      <td class="txt_left"><strong class="b_tit"><a href="view.do?bbs_seq=20260700361" onclick="scEventListener.fnView('20260700361');return false;" title="고용노동부 정부위원회 현황 및 활동내역('26년 2분기)">고용노동부 정부위원회 현황 및 활동내역('26년 2분기)</a></strong></td>
      <td><span class="ellipsis">혁신행정담당관</span></td>
      <td>2026.07.10</td>
      <td></td>
      <td class="txt_right">203</td>
    </tr>
  </tbody>
</table>
"""

SAMPLE_LIST_HTML_EMPTY = "<table><tbody></tbody></table>"

# bbs_seq=20260700329 상세페이지 — 질의회시집(첨부파일 1개, 실제 응답 구조 그대로
# 축약, 2026-07-14 캡처).
SAMPLE_DETAIL_HTML_WITH_FILE = """
<div class="board_view_wrap">
  <div class="b_info">
    <dl class="w100p"><dt>제목</dt><dd>중대재해처벌법 중대산업재해 질의회시집(2026.6.) 배포</dd></dl>
    <dl><dt>등록일</dt><dd>2026-07-08&nbsp;</dd></dl>
    <dl><dt>담당부서</dt><dd>중대산업재해수사과&nbsp;</dd></dl>
    <dl><dt>담당자</dt><dd>이환준&nbsp;</dd></dl>
    <dl><dt>전화번호</dt><dd>044-202-8955&nbsp;</dd></dl>
    <div class="b_content">
      2021년 1월부터 2025년 12월까지의 기간 동안 질의회시한 내용을 바탕으로 일부 문구를 수정, 보완하여 발간하였습니다.
    </div>
    <div class="file">
      <strong class="title">첨부</strong>
      <ul class="list">
        <li>
          <img src="/images/common/pdf.png" alt="pdf 첨부파일">
          <a href="/common/downloadFile.do?file_seq=20260700505&amp;bbs_seq=20260700329&amp;bbs_id=29&amp;file_ext=pdf" title="중대재해처벌법 중대산업재해 질의회시집(26년6월)_배포.pdf 다운로드">중대재해처벌법 중대산업재해 질의회시집(26년6월)_배포.pdf</a>
          <span class="link">
            <a class="btn_line" href="/common/downloadFile.do?file_seq=20260700505&amp;bbs_seq=20260700329&amp;bbs_id=29&amp;file_ext=pdf" title="다운로드">다운로드</a>
            <a class="btn_line attachPreview" href="javascript:void(0);" onclick="gfnPreView('/common/filePreview.do?file_seq=20260700505&amp;bbs_seq=20260700329&amp;bbs_id=29');">바로보기</a>
          </span>
        </li>
      </ul>
    </div>
  </div>
</div>
"""

# bbs_seq=20260700361 상세페이지 — 현황류, 첨부파일 없는 케이스.
SAMPLE_DETAIL_HTML_STATUS_NO_FILES = """
<div class="board_view_wrap">
  <div class="b_info">
    <dl class="w100p"><dt>제목</dt><dd>고용노동부 정부위원회 현황 및 활동내역('26년 2분기)</dd></dl>
    <dl><dt>등록일</dt><dd>2026-07-10&nbsp;</dd></dl>
    <dl><dt>담당부서</dt><dd>혁신행정담당관&nbsp;</dd></dl>
    <div class="b_content">2026년 2분기 기준 고용노동부 소관 정부위원회 현황 및 활동내역을 공개합니다.</div>
  </div>
</div>
"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle)은 테스트에서 필요 없다."""
    monkeypatch.setattr(moel_policy, "_throttle", lambda: None)


def test_infer_doc_type_priority():
    """실사(2026-07-14, 5페이지 50건 샘플)로 확인한 실제 제목들로 우선순위를
    검증한다 — mohw.py의 키워드 우선순위 검증 방식과 동일."""
    assert (
        moel_policy._infer_doc_type("중대재해처벌법 중대산업재해 질의회시집(2026.6.) 배포")
        == DOC_TYPE_INTERPRETATION_COMPILATION
    )
    assert moel_policy._infer_doc_type("2026년 사업장 보건관리 업무매뉴얼") == DOC_TYPE_GUIDE
    assert moel_policy._infer_doc_type("2026년 해빙기 건설현장 안전보건길잡이") == DOC_TYPE_GUIDE
    assert moel_policy._infer_doc_type("2026년 국민취업지원제도 참여자 수첩") == DOC_TYPE_GUIDE
    assert (
        moel_policy._infer_doc_type("고용노동부 정부위원회 현황 및 활동내역('26년 2분기)")
        == DOC_TYPE_STATUS_REPORT
    )
    assert moel_policy._infer_doc_type("2025년도 임금채권보장기금 결산보고서") == DOC_TYPE_STATUS_REPORT
    assert moel_policy._infer_doc_type("2026년 표준 취업규칙 게시") == DOC_TYPE_NOTICE
    assert moel_policy._infer_doc_type("'26년 6월 중대재해사이렌(오픈채팅방) 자료 공개") == DOC_TYPE_NOTICE


def test_fetch_list_extracts_bbs_seq_with_policydata_column_order(monkeypatch):
    """핵심 함정 회귀 테스트: 이 게시판은 moel.py(훈령·예규·고시)와 컬럼 순서가
    다르다(행정규칙번호 컬럼이 없음) — 제목 컬럼 인덱스를 그대로 재사용하면 안 된다."""
    monkeypatch.setattr(moel_policy, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = MoelPolicyAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_bbs_seq"] == "20260700446"
    assert items[0]["_title"] == "26년 소규모 사업장을 위한 7가지 노른자 노동법"
    assert items[0]["_department_list"] == "근로감독협력과"
    assert items[0]["_date_text"] == "2026.07.13"
    assert items[1]["_bbs_seq"] == "20260700361"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(moel_policy, "_fetch_list_html", _fake_list)
    adapter = MoelPolicyAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_bbs_seq"] == "20260700361"
    assert calls == [1]


def test_fetch_list_stops_on_empty_page(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML if page == 1 else SAMPLE_LIST_HTML_EMPTY

    monkeypatch.setattr(moel_policy, "_fetch_list_html", _fake_list)
    adapter = MoelPolicyAdapter()
    items = list(adapter.fetch_list())
    assert len(items) == 2
    assert calls == [1, 2]


def test_parse_detail_and_to_schema_with_file_excludes_contact_info(monkeypatch, tmp_path):
    """핵심 함정 회귀 테스트: 담당자 실명/전화번호가 department/body_text 어디에도
    남으면 안 된다(moel.py와 동일한 개인정보 제외 원칙). doc_type은 질의회시집으로
    추론돼야 하고, 저장 경로도 그 doc_type 폴더를 써야 한다."""
    monkeypatch.setattr(moel_policy, "_fetch_detail_html", lambda bbs_seq: SAMPLE_DETAIL_HTML_WITH_FILE)
    downloaded: list[tuple[str, Path]] = []

    def _fake_download(url: str, dest: Path) -> None:
        downloaded.append((url, dest))
        dest.write_bytes(f"content-of-{url}".encode())

    monkeypatch.setattr(moel_policy, "_download_file_streaming", _fake_download)

    adapter = MoelPolicyAdapter(files_root=tmp_path)
    raw = {
        "_bbs_seq": "20260700329",
        "_title": "중대재해처벌법 중대산업재해 질의회시집(2026.6.) 배포",
        "_department_list": "중대산업재해수사과",
        "_date_text": "2026.07.08",
        "_detail_url": "https://www.moel.go.kr/policy/policydata/view.do?bbs_seq=20260700329",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "고용노동부"
    assert doc.department == "중대산업재해수사과"
    assert doc.production_date == date(2026, 7, 8)
    assert doc.doc_type == DOC_TYPE_INTERPRETATION_COMPILATION
    assert "질의회시한 내용" in (doc.body_text or "")
    assert "044-202-8955" not in (doc.department or "")
    assert "044-202-8955" not in (doc.body_text or "")
    assert "이환준" not in (doc.body_text or "")
    assert len(downloaded) == 1
    assert doc.body_file_path == str(
        Path(SOURCE_MOEL_POLICY)
        / DOC_TYPE_INTERPRETATION_COMPILATION
        / "20260700329_중대재해처벌법 중대산업재해 질의회시집(26년6월)_배포.pdf"
    )
    assert doc.other_file_paths == []


def test_parse_detail_without_attachments_infers_status_report_type(monkeypatch, tmp_path):
    monkeypatch.setattr(
        moel_policy, "_fetch_detail_html", lambda bbs_seq: SAMPLE_DETAIL_HTML_STATUS_NO_FILES
    )

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("첨부파일이 없는데 다운로드가 호출됨")

    monkeypatch.setattr(moel_policy, "_download_file_streaming", _fail_if_called)

    adapter = MoelPolicyAdapter(files_root=tmp_path)
    raw = {
        "_bbs_seq": "20260700361",
        "_title": "고용노동부 정부위원회 현황 및 활동내역('26년 2분기)",
        "_department_list": "혁신행정담당관",
        "_date_text": "2026.07.10",
        "_detail_url": "https://www.moel.go.kr/policy/policydata/view.do?bbs_seq=20260700361",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.doc_type == DOC_TYPE_STATUS_REPORT
    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert doc.department == "혁신행정담당관"


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(moel_policy, "_fetch_detail_html", lambda bbs_seq: SAMPLE_DETAIL_HTML_WITH_FILE)

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(moel_policy, "_download_file_streaming", _fail_if_called)

    adapter = MoelPolicyAdapter(files_root=tmp_path)
    raw = {
        "_bbs_seq": "20260700329",
        "_title": "중대재해처벌법 중대산업재해 질의회시집(2026.6.) 배포",
        "_department_list": "중대산업재해수사과",
        "_date_text": "2026.07.08",
        "_detail_url": "https://www.moel.go.kr/policy/policydata/view.do?bbs_seq=20260700329",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert list(tmp_path.iterdir()) == []
