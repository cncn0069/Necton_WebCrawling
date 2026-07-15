from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import moel
from rd2.adapters.moel import MoelAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_DIRECTIVE, DOC_TYPE_NOTIFICATION, SOURCE_MOEL

# 실제 사이트(moel.go.kr/info/lawinfo/instruction/list.do) 원문 HTML 구조를 그대로
# 축약한 샘플(2026-07-14 curl 직접 요청으로 실사 확인).
SAMPLE_LIST_HTML = """
<table>
  <thead><tr><th>번호</th><th>행정규칙번호</th><th>제목</th><th>담당부서</th><th>등록일</th><th>첨부</th><th>조회</th></tr></thead>
  <tbody>
    <tr>
      <td class="m_hidden">3521</td>
      <td>고용노동부훈령 제602호</td>
      <td class="txt_left"><strong class="b_tit"><a href="view.do?bbs_seq=20260700323" onclick="scEventListener.fnView('20260700323');return false;">[훈령] 고용노동부 성희롱 성폭력 스토킹 예방 및 2차 피해 방지 지침</a></strong></td>
      <td><span class="ellipsis">양성평등정책담당관</span></td>
      <td>2026.07.08</td>
      <td><i class="ri-attachment-2"><span class="sr_only">첨부파일 있음</span></i></td>
      <td class="txt_right">1,699</td>
    </tr>
    <tr>
      <td class="m_hidden">3520</td>
      <td>고용노동부고시 제2026-54호</td>
      <td class="txt_left"><strong class="b_tit"><a href="view.do?bbs_seq=20260700318" onclick="scEventListener.fnView('20260700318');return false;">[고시] 고용위기 선제대응지역 지정 고시(인천광역시 제물포구)</a></strong></td>
      <td><span class="ellipsis">지역산업고용정책과</span></td>
      <td>2026.07.07</td>
      <td></td>
      <td class="txt_right">512</td>
    </tr>
  </tbody>
</table>
"""

SAMPLE_LIST_HTML_EMPTY = "<table><tbody></tbody></table>"

# bbs_seq=20260100003 상세페이지 — 첨부파일 1개 + 실제 본문(실제 응답 구조 그대로
# 축약, 2026-07-14 캡처). 담당자/전화번호가 body_text/department 어디에도 남으면
# 안 된다는 게 핵심(moe.py의 전화번호 제외 원칙과 동일).
SAMPLE_DETAIL_HTML_NOTIFICATION = """
<div class="board_view_wrap">
  <div class="b_info">
    <dl class="w100p"><dt>제목</dt><dd>건설공사의 노무비율 고시</dd></dl>
    <dl class="w100p"><dt>유형</dt><dd>고시&nbsp;</dd></dl>
    <dl><dt>담당부서</dt><dd>고용보험기획과&nbsp;</dd></dl>
    <dl><dt>전화번호</dt><dd>044-202-7358&nbsp;</dd></dl>
    <dl><dt>담당자</dt><dd>임동석&nbsp;</dd></dl>
    <dl><dt>등록일</dt><dd>2026-01-02&nbsp;</dd></dl>
    <div class="b_content">
      고용노동부고시 제2025-119호 「고용보험 및 산업재해보상보험의 보험료징수 등에 관한 법률」에 따라 건설공사의 노무비율을 다음과 같이 고시합니다.
    </div>
    <div class="file">
      <strong class="title">첨부</strong>
      <ul class="list">
        <li>
          <img src="/images/common/hwp.png" alt="hwp 첨부파일">
          <a href="/common/downloadFile.do?file_seq=20260100007&amp;bbs_seq=20260100003&amp;bbs_id=19&amp;file_ext=hwp" title="건설공사의 노무비율 고시(제2025-119호).hwp 다운로드">건설공사의 노무비율 고시(제2025-119호).hwp</a>
          <span class="link">
            <a class="btn_line" href="/common/downloadFile.do?file_seq=20260100007&amp;bbs_seq=20260100003&amp;bbs_id=19&amp;file_ext=hwp" title="다운로드">다운로드</a>
            <a class="btn_line attachPreview" href="javascript:void(0);" onclick="gfnPreView('/common/filePreview.do?file_seq=20260100007&amp;bbs_seq=20260100003&amp;bbs_id=19');">바로보기</a>
          </span>
        </li>
      </ul>
    </div>
  </div>
</div>
"""

# bbs_seq=20260700323 상세페이지 — 첨부파일 없는 훈령 케이스.
SAMPLE_DETAIL_HTML_DIRECTIVE_NO_FILES = """
<div class="board_view_wrap">
  <div class="b_info">
    <dl class="w100p"><dt>제목</dt><dd>고용노동부 성희롱 성폭력 스토킹 예방 및 2차 피해 방지 지침</dd></dl>
    <dl class="w100p"><dt>유형</dt><dd>훈령&nbsp;</dd></dl>
    <dl><dt>담당부서</dt><dd>양성평등정책담당관&nbsp;</dd></dl>
    <dl><dt>등록일</dt><dd>2026-07-08&nbsp;</dd></dl>
    <div class="b_content">이 지침은 고용노동부 소속 공무원의 성희롱·성폭력·스토킹 예방 및 2차 피해 방지를 위한 사항을 규정함을 목적으로 한다.</div>
  </div>
</div>
"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle)은 테스트에서 필요 없다."""
    monkeypatch.setattr(moel, "_throttle", lambda: None)


