from datetime import date
from pathlib import Path

import httpx
import pytest

from rd2.adapters import seoul_opengov
from rd2.adapters.seoul_opengov import SeoulOpengovAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import (
    DOC_TYPE_OFFICIAL_DOCUMENT,
    SOURCE_SEOUL_OPENGOV,
)

# 실제 사이트(opengov.seoul.go.kr/sanction/list) 원문 HTML 구조를 그대로 축약한
# 샘플(2026-07-16 curl 직접 요청으로 실사 확인). 제목 앞에 스크린리더용
# <strong class="element-invisible">제목 : </strong>이 붙어 있고, title-category의
# 두 span이 [공개구분, 기관구분] 순서라는 게 핵심 구조.
SAMPLE_LIST_HTML = """
<div class="view-content">
  <ul>
    <li>
      <div class="title-area">
        <div class="title-wrap width100">
          <a href="/sanction/36538874"><strong class="element-invisible">제목 : </strong><span>2026년 6월 관내 출장 여비 지급</span></a>
          <p class="title-category"><span>부분공개</span> <span>서울시</span></p>
        </div>
      </div>
      <p class="title-info">
        <span class="date"><strong class="element-invisible">등록일 : </strong> 2026-07-15</span>
        <span class="dept"><strong class="element-invisible">부서 : </strong> 평생교육국 친환경급식과</span>
      </p>
    </li>
    <li>
      <div class="title-area">
        <div class="title-wrap width100">
          <a href="/sanction/36533899"><strong class="element-invisible">제목 : </strong><span>연장근무 및 초과근무 실시</span></a>
          <p class="title-category"><span>공개</span> <span>자치구</span></p>
        </div>
      </div>
      <p class="title-info">
        <span class="date"><strong class="element-invisible">등록일 : </strong> 2026-07-15</span>
        <span class="dept"><strong class="element-invisible">부서 : </strong> 중부공원여가센터 공원운영과</span>
      </p>
    </li>
    <li><a href="/sanction/list?page=2">2</a></li>
  </ul>
</div>
"""

SAMPLE_LIST_HTML_EMPTY = '<div class="view-content"><ul></ul></div>'

# nid=36538874 상세페이지 — 부분공개 문서(2026-07-16 캡처를 축약).
# 핵심 함정 세 가지가 전부 들어 있다:
# 1) 첨부 4건 중 결재문서본문.hwpx만 다운로드 링크가 있고 나머지 3건은
#    "비공개 문서"(span.txt-notopen)라 링크 자체가 없다.
# 2) 문서 정보 테이블에 작성자(전화번호) = 실명+직통번호가 있다 — 어디에도
#    저장되면 안 된다.
# 3) 분류정보 td 끝에 "같은 분류 문서보기" 링크 텍스트가 붙어 있다 — BRM
#    경로만 남기고 제거해야 한다.
SAMPLE_DETAIL_HTML_PARTIAL = """
<div class="view-content view-content-article">
  <h3 class="title-article">2026년 6월 관내 출장 여비 지급</h3>
  <div class="comm-view-article print-no">
    <h4 id="attachment">첨부된 문서</h4>
    <ul class="list-attachment">
      <li>
        <p class="title-down">결재문서본문.hwpx <span class="txt-gray">(33.94 KB)</span> <span>  </span></p>
        <span class="btn-downset">
          <button type="button" onclick="docview8('F0000120563674', 'hview');" class="btn btn-view">문서보기</button><a href="/og/com/download.php?nid=36538874&amp;dtype=basic&amp;rid=F0000120563674&amp;fid=" class="btn btn-download btn-original">원문<em class="element-invisible">다운로드</em></a>
        </span>
      </li>
      <li>
        <p class="title-down"><span class="txt-gray">1. 출장내역서.xlsx</span></p>
        <span class="btn-downset"><span class="txt-notopen">비공개 문서</span></span>
      </li>
      <li>
        <p class="title-down"><span class="txt-gray">2. 출장여비 지급조서.xlsx</span></p>
        <span class="btn-downset"><span class="txt-notopen">비공개 문서</span></span>
      </li>
      <li>
        <p class="title-down"><span class="txt-gray">3. 입금의뢰명세서.xlsx</span></p>
        <span class="btn-downset"><span class="txt-notopen">비공개 문서</span></span>
      </li>
    </ul>
  </div>
  <div class="comm-view-article print-yes">
    <h4>문서 정보</h4>
    <div class="table-wrap">
      <table class="table table-response">
        <tbody>
          <tr>
            <th scope="row">기관명</th><td>서울시</td>
            <th scope="row">부서명</th><td>평생교육국 친환경급식과</td>
          </tr>
          <tr>
            <th scope="row">문서번호</th><td>친환경급식과-6067</td>
            <th scope="row">생산일자</th><td>2026-07-15</td>
          </tr>
          <tr>
            <th scope="row">공개구분</th><td>부분공개</td>
            <th scope="row">보존기간</th><td>5년</td>
          </tr>
          <tr>
            <th scope="row">작성자(전화번호)</th><td>정재은 (02-2133-4152)</td>
            <th scope="row">관리번호</th><td>D0000056769804</td>
          </tr>
          <tr>
            <th scope="row">분류정보</th>
            <td>행정 &gt;  일반행정지원  &gt;  과공통일반사무  &gt;  예산회계(서무)  &gt;  급여및수당관리 <a href="/public/category">같은 분류 문서보기</a></td>
          </tr>
          <tr><th scope="row">이용조건</th><td></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
"""


