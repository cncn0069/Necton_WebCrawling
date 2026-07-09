from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import browse_client, prism
from rd2.adapters.prism import PrismAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_RESEARCH_REPORT, SOURCE_PRISM

SAMPLE_LIST_HTML = """
<table>
<tbody>
<tr>
  <td class="no-ellipsis" aria-label="연구과제명"><strong class="b_tit"><a class="ellipsis" href="javascript:void(0)">공개 연구 샘플</a></strong></td>
  <td aria-label="연구분야">일반공공행정</td>
  <td aria-label="공개구분">공개</td>
  <td aria-label="연구수행기관">샘플연구원</td>
  <td aria-label="진행단계">연구결과활용</td>
  <td aria-label="관리기관">샘플기관</td>
  <td aria-label="연구기간">2026-01-01 ~ 2026-03-01</td>
  <td aria-label="조회수">10</td>
</tr>
<tr>
  <td class="no-ellipsis" aria-label="연구과제명"><strong class="b_tit"><a class="ellipsis" href="javascript:void(0)">비공개 연구 샘플</a></strong></td>
  <td aria-label="연구분야">일반공공행정</td>
  <td aria-label="공개구분">비공개</td>
  <td aria-label="연구수행기관">샘플연구원2</td>
  <td aria-label="진행단계">연구결과활용</td>
  <td aria-label="관리기관">샘플기관2</td>
  <td aria-label="연구기간">2026-02-01 ~ 2026-04-01</td>
  <td aria-label="조회수">5</td>
</tr>
</tbody>
</table>
"""

SAMPLE_DETAIL_HTML_OPEN = """
<table>
<tbody>
<tr><th>관리부서</th><td>샘플부서</td></tr>
<tr><th>목차</th><td>1 개요 2 본론 3 결론</td></tr>
<tr><th>초록</th><td>실제 연구 내용 요약입니다.</td></tr>
<tr><th>수행기관</th><td>샘플연구원</td></tr>
</tbody>
</table>
"""

SAMPLE_DETAIL_HTML_CLOSED = """
<table>
<tbody>
<tr><th>관리부서</th><td>샘플부서2</td></tr>
<tr><th>공개제한근거</th><td>5호</td></tr>
<tr><th>비공개사유</th><td>5. 감사·검사 등에 관한 정보</td></tr>
<tr><th>목차</th><td>1 개요 2 현황진단 3 개선방향</td></tr>
<tr><th>초록</th><td>본 과제는 비공개 연구입니다 향후 공개 시 제공할 예정입니다</td></tr>
<tr><th>수행기관</th><td>샘플연구원2</td></tr>
</tbody>
</table>
"""

SAMPLE_DETAIL_HTML_MULTI_CLAUSE = SAMPLE_DETAIL_HTML_CLOSED.replace(
    "<td>5호</td>", "<td>3호\xa05호</td>"
)

SAMPLE_DETAIL_HTML_PARTIAL = """
<table>
<tbody>
<tr><th>관리부서</th><td>샘플부서3</td></tr>
<tr><th>부분공개 연구보고서</th><td>일부 공개된 파일</td></tr>
<tr><th>초록</th><td>본 과제는 부분공개 연구입니다</td></tr>
<tr><th>수행기관</th><td>샘플연구원3</td></tr>
</tbody>
</table>
"""


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(
        browse_client, "fetch_prism_list_page", lambda page_number: SAMPLE_LIST_HTML
    )
    # 기본값: 파일 목록 없음 — 다운로드 동작을 검증하는 테스트만 개별적으로 override.
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: [])
    return PrismAdapter()


def test_fetch_list_extracts_both_rows(adapter, monkeypatch):
    monkeypatch.setattr(
        browse_client,
        "fetch_prism_detail_url",
        lambda idx, total, page_number=1: f"https://prism/{idx}",
    )
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_title"] == "공개 연구 샘플"
    assert items[0]["_disclosure_text"] == "공개"
    assert items[1]["_title"] == "비공개 연구 샘플"
    assert items[1]["_disclosure_text"] == "비공개"