def test_fetch_list_extracts_bbs_seq_and_columns(monkeypatch):
    monkeypatch.setattr(moel, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = MoelAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_bbs_seq"] == "20260700323"
    assert "성희롱" in items[0]["_title"]
    assert items[0]["_department_list"] == "양성평등정책담당관"
    assert items[0]["_date_text"] == "2026.07.08"
    assert items[1]["_bbs_seq"] == "20260700318"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(moel, "_fetch_list_html", _fake_list)
    adapter = MoelAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_bbs_seq"] == "20260700318"
    assert calls == [1]


def test_fetch_list_stops_on_empty_page(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML if page == 1 else SAMPLE_LIST_HTML_EMPTY

    monkeypatch.setattr(moel, "_fetch_list_html", _fake_list)
    adapter = MoelAdapter()
    items = list(adapter.fetch_list())
    assert len(items) == 2
    assert calls == [1, 2]


def test_parse_detail_and_to_schema_with_file_excludes_contact_info(monkeypatch, tmp_path):
    """핵심 함정 회귀 테스트: 담당자 실명/전화번호가 department/body_text 어디에도
    남으면 안 된다(moe.py와 동일한 개인정보 제외 원칙)."""
    monkeypatch.setattr(moel, "_fetch_detail_html", lambda bbs_seq: SAMPLE_DETAIL_HTML_NOTIFICATION)
    downloaded: list[tuple[str, Path]] = []

    def _fake_download(url: str, dest: Path) -> None:
        downloaded.append((url, dest))
        dest.write_bytes(f"content-of-{url}".encode())

    monkeypatch.setattr(moel, "_download_file_streaming", _fake_download)

    adapter = MoelAdapter(files_root=tmp_path)
    raw = {
        "_bbs_seq": "20260100003",
        "_title": "건설공사의 노무비율 고시",
        "_department_list": "고용보험기획과",
        "_date_text": "2026.01.02",
        "_detail_url": "https://www.moel.go.kr/info/lawinfo/instruction/view.do?bbs_seq=20260100003",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "고용노동부"
    assert doc.department == "고용보험기획과"
    assert doc.production_date == date(2026, 1, 2)
    assert doc.doc_type == DOC_TYPE_NOTIFICATION
    assert "건설공사의 노무비율" in (doc.body_text or "")
    assert "044-202-7358" not in (doc.department or "")
    assert "044-202-7358" not in (doc.body_text or "")
    assert "임동석" not in (doc.body_text or "")
    # 다운로드/바로보기 두 링크가 같은 파일을 가리켜 dedup 후 1개만 다운로드돼야 함.
    assert len(downloaded) == 1
    assert doc.body_file_path == str(
        Path(SOURCE_MOEL) / DOC_TYPE_NOTIFICATION / "1-500" / "20260100003_건설공사의 노무비율 고시(제2025-119호).hwp"
    )
    assert doc.other_file_paths == []


def test_parse_detail_without_attachments_maps_directive_type(monkeypatch, tmp_path):
    monkeypatch.setattr(
        moel, "_fetch_detail_html", lambda bbs_seq: SAMPLE_DETAIL_HTML_DIRECTIVE_NO_FILES
    )

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("첨부파일이 없는데 다운로드가 호출됨")

    monkeypatch.setattr(moel, "_download_file_streaming", _fail_if_called)

    adapter = MoelAdapter(files_root=tmp_path)
    raw = {
        "_bbs_seq": "20260700323",
        "_title": "고용노동부 성희롱 성폭력 스토킹 예방 및 2차 피해 방지 지침",
        "_department_list": "양성평등정책담당관",
        "_date_text": "2026.07.08",
        "_detail_url": "https://www.moel.go.kr/info/lawinfo/instruction/view.do?bbs_seq=20260700323",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.doc_type == DOC_TYPE_DIRECTIVE
    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert doc.department == "양성평등정책담당관"


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(moel, "_fetch_detail_html", lambda bbs_seq: SAMPLE_DETAIL_HTML_NOTIFICATION)

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(moel, "_download_file_streaming", _fail_if_called)

    adapter = MoelAdapter(files_root=tmp_path)
    raw = {
        "_bbs_seq": "20260100003",
        "_title": "건설공사의 노무비율 고시",
        "_department_list": "고용보험기획과",
        "_date_text": "2026.01.02",
        "_detail_url": "https://www.moel.go.kr/info/lawinfo/instruction/view.do?bbs_seq=20260100003",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert list(tmp_path.iterdir()) == []
