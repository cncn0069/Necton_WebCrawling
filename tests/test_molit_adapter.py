from datetime import date
from pathlib import Path

import pytest

from rd2.adapters import molit
from rd2.adapters.molit import MolitAdapter
from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.storage.naming import DOC_TYPE_MEETING_MINUTES, DOC_TYPE_POLICY_MATERIAL, SOURCE_MOLIT

# 실제 사이트(molit.go.kr, m_34681) 원문 HTML 구조를 그대로 축약한 샘플
# (2026-07-09 httpx 직접 요청으로 실사 확인). 목록의 "번호"(4585)와 상세링크의
# id(4901)가 서로 다른 값 공간이라는 게 이 게시판의 핵심 함정 — 아래 목록 샘플도
# 실제로 그 두 값이 다르게 나온 실사 결과를 그대로 반영한다.
SAMPLE_LIST_HTML = """
<table>
<tr>
  <th scope="col" class="bd_num">번호</th>
  <th scope="col" class="bd_title">제목</th>
  <th scope="col" class="bd_category">분류</th>
  <th scope="col" class="bd_date">등록일자</th>
</tr>
<tr>
  <td class="bd_num">4585</td>
  <td class="bd_title">
    <a href="dtl.jsp?search=&amp;srch_dept_nm=&amp;srch_dept_id=&amp;srch_usr_nm=&amp;srch_usr_titl=Y&amp;srch_usr_ctnt=&amp;search_regdate_s=&amp;search_regdate_e=&amp;psize=10&amp;s_category=&amp;p_category=&amp;lcmspage=1&amp;id=4901" class="">
      2차로형 회전교차로 설치 및 개선 가이드라인
    </a>
  </td>
  <td class="bd_category">도로철도&gt;도로정책</td>
  <td class="bd_date">2026-07-07</td>
</tr>
<tr>
  <td class="bd_num">4576</td>
  <td class="bd_title">
    <a href="dtl.jsp?search=&amp;srch_dept_nm=&amp;srch_dept_id=&amp;srch_usr_nm=&amp;srch_usr_titl=Y&amp;srch_usr_ctnt=&amp;search_regdate_s=&amp;search_regdate_e=&amp;psize=10&amp;s_category=&amp;p_category=&amp;lcmspage=1&amp;id=4891" class="">
      스마트 안전장비 활용 가이드라인 개정 알림
    </a>
  </td>
  <td class="bd_category">건설&gt;기술안전</td>
  <td class="bd_date">2026-05-06</td>
</tr>
</table>
"""

# id=4901 상세페이지 — 실제 응답을 그대로 캡처(2026-07-09, div.bd_view.prettify() 덤프).
SAMPLE_DETAIL_HTML_SINGLE_FILE = """
<div class="bd_view">
 <h4>
  2차로형 회전교차로 설치 및 개선 가이드라인
 </h4>
 <ul class="bd_view_ul_info">
  <li>
   <strong>담당부서</strong>
   <span>도로건설과</span>
  </li>
  <li>
   <strong>담당자</strong>
   <span>허원행</span>
  </li>
  <li>
   <strong>전화번호</strong>
   <span>044-201-3893</span>
  </li>
  <li>
   <strong>등록일</strong>
   <span>2026-07-07</span>
  </li>
  <li>
   <strong>조회</strong>
   <span>185</span>
  </li>
  <li>
   <strong>분류</strong>
   <span>도로철도 &gt; 도로정책</span>
  </li>
  <li class="file">
   <strong>첨부파일</strong>
   <span>
    <a href="/portal/common/download/DownloadMltm2.jsp?FilePath=portal/DextUpload/202607/20260707_161413_968.pdf&amp;FileName=2차로형 회전교차로 설치 및 개선 가이드라인_(배포용).pdf">
     <i><img alt="pdf" src="/images/www2019/board/ico_pdf.gif"/></i>
     2차로형 회전교차로 설치 및 개선 가이드라인_(배포용).pdf
    </a>
    <a class="icon_docu" href="/USR/viewer.do?mode=pc&amp;type=policyData&amp;id=AAATkEAAvAAB/xfAAZ" target="_blank" title="미리보기">바로보기</a>
   </span>
  </li>
 </ul>
 <div class="bd_view_cont">
  2차로형 회전교차로 설치 및 개선 가이드라인
 </div>
</div>
"""