def test_fetch_list_skip_avoids_clicking_skipped_rows(adapter, monkeypatch):
    """2026-07-08 plan-eng-review 회귀 테스트: skip된 행은 fetch_prism_detail_url
    (행당 서브프로세스 호출 5~6회의 비싼 클릭)을 아예 호출하지 않아야 한다 —
    체크포인트 재개 시 이미 처리한 행까지 매번 다시 클릭하던 O(n) 재작업 버그 수정."""
    clicked_indices = []

    def _fake_click(idx, total, page_number=1):
        clicked_indices.append(idx)
        return f"https://prism/{idx}"

    monkeypatch.setattr(browse_client, "fetch_prism_detail_url", _fake_click)

    items = list(adapter.fetch_list(skip=1, max_items=1))

    assert len(items) == 1
    assert items[0]["_title"] == "비공개 연구 샘플"
    assert clicked_indices == [1]  # 0번 행(건너뛴 행)은 클릭되지 않아야 한다


def test_open_item_maps_to_o_track(adapter, monkeypatch):
    monkeypatch.setattr(browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_OPEN)
    raw = {
        "_title": "공개 연구 샘플",
        "_subject_category": "일반공공행정",
        "_disclosure_text": "공개",
        "_performing_agency": "샘플연구원",
        "_ordering_agency": "샘플기관",
        "_period_text": "2026-01-01 ~ 2026-03-01",
        "_detail_url": "https://prism/0",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.start_date == date(2026, 1, 1)
    assert doc.end_date == date(2026, 3, 1)
    assert doc.body_text == "실제 연구 내용 요약입니다."
    assert doc.department == "샘플부서"
    assert doc.table_of_contents == "1 개요 2 본론 3 결론"


def test_closed_item_uses_real_clause(adapter, monkeypatch):
    monkeypatch.setattr(browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_CLOSED)
    raw = {
        "_title": "비공개 연구 샘플",
        "_subject_category": "일반공공행정",
        "_disclosure_text": "비공개",
        "_performing_agency": "샘플연구원2",
        "_ordering_agency": "샘플기관2",
        "_period_text": "2026-02-01 ~ 2026-04-01",
        "_detail_url": "https://prism/1",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.disclosure_status == DisclosureStatus.CLOSED
    assert doc.cso_classification == CsoClassification.S
    assert doc.cso_sub_clause == "5"
    assert "감사" in doc.non_disclosure_reason
    assert doc.body_text is None  # 비공개 안내문이 본문으로 새면 안 됨
    assert doc.table_of_contents == "1 개요 2 현황진단 3 개선방향"  # 목차는 비공개여도 실제 값


def test_multi_clause_prefers_c_when_mixed(adapter, monkeypatch):
    monkeypatch.setattr(
        browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_MULTI_CLAUSE
    )
    raw = {
        "_title": "복수 조항 샘플",
        "_disclosure_text": "비공개",
        "_ordering_agency": "샘플기관",
        "_period_text": None,
        "_detail_url": "https://prism/2",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.cso_classification == CsoClassification.C  # 3호(C)와 5호(S) 혼재 -> 더 제한적인 C
    assert doc.cso_sub_clause == "3,5"


def test_partial_disclosure_has_no_clause_but_still_valid(adapter, monkeypatch):
    monkeypatch.setattr(
        browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_PARTIAL
    )
    raw = {
        "_title": "부분공개 샘플",
        "_disclosure_text": "부분공개",
        "_ordering_agency": "샘플기관",
        "_period_text": None,
        "_detail_url": "https://prism/3",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.disclosure_status == DisclosureStatus.PARTIAL
    assert doc.cso_classification == CsoClassification.S
    assert doc.cso_sub_clause is None
    assert doc.non_disclosure_reason is not None


def test_parse_clause_numbers_handles_multiple():
    assert prism._parse_clause_numbers("5호\xa06호\xa07호") == [5, 6, 7]
    assert prism._parse_clause_numbers("5호") == [5]
    assert prism._parse_clause_numbers("") == []


def test_open_item_downloads_click_verified_files_and_picks_title_match(monkeypatch, tmp_path):
    """상세페이지에서 클릭 검증을 통과한 파일만 저장하고, body_file_path는 제목과
    가장 비슷한 파일명을 고른다(2026-07-07 사용자 결정)."""
    monkeypatch.setattr(
        browse_client, "fetch_prism_list_page", lambda page_number: SAMPLE_LIST_HTML
    )
    files = [
        {"fileSn": 1, "fileTypeCd": "D0150010", "fileNm": "정책연구과제_심의신청서(샘플).pdf",
         "asmtId": "3330000-1", "fileWkky": "000", "pdfTrsfYn": "Y"},
        {"fileSn": 1, "fileTypeCd": "D0150004", "fileNm": "공개 연구 샘플.pdf",
         "asmtId": "3330000-1", "fileWkky": "001", "pdfTrsfYn": "Y"},
    ]
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: files)
    monkeypatch.setattr(
        browse_client,
        "probe_and_download_prism_files",
        lambda file_list: [
            {**fm, "_raw_bytes": f"content-of-{fm['fileNm']}".encode()} for fm in file_list
        ],
    )
    monkeypatch.setattr(
        browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_OPEN
    )
    adapter = PrismAdapter(files_root=tmp_path)
    raw = {
        "_title": "공개 연구 샘플",
        "_subject_category": "일반공공행정",
        "_disclosure_text": "공개",
        "_performing_agency": "샘플연구원",
        "_ordering_agency": "샘플기관",
        "_period_text": "2026-01-01 ~ 2026-03-01",
        "_detail_url": "https://www.prism.go.kr/homepage/asmt/3330000-1",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "3330000-1_정책연구과제_심의신청서(샘플).pdf").exists()
    assert (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "3330000-1_공개 연구 샘플.pdf").exists()
    assert doc.body_file_path == str(
        Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "3330000-1_공개 연구 샘플.pdf"
    )
    assert doc.other_file_paths == [
        str(Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "3330000-1_정책연구과제_심의신청서(샘플).pdf")
    ]


def test_closed_item_downloads_nothing_when_click_verification_blocks_all(monkeypatch, tmp_path):
    """비공개 문서는 상세페이지에 "다운로드" 링크가 있어도 클릭하면 사이트가
    alert("비공개 연구보고서입니다.")로 막는다(2026-07-07 실사 확인) — 클릭 검증에서
    아무 파일도 통과하지 못하면 body_file_path는 반드시 None이어야 한다."""
    monkeypatch.setattr(
        browse_client, "fetch_prism_list_page", lambda page_number: SAMPLE_LIST_HTML
    )
    files = [
        {"fileSn": 1, "fileTypeCd": "D0150004", "fileNm": "비공개 연구 샘플.pdf",
         "asmtId": "6460000-1", "fileWkky": "001", "pdfTrsfYn": "Y"},
    ]
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: files)
    # 사이트가 클릭 시점에 alert로 막아 아무 파일도 통과하지 못하는 상황을 재현.
    monkeypatch.setattr(browse_client, "probe_and_download_prism_files", lambda file_list: [])
    monkeypatch.setattr(browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_CLOSED)
    adapter = PrismAdapter(files_root=tmp_path)
    raw = {
        "_title": "비공개 연구 샘플",
        "_subject_category": "일반공공행정",
        "_disclosure_text": "비공개",
        "_performing_agency": "샘플연구원2",
        "_ordering_agency": "샘플기관2",
        "_period_text": "2026-02-01 ~ 2026-04-01",
        "_detail_url": "https://www.prism.go.kr/homepage/asmt/6460000-1",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert list(tmp_path.iterdir()) == []


def test_skip_files_records_pending_flag_without_downloading(monkeypatch, tmp_path):
    """download_files=False면 클릭 검증/다운로드를 아예 호출하지 않고, 파일이
    존재한다는 사실만 _files_pending으로 알려야 한다(2-pass 백필용)."""
    monkeypatch.setattr(
        browse_client, "fetch_prism_list_page", lambda page_number: SAMPLE_LIST_HTML
    )
    files = [
        {"fileSn": 1, "fileTypeCd": "D0150004", "fileNm": "공개 연구 샘플.pdf",
         "asmtId": "3330000-1", "fileWkky": "001", "pdfTrsfYn": "Y"},
    ]
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: files)

    def _fail_if_called(file_list):
        raise AssertionError("download_files=False인데 probe_and_download가 호출됨")

    monkeypatch.setattr(browse_client, "probe_and_download_prism_files", _fail_if_called)
    monkeypatch.setattr(
        browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_OPEN
    )
    adapter = PrismAdapter(files_root=tmp_path)
    raw = {
        "_title": "공개 연구 샘플",
        "_disclosure_text": "공개",
        "_ordering_agency": "샘플기관",
        "_period_text": "2026-01-01 ~ 2026-03-01",
        "_detail_url": "https://www.prism.go.kr/homepage/asmt/3330000-1",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)

    assert detail["_files_pending"] is True
    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert list(tmp_path.iterdir()) == []


def test_skip_files_no_pending_when_no_files_listed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        browse_client, "fetch_prism_list_page", lambda page_number: SAMPLE_LIST_HTML
    )
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: [])
    monkeypatch.setattr(
        browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_OPEN
    )
    adapter = PrismAdapter(files_root=tmp_path)
    raw = {
        "_title": "공개 연구 샘플",
        "_disclosure_text": "공개",
        "_ordering_agency": "샘플기관",
        "_period_text": "2026-01-01 ~ 2026-03-01",
        "_detail_url": "https://www.prism.go.kr/homepage/asmt/3330000-1",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    assert detail["_files_pending"] is False


def test_download_files_for_reruns_click_verification_later(monkeypatch, tmp_path):
    """백필 패스가 쓰는 download_files_for()는 상세페이지를 다시 열고 나서
    parse_detail()의 다운로드 경로와 동일하게 클릭 검증+저장을 수행해야 한다."""
    files = [
        {"fileSn": 1, "fileTypeCd": "D0150004", "fileNm": "공개 연구 샘플.pdf",
         "asmtId": "3330000-1", "fileWkky": "001", "pdfTrsfYn": "Y"},
    ]
    goto_calls: list[str] = []

    def _fake_goto(url):
        goto_calls.append(url)
        return SAMPLE_DETAIL_HTML_OPEN

    monkeypatch.setattr(browse_client, "fetch_rendered_detail_html", _fake_goto)
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: files)
    monkeypatch.setattr(
        browse_client,
        "probe_and_download_prism_files",
        lambda file_list: [
            {**fm, "_raw_bytes": f"content-of-{fm['fileNm']}".encode()} for fm in file_list
        ],
    )
    adapter = PrismAdapter(files_root=tmp_path)
    body_file_path, other_file_paths = adapter.download_files_for(
        "https://www.prism.go.kr/homepage/asmt/3330000-1", "공개 연구 샘플"
    )

    assert goto_calls == ["https://www.prism.go.kr/homepage/asmt/3330000-1"]
    assert body_file_path == str(Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "3330000-1_공개 연구 샘플.pdf")
    assert other_file_paths == []
    assert (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "3330000-1_공개 연구 샘플.pdf").exists()