# 뷰어 체인(문서보기) 실제 응답 구조 그대로 축약(2026-07-16 실사 —
# docviewer.js 리버스엔지니어링, 어댑터 독스트링 참고).
SAMPLE_VIEWER_TRIGGER = {"success": "true", "iframe_para": "?p_path=/sanction/36538874"}

SAMPLE_VIEWER_DOCUMENT_JSON = {
    "docsconverter": {
        "file": {
            "convert_total_page": "2",
            "filename": "F0000120563674.hwpx",
            "pages_info": {
                "1": {"height": 1123, "src": "F0000120563674_1.html", "width": 794},
                "2": {"height": 1123, "src": "F0000120563674_2.html", "width": 794},
            },
            "total_page": "2",
        },
        "result": {"code": "0000", "message": "SUCCESS"},
    }
}

# 실제 변환 페이지처럼 개인정보가 사이트 측에서 이미 ****로 마스킹돼 있다.
SAMPLE_VIEWER_PAGES = {
    "F0000120563674_1.html": "<html><body>﻿<div>2026년 6월 관내 출장 여비를 아래와 같이 지급하고자 합니다. 대상인원 : **** 지급방법 : 개인별 계좌이체</div></body></html>",
    "F0000120563674_2.html": "<html><body><div>붙임 1. 출장내역서 1부. 끝.</div></body></html>",
}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle, 이 어댑터는 운영자 안내대로 10초)은
    테스트에서 필요 없다."""
    monkeypatch.setattr(seoul_opengov, "_throttle", lambda: None)


def _mock_viewer_chain(monkeypatch):
    monkeypatch.setattr(
        seoul_opengov, "_fetch_viewer_trigger", lambda rid, *, referer: SAMPLE_VIEWER_TRIGGER
    )
    monkeypatch.setattr(
        seoul_opengov, "_fetch_viewer_document_json", lambda rid: SAMPLE_VIEWER_DOCUMENT_JSON
    )
    monkeypatch.setattr(
        seoul_opengov,
        "_fetch_viewer_page_html",
        lambda rid, page_src: SAMPLE_VIEWER_PAGES[page_src],
    )


def _raw_item() -> dict:
    return {
        "_nid": "36538874",
        "_title": "2026년 6월 관내 출장 여비 지급",
        "_disclosure_list": "부분공개",
        "_agency_category": "서울시",
        "_date_text": "2026-07-15",
        "_department_list": "평생교육국 친환경급식과",
        "_detail_url": "https://opengov.seoul.go.kr/sanction/36538874",
    }


def test_fetch_list_parses_items_and_ignores_pager_links(monkeypatch):
    monkeypatch.setattr(seoul_opengov, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = SeoulOpengovAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_nid"] == "36538874"
    assert items[0]["_title"] == "2026년 6월 관내 출장 여비 지급"
    assert items[0]["_disclosure_list"] == "부분공개"
    assert items[0]["_agency_category"] == "서울시"
    assert items[0]["_date_text"] == "2026-07-15"
    assert items[0]["_department_list"] == "평생교육국 친환경급식과"
    assert items[1]["_nid"] == "36533899"
    assert items[1]["_agency_category"] == "자치구"


def test_fetch_list_skip_jumps_to_page_without_walking(monkeypatch):
    """요청 지연이 페이지당 10초라 skip은 페이지 계산으로 바로 건너뛰어야 한다
    (orginl_info와 동일) — skip=51이면 2페이지 하나만 요청하고 그 페이지의
    두 번째 항목부터 내보낸다."""
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(seoul_opengov, "_fetch_list_html", _fake_list)
    adapter = SeoulOpengovAdapter()
    items = list(adapter.fetch_list(skip=51, max_items=1))
    assert calls == [2]
    assert len(items) == 1
    assert items[0]["_nid"] == "36533899"


def test_fetch_list_stops_on_empty_page(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML if page == 1 else SAMPLE_LIST_HTML_EMPTY

    monkeypatch.setattr(seoul_opengov, "_fetch_list_html", _fake_list)
    adapter = SeoulOpengovAdapter()
    items = list(adapter.fetch_list())
    assert len(items) == 2
    assert calls == [1, 2]


def test_parse_detail_and_to_schema_partial_disclosure(monkeypatch, tmp_path):
    """핵심 회귀 테스트: ① 비공개 첨부는 다운로드하지 않는다 ② 작성자
    실명/전화번호가 Document 어디에도 남지 않는다 ③ 부분공개 → S 트랙 +
    비공개사유 근사 매핑 ④ BRM 분류에서 '같은 분류 문서보기' 링크 텍스트가
    제거된다."""
    monkeypatch.setattr(
        seoul_opengov, "_fetch_detail_html", lambda nid: SAMPLE_DETAIL_HTML_PARTIAL
    )
    _mock_viewer_chain(monkeypatch)
    downloaded: list[str] = []

    def _fake_download(url: str, dest: Path, *, referer: str) -> None:
        downloaded.append(url)
        assert referer == "https://opengov.seoul.go.kr/sanction/36538874"
        dest.write_bytes(b"hwpx-bytes")

    monkeypatch.setattr(seoul_opengov, "_download_file_streaming", _fake_download)

    adapter = SeoulOpengovAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item())
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.PARTIAL
    assert doc.cso_classification == CsoClassification.S
    assert doc.non_disclosure_reason is not None
    assert doc.ordering_agency == "서울시"
    assert doc.department == "평생교육국 친환경급식과"
    assert doc.production_date == date(2026, 7, 15)
    # 제목("지급")과 무관하게 고정값 — 이 게시판의 수집물은 전부 결재 통지
    # 커버 문서(2026-07-16 사용자 결정, 어댑터 독스트링 참고).
    assert doc.doc_type == DOC_TYPE_OFFICIAL_DOCUMENT
    assert doc.content_summary == "친환경급식과-6067"
    assert doc.subject_category == "행정 > 일반행정지원 > 과공통일반사무 > 예산회계(서무) > 급여및수당관리"
    assert "같은 분류" not in (doc.subject_category or "")
    assert doc.unit_task == "급여및수당관리"
    assert doc.source == SOURCE_SEOUL_OPENGOV
    assert doc.source_url == "https://opengov.seoul.go.kr/sanction/36538874"

    # 본문은 뷰어 변환본 2페이지가 개행으로 합쳐져 저장된다 — 사이트 측
    # 마스킹(****)은 그대로 유지돼야 한다.
    assert "지급하고자 합니다" in (doc.body_text or "")
    assert "대상인원 : ****" in (doc.body_text or "")
    assert "출장내역서 1부" in (doc.body_text or "")
    assert not (doc.body_text or "").startswith("﻿")

    # 비공개 첨부 3건은 건너뛰고 결재문서본문 1건만 다운로드.
    assert len(downloaded) == 1
    assert "download.php" in downloaded[0]
    assert doc.body_file_path == str(
        Path(SOURCE_SEOUL_OPENGOV)
        / DOC_TYPE_OFFICIAL_DOCUMENT
        / "1-500"
        / "36538874_결재문서본문.hwpx"
    )
    assert doc.other_file_paths == []

    # 개인정보(작성자 실명/전화번호)가 어디에도 남으면 안 된다.
    for value in doc.model_dump().values():
        text = str(value)
        assert "정재은" not in text
        assert "02-2133-4152" not in text


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(
        seoul_opengov, "_fetch_detail_html", lambda nid: SAMPLE_DETAIL_HTML_PARTIAL
    )

    def _fail_if_called(url: str, dest: Path, *, referer: str) -> None:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(seoul_opengov, "_download_file_streaming", _fail_if_called)

    adapter = SeoulOpengovAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item(), download_files=False, fetch_body_text=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert doc.body_text is None
    assert list(tmp_path.iterdir()) == []


def test_parse_detail_ignores_empty_download_endpoint(monkeypatch, tmp_path):
    detail_html = SAMPLE_DETAIL_HTML_PARTIAL.replace(
        '<a href="/og/com/download.php?nid=36538874',
        '<a href="/og/com/download.php"></a><a href="/og/com/download.php?nid=36538874',
    )
    monkeypatch.setattr(seoul_opengov, "_fetch_detail_html", lambda nid: detail_html)
    _mock_viewer_chain(monkeypatch)
    downloaded: list[str] = []
    monkeypatch.setattr(
        seoul_opengov,
        "_download_file_streaming",
        lambda url, dest, *, referer: (downloaded.append(url), dest.write_bytes(b"hwpx")),
    )

    SeoulOpengovAdapter(files_root=tmp_path).parse_detail(_raw_item())

    assert downloaded == [
        "https://opengov.seoul.go.kr/og/com/download.php?nid=36538874&dtype=basic&rid=F0000120563674&fid="
    ]


def test_download_rejects_error_payload_without_replacing_existing_file(monkeypatch, tmp_path):
    """HTTP 200 ``error`` 응답은 HWPX 파일로 저장되면 안 된다."""
    destination = tmp_path / "document.hwpx"
    destination.write_bytes(b"PK\x03\x04existing")

    class _Response:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"error"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(seoul_opengov.httpx, "stream", lambda *args, **kwargs: _Response())

    with pytest.raises(ValueError, match="ZIP"):
        seoul_opengov._download_file_streaming(
            "https://example.test/download", destination, referer="https://example.test/detail"
        )

    assert destination.read_bytes() == b"PK\x03\x04existing"
    assert not (tmp_path / ".document.hwpx.part").exists()


def test_body_text_failure_is_best_effort(monkeypatch, tmp_path):
    """뷰어 변환 실패는 부가 정보 손실일 뿐 — 문서 수집 자체는 계속돼야 한다
    (격리 금지)."""
    monkeypatch.setattr(
        seoul_opengov, "_fetch_detail_html", lambda nid: SAMPLE_DETAIL_HTML_PARTIAL
    )
    monkeypatch.setattr(
        seoul_opengov, "_download_file_streaming",
        lambda url, dest, *, referer: dest.write_bytes(b"hwpx"),
    )

    def _boom(rid, *, referer):
        raise httpx.ConnectError("viewer down")

    monkeypatch.setattr(seoul_opengov, "_fetch_body_text", _boom)

    adapter = SeoulOpengovAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item())
    doc = adapter.to_schema(detail)
    assert doc.body_text is None
    assert doc.body_file_path is not None  # 파일 다운로드는 정상 진행


def test_body_text_none_when_trigger_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        seoul_opengov, "_fetch_detail_html", lambda nid: SAMPLE_DETAIL_HTML_PARTIAL
    )
    monkeypatch.setattr(
        seoul_opengov, "_download_file_streaming",
        lambda url, dest, *, referer: dest.write_bytes(b"hwpx"),
    )
    monkeypatch.setattr(
        seoul_opengov, "_fetch_viewer_trigger",
        lambda rid, *, referer: {"success": "false"},
    )

    def _fail_if_called(rid):
        raise AssertionError("트리거 실패면 document.json을 조회하면 안 됨")

    monkeypatch.setattr(seoul_opengov, "_fetch_viewer_document_json", _fail_if_called)

    adapter = SeoulOpengovAdapter(files_root=tmp_path)
    detail = adapter.parse_detail(_raw_item())
    doc = adapter.to_schema(detail)
    assert doc.body_text is None


def test_to_schema_rejects_unknown_disclosure(monkeypatch, tmp_path):
    """공개구분이 없거나 알 수 없는 값이면 공개로 간주하지 말고 격리(예외)해야
    한다 — orginl_info와 동일한 원칙."""
    adapter = SeoulOpengovAdapter(files_root=tmp_path)
    enriched = dict(_raw_item(), _disclosure=None)
    with pytest.raises(ValueError):
        adapter.to_schema(enriched)
    enriched = dict(_raw_item(), _disclosure="열람제한")
    with pytest.raises(ValueError):
        adapter.to_schema(enriched)