# id=4898 상세페이지 — 첨부파일 없는 케이스(의견수렴 공지). 필드 값은 실사로 확인된
# 그대로이며, 구조는 확인된 selector(bd_view/bd_view_ul_info/bd_view_cont)를 그대로 씀.
SAMPLE_DETAIL_HTML_NO_FILES = """
<div class="bd_view">
 <h4>감항성개선지시 기술검토서</h4>
 <ul class="bd_view_ul_info">
  <li><strong>담당부서</strong><span>항공기술과</span></li>
  <li><strong>담당자</strong><span>김호진</span></li>
  <li><strong>전화번호</strong><span>044-201-4288</span></li>
  <li><strong>등록일</strong><span>2026-06-25</span></li>
  <li><strong>조회</strong><span>331</span></li>
  <li><strong>분류</strong><span>항공 &gt; 항공안전정책</span></li>
 </ul>
 <div class="bd_view_cont">
  당해 감항성개선지시서 기술검토서는 2026.7.24.까지 국토교통부 홈페이지에 게시하고 의견을 수렴하오니, 국토교통부 항공기술과 담당자에게 전화(044-201-4288) 또는 메일(aw_division@korea.kr)로 제출해 주시기 바랍니다.
 </div>
</div>
"""

# id=4892 상세페이지 — 첨부파일 5개(터널붕괴사고 조사보고서 + 부록). 실사로 확인한
# 실제 파일명/개수를 그대로 반영, 구조는 확인된 li.file 안에 여러 <a> 반복 패턴.
SAMPLE_DETAIL_HTML_MULTI_FILE = """
<div class="bd_view">
 <h4>신안산선 복선전철 제5-2공구 건설공사 터널붕괴사고 건설사고조사위원회 조사결과 보고서</h4>
 <ul class="bd_view_ul_info">
  <li><strong>담당부서</strong><span>건설안전과</span></li>
  <li><strong>담당자</strong><span>서경원</span></li>
  <li><strong>전화번호</strong><span>044-201-3586</span></li>
  <li><strong>등록일</strong><span>2026-05-14</span></li>
  <li><strong>조회</strong><span>3277</span></li>
  <li><strong>분류</strong><span>건설 &gt; 기술안전</span></li>
  <li class="file">
   <strong>첨부파일</strong>
   <span>
    <a href="/portal/common/download/DownloadMltm2.jsp?FilePath=portal/DextUpload/202605/20260514_131734_237.pdf&amp;FileName=1_신안산선 5-2공구 터널붕괴사고 사고조사보고서.pdf">1_신안산선 5-2공구 터널붕괴사고 사고조사보고서.pdf</a>
    <a href="/portal/common/download/DownloadMltm2.jsp?FilePath=portal/DextUpload/202605/20260514_131748_018.pdf&amp;FileName=부록-1_경기광명 신안산선 터널붕괴사고 원인규명을 위한 구조해석_최종보고서.pdf">부록-1_경기광명 신안산선 터널붕괴사고 원인규명을 위한 구조해석_최종보고서.pdf</a>
   </span>
  </li>
 </ul>
 <div class="bd_view_cont">건설기술진흥법 제67조제3항 및 시행령 제105조제4항에 따라 사조조사 결과보고를 공개합니다.</div>
</div>
"""

# 제목에 "회의록"이 들어간 케이스 — 실제 캡처는 아니고(이 게시판 실사 10건 샘플엔
# 없었음) 확인된 selector 구조를 그대로 써서 구성. 회의록은 doc_type이 분리돼야
# 한다는 요청(2026-07-09)에 따른 회귀 테스트용.
SAMPLE_DETAIL_HTML_MEETING_MINUTES = """
<div class="bd_view">
 <h4>2026년 제3차 도로정책심의위원회 회의록</h4>
 <ul class="bd_view_ul_info">
  <li><strong>담당부서</strong><span>도로정책과</span></li>
  <li><strong>등록일</strong><span>2026-06-20</span></li>
  <li><strong>분류</strong><span>도로철도 &gt; 도로정책</span></li>
  <li class="file">
   <strong>첨부파일</strong>
   <span>
    <a href="/portal/common/download/DownloadMltm2.jsp?FilePath=portal/DextUpload/202606/20260620_090000_001.pdf&amp;FileName=2026년 제3차 도로정책심의위원회 회의록.pdf">2026년 제3차 도로정책심의위원회 회의록.pdf</a>
   </span>
  </li>
 </ul>
 <div class="bd_view_cont">2026년 제3차 도로정책심의위원회 회의록을 공개합니다.</div>
</div>
"""