def test_partial_item_downloads_only_click_verified_subset(monkeypatch, tmp_path):
    """부분공개 문서는 API 목록에 여러 파일이 있어도 실제로 클릭 검증을 통과한
    파일만 저장해야 한다(2026-07-07 실사: 요약본/전체보고서는 링크 자체가 없어
    클릭 불가, 활용결과보고서만 링크가 있고 정상 통과)."""
    monkeypatch.setattr(
        browse_client, "fetch_prism_list_page", lambda page_number: SAMPLE_LIST_HTML
    )
    all_files = [
        {"fileSn": 1, "fileTypeCd": "D0150003", "fileNm": "[요약본]부분공개 샘플.pdf",
         "asmtId": "3330000-5", "fileWkky": "000", "pdfTrsfYn": "Y"},
        {"fileSn": 1, "fileTypeCd": "D0150018", "fileNm": "정책연구_활용결과_보고서.pdf",
         "asmtId": "3330000-5", "fileWkky": "000", "pdfTrsfYn": "Y"},
    ]
    monkeypatch.setattr(browse_client, "fetch_prism_file_list", lambda asmt_id: all_files)
    # 링크가 없는 요약본은 클릭 검증 단계에서 애초에 후보가 안 되고,
    # 활용결과보고서만 링크가 있어 통과했다고 가정.
    only_activity_report = all_files[1]
    monkeypatch.setattr(
        browse_client,
        "probe_and_download_prism_files",
        lambda file_list: [{**only_activity_report, "_raw_bytes": b"activity-report-bytes"}],
    )
    monkeypatch.setattr(
        browse_client, "fetch_rendered_detail_html", lambda url: SAMPLE_DETAIL_HTML_PARTIAL
    )
    adapter = PrismAdapter(files_root=tmp_path)
    raw = {
        "_title": "부분공개 샘플",
        "_disclosure_text": "부분공개",
        "_ordering_agency": "샘플기관",
        "_period_text": None,
        "_detail_url": "https://www.prism.go.kr/homepage/asmt/3330000-5",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path == str(
        Path(SOURCE_PRISM) / DOC_TYPE_RESEARCH_REPORT / "3330000-5_정책연구_활용결과_보고서.pdf"
    )
    assert doc.other_file_paths == []
    assert not (tmp_path / SOURCE_PRISM / DOC_TYPE_RESEARCH_REPORT / "3330000-5_[요약본]부분공개 샘플.pdf").exists()