# id=4899 상세페이지 — 실제 사이트 HTML 결함 재현(2026-07-09 원문 그대로 캡처).
# 이 앵커는 href='...'(홑따옴표)로 감싸져 있는데 파일명 자체에 홑따옴표가 들어있어
# ("('26.07.01) ...") FileName 쿼리파라미터가 그 지점에서 잘린다 — 브라우저로 봐도
# 마찬가지로 깨지는, 우리 파싱 버그가 아닌 사이트 자체의 결함.
SAMPLE_DETAIL_HTML_BROKEN_FILENAME_ATTR = """
<div class="bd_view">
 <h4>전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황</h4>
 <ul class="bd_view_ul_info">
  <li><strong>담당부서</strong><span>항공안전정책과</span></li>
  <li><strong>등록일</strong><span>2026-07-01</span></li>
  <li><strong>분류</strong><span>항공 &gt; 항공안전정책</span></li>
  <li class="file">
   <strong>첨부파일</strong>
   <span>
    <a href='/portal/common/download/DownloadMltm2.jsp?FilePath=portal/DextUpload/202607/20260701_131225_903.pdf&FileName=('26.07.01)  전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황.pdf'><i><img src='/images/www2019/board/ico_pdf.gif' alt='pdf' /></i>('26.07.01)  전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황.pdf</a>
   </span>
  </li>
 </ul>
 <div class="bd_view_cont">항공정책실 항공안전정책과에서 관리하는 현황 자료입니다</div>
</div>
"""

# id=4580(실제로는 다른 id 공간) 접근 시 실제로 받은 응답 — 원문 그대로(2026-07-09 캡처).
SAMPLE_DETAIL_HTML_PAGE_MOVED = """
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html xml:lang="ko" lang="ko">
<head>
<title>페이지 이동중 | 국토교통부</title>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<meta http-equiv="refresh" content="0; url=">
<script type="text/javascript" src="/LCMS/js/lcms.js"></script>
<script type="text/javascript">
alert(getMessage('0011'));
history.back();
</script>
</head>
<body><!--H1--></body>
</html>
"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """실제 요청 간 지연(_throttle)은 테스트에서 필요 없다."""
    monkeypatch.setattr(molit, "_throttle", lambda: None)


def test_fetch_list_extracts_id_from_href_not_from_display_number(monkeypatch):
    """핵심 함정 회귀 테스트: 표의 "번호"(예: 4585)와 상세링크의 id(예: 4901)는
    다른 값 공간이다 — id를 번호로 유추하면 엉뚱한 게시물을 받는다(실사 중 실제로
    겪은 버그)."""
    monkeypatch.setattr(molit, "_fetch_list_html", lambda page: SAMPLE_LIST_HTML)
    adapter = MolitAdapter()
    items = list(adapter.fetch_list(max_items=2))
    assert len(items) == 2
    assert items[0]["_item_id"] == "4901"  # 번호=4585가 아니라 href의 id
    assert items[0]["_title"] == "2차로형 회전교차로 설치 및 개선 가이드라인"
    assert items[1]["_item_id"] == "4891"


def test_fetch_list_skip_skips_without_extra_requests(monkeypatch):
    calls: list[int] = []

    def _fake_list(page: int) -> str:
        calls.append(page)
        return SAMPLE_LIST_HTML

    monkeypatch.setattr(molit, "_fetch_list_html", _fake_list)
    adapter = MolitAdapter()
    items = list(adapter.fetch_list(skip=1, max_items=1))
    assert len(items) == 1
    assert items[0]["_item_id"] == "4891"
    assert calls == [1]


def test_parse_detail_and_to_schema_single_file(monkeypatch, tmp_path):
    monkeypatch.setattr(molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_SINGLE_FILE)
    downloaded: list[tuple[str, Path]] = []

    def _fake_download(url: str, dest: Path) -> None:
        downloaded.append((url, dest))
        dest.write_bytes(f"content-of-{url}".encode())

    monkeypatch.setattr(molit, "_download_file_streaming", _fake_download)

    adapter = MolitAdapter(files_root=tmp_path)
    raw = {
        "_item_id": "4901",
        "_title": "2차로형 회전교차로 설치 및 개선 가이드라인",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=4901",
        "_category": "도로철도>도로정책",
        "_list_date": "2026-07-07",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.disclosure_status == DisclosureStatus.OPEN
    assert doc.cso_classification == CsoClassification.O
    assert doc.ordering_agency == "국토교통부"
    assert doc.department == "도로건설과"
    assert doc.subject_category == "도로철도 > 도로정책"
    assert doc.production_date == date(2026, 7, 7)
    assert doc.doc_type == DOC_TYPE_POLICY_MATERIAL
    assert doc.non_disclosure_reason is None  # OPEN이라 검증 통과해야 함
    assert len(downloaded) == 1
    assert doc.body_file_path == str(
        Path(SOURCE_MOLIT) / DOC_TYPE_POLICY_MATERIAL / "1-500"
        / "4901_2차로형 회전교차로 설치 및 개선 가이드라인_(배포용).pdf"
    )
    assert doc.other_file_paths == []
    # 담당자 실명/전화번호는 수집 대상이 아니므로 Document 어디에도 남지 않아야 한다.
    assert "허원행" not in (doc.body_text or "")
    assert "044-201-3893" not in (doc.body_text or "")


def test_parse_detail_without_attachments_has_no_files(monkeypatch, tmp_path):
    monkeypatch.setattr(molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_NO_FILES)

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("첨부파일이 없는데 다운로드가 호출됨")

    monkeypatch.setattr(molit, "_download_file_streaming", _fail_if_called)

    adapter = MolitAdapter(files_root=tmp_path)
    raw = {
        "_item_id": "4898",
        "_title": "감항성개선지시 기술검토서",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=4898",
        "_category": "항공>항공안전정책",
        "_list_date": "2026-06-25",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path is None
    assert doc.other_file_paths == []
    assert doc.department == "항공기술과"


def test_parse_detail_multi_file_picks_primary_by_title_similarity(monkeypatch, tmp_path):
    monkeypatch.setattr(molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_MULTI_FILE)
    monkeypatch.setattr(
        molit, "_download_file_streaming", lambda url, dest: dest.write_bytes(b"x")
    )

    adapter = MolitAdapter(files_root=tmp_path)
    raw = {
        "_item_id": "4892",
        "_title": "신안산선 복선전철 제5-2공구 건설공사 터널붕괴사고 건설사고조사위원회 조사결과 보고서",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=4892",
        "_category": "건설>기술안전",
        "_list_date": "2026-05-14",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path == str(
        Path(SOURCE_MOLIT) / DOC_TYPE_POLICY_MATERIAL / "1-500"
        / "4892_1_신안산선 5-2공구 터널붕괴사고 사고조사보고서.pdf"
    )
    assert doc.other_file_paths == [
        str(Path(SOURCE_MOLIT) / DOC_TYPE_POLICY_MATERIAL / "1-500"
            / "4892_부록-1_경기광명 신안산선 터널붕괴사고 원인규명을 위한 구조해석_최종보고서.pdf")
    ]


def test_download_files_false_skips_network_download(monkeypatch, tmp_path):
    monkeypatch.setattr(molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_SINGLE_FILE)

    def _fail_if_called(url: str, dest: Path) -> None:
        raise AssertionError("download_files=False인데 다운로드가 호출됨")

    monkeypatch.setattr(molit, "_download_file_streaming", _fail_if_called)

    adapter = MolitAdapter(files_root=tmp_path)
    raw = {
        "_item_id": "4901",
        "_title": "2차로형 회전교차로 설치 및 개선 가이드라인",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=4901",
        "_category": "도로철도>도로정책",
        "_list_date": "2026-07-07",
    }
    detail = adapter.parse_detail(raw, download_files=False)
    doc = adapter.to_schema(detail)
    assert doc.body_file_path is None
    assert list(tmp_path.iterdir()) == []


def test_parse_detail_recovers_filename_when_source_html_truncates_it(monkeypatch, tmp_path):
    """실사로 실제 발견한 버그(2026-07-09 라이브 스모크 테스트, id=4899): 사이트
    HTML 결함으로 FileName이 "("에서 잘려 저장 파일명이 확장자 없는 "4899_("가
    되던 문제. FilePath의 서버 생성 파일명(항상 .pdf 확장자 포함)으로 대체돼야 한다."""
    monkeypatch.setattr(
        molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_BROKEN_FILENAME_ATTR
    )
    monkeypatch.setattr(
        molit, "_download_file_streaming", lambda url, dest: dest.write_bytes(b"x")
    )

    adapter = MolitAdapter(files_root=tmp_path)
    raw = {
        "_item_id": "4899",
        "_title": "전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=4899",
        "_category": "항공>항공안전정책",
        "_list_date": "2026-07-01",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.body_file_path == str(
        Path(SOURCE_MOLIT) / DOC_TYPE_POLICY_MATERIAL / "1-500" / "4899_20260701_131225_903.pdf"
    )
    assert doc.body_file_path.endswith(".pdf")


def test_meeting_minutes_get_separate_doc_type_and_directory(monkeypatch, tmp_path):
    """제목에 "회의록"이 있으면 doc_type이 policy_material이 아니라
    meeting_minutes로 분리돼야 하고, 저장 폴더도 source/doc_type 기준이라
    자동으로 별도 디렉토리에 쌓여야 한다(2026-07-09 사용자 요청)."""
    monkeypatch.setattr(
        molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_MEETING_MINUTES
    )
    monkeypatch.setattr(
        molit, "_download_file_streaming", lambda url, dest: dest.write_bytes(b"x")
    )

    adapter = MolitAdapter(files_root=tmp_path)
    raw = {
        "_item_id": "5001",
        "_title": "2026년 제3차 도로정책심의위원회 회의록",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=5001",
        "_category": "도로철도>도로정책",
        "_list_date": "2026-06-20",
    }
    detail = adapter.parse_detail(raw)
    doc = adapter.to_schema(detail)

    assert doc.doc_type == DOC_TYPE_MEETING_MINUTES
    assert doc.body_file_path == str(
        Path(SOURCE_MOLIT) / DOC_TYPE_MEETING_MINUTES / "1-500"
        / "5001_2026년 제3차 도로정책심의위원회 회의록.pdf"
    )
    assert (tmp_path / SOURCE_MOLIT / DOC_TYPE_MEETING_MINUTES).is_dir()
    assert not (tmp_path / SOURCE_MOLIT / DOC_TYPE_POLICY_MATERIAL).exists()


def test_infer_doc_type_only_matches_meeting_minutes_keyword():
    assert molit._infer_doc_type("2026년 제3차 도로정책심의위원회 회의록") == DOC_TYPE_MEETING_MINUTES
    assert molit._infer_doc_type("2차로형 회전교차로 설치 및 개선 가이드라인") == DOC_TYPE_POLICY_MATERIAL


def test_parse_detail_raises_on_page_moved_response(monkeypatch):
    """실사로 확인된 edge case(잘못된/만료된 id 접근 시 "페이지 이동중" 응답)는
    조용히 빈 문서를 만들지 않고 예외를 던져 호출부가 quarantine 처리하게 해야
    한다 — 다른 어댑터와 동일한 원칙."""
    monkeypatch.setattr(molit, "_fetch_detail_html", lambda item_id: SAMPLE_DETAIL_HTML_PAGE_MOVED)
    adapter = MolitAdapter()
    raw = {
        "_item_id": "9999",
        "_title": "존재하지 않는 게시물",
        "_detail_url": "https://www.molit.go.kr/USR/policyData/m_34681/dtl.jsp?id=9999",
        "_category": None,
        "_list_date": None,
    }
    with pytest.raises(ValueError, match="상세페이지 파싱 실패"):
        adapter.parse_detail(raw)
