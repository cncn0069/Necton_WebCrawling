"""여러 O트랙 소스가 공통으로 겪는 두 가지 문제를 gstack 헤드리스 브라우저(browse)
서브프로세스로 우회한다:

1. 정보공개포털: 목록 조회 API가 순수 HTTP 클라이언트를 봇으로 차단하고, 상세페이지의
   공개여부는 JS가 AJAX 응답으로 채워 넣어 정적 HTML로는 못 본다.
2. PRISM: React SPA라 목록의 각 행 링크가 전부 href="javascript:void(0)"이고
   DOM에 항목 ID를 담은 data-* 속성도 없다(실사로 확인, 2026-07-07) — 실제 상세페이지
   URL을 알아내려면 행을 클릭해야 한다. 페이지네이션도 URL 쿼리파라미터가 아니라
   클라이언트 상태(스핀버튼+이동 버튼)라 이것도 클릭으로만 넘길 수 있다.

읽는 순서 제안: _run_browse (모든 호출의 기반) → fetch_list_page/fetch_rendered_detail_html
(정보공개포털) → fetch_prism_list_page/fetch_prism_detail_url (PRISM 목록·상세) →
probe_and_download_prism_files (PRISM 파일 다운로드). PRISM 관련 함수를 읽을 때는
fetch_prism_detail_url의 docstring에 있는 "브라우저 상태 공유" 계약을 먼저 이해하고
가는 게 순서가 헷갈리지 않는다(ARCHITECTURE.md 참고).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_BROWSE_DIR = Path.home() / ".claude" / "skills" / "gstack" / "browse" / "dist"
_BROWSE_CANDIDATES = [_BROWSE_DIR / "browse", _BROWSE_DIR / "browse.exe"]

# 아래 타임아웃 상수들은 전부 기존 코드에 흩어져 있던 매직넘버를 이름만 붙인 것으로,
# 값 자체는 하나도 바꾸지 않았다(동작 보존). 같은 숫자라도 의미가 다르면 일부러
# 별도 상수로 분리했다 — 예: _NETWORKIDLE_TIMEOUT과 _JS_ACTION_TIMEOUT은 둘 다 15.0초지만
# 하나는 "네트워크 유휴 대기", 하나는 "클릭 시뮬레이션 JS 실행"이라 의미가 다르다.
_GOTO_TIMEOUT = 60.0  # goto()로 페이지 최초 진입할 때
_NETWORKIDLE_TIMEOUT = 15.0  # wait --networkidle
_HTML_CAPTURE_TIMEOUT = 30.0  # 렌더링된 HTML 전체를 읽어올 때
_JS_FETCH_TIMEOUT = 30.0  # js 안에서 실제 fetch() 네트워크 요청을 보낼 때
_JS_ACTION_TIMEOUT = 15.0  # js로 클릭/입력값 변경 등 UI 조작을 시뮬레이션할 때
_QUICK_JS_TIMEOUT = 10.0  # 짧게 끝나는 js(현재 url 읽기, 훅 설치, 상태 확인, 다운로드 링크 클릭 등)
_FILE_BYTES_TIMEOUT = 90.0  # 파일 바이트 전체를 base64로 받아올 때(용량이 큼)
_BACK_TIMEOUT = 30.0  # 브라우저 back()
_ROW_RENDER_TIMEOUT = 15.0  # PRISM 목록 행이 React로 다시 그려질 때까지 폴링 대기
_ROW_RENDER_BUFFER = 5.0  # 위 대기시간에 여유로 더하는 값(js 호출 자체의 오버헤드용)


def _browse_binary() -> str:
    for candidate in _BROWSE_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    found = shutil.which("browse") or shutil.which("browse.exe")
    if found:
        return found
    raise RuntimeError(
        "gstack browse binary not found — required to bypass open.go.kr's "
        "bot detection on the list AJAX endpoint. See adapters/browse_client.py."
    )


def _run_browse(*args: str, timeout: float = 30.0) -> str:
    result = subprocess.run(
        [_browse_binary(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"browse {args} failed: {result.stderr or result.stdout}")
    return result.stdout


_LIST_URL = "https://www.open.go.kr/othicInfo/infoList/infoList.do"
_NAVIGATED = False


def _ensure_navigated() -> None:
    global _NAVIGATED
    if not _NAVIGATED:
        _run_browse("goto", _LIST_URL, timeout=_GOTO_TIMEOUT)
        _NAVIGATED = True


def fetch_list_page(
    *, start_date: str, end_date: str, view_page: int, row_page: int
) -> dict:
    """정보공개포털 목록 AJAX를 헤드리스 브라우저 오리진에서 fetch()로 호출한다."""
    _ensure_navigated()
    body = (
        f"kwd=&preKwds=&reSrchFlag=off&othbcSeCd=&insttSeCd=&eduYn=N"
        f"&startDate={start_date}&endDate={end_date}&insttCdNm=&insttCd="
        f"&searchMainYn=&viewPage={view_page}&rowPage={row_page}&sort=s"
        f"&url=%2FothicInfo%2FinfoList%2FinfoList.ajax&callBackFn=searchFn_callBack"
    )
    js_expr = (
        "fetch('/othicInfo/infoList/infoList.ajax', {"
        "method: 'POST',"
        "headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', "
        "'X-Requested-With': 'XMLHttpRequest'},"
        f"body: {json.dumps(body)}"
        "}).then(r => r.text())"
    )
    raw = _run_browse("js", js_expr, timeout=_JS_FETCH_TIMEOUT)
    raw = raw.strip()
    # browse CLI는 fetch().then(r => r.text())의 문자열 결과를 그대로 stdout에 출력한다
    # (JSON으로 한 번 더 감싸지 않음) — 응답 원문 JSON을 바로 파싱한다.
    return json.loads(raw)


_UNTRUSTED_BEGIN_RE = re.compile(r"\A--- BEGIN UNTRUSTED EXTERNAL CONTENT.*?---\n", re.DOTALL)
_UNTRUSTED_END_RE = re.compile(r"\n--- END UNTRUSTED EXTERNAL CONTENT ---\s*\Z")


def _strip_untrusted_wrapper(raw: str) -> str:
    """browse CLI는 외부 사이트에서 가져온 콘텐츠를
    '--- BEGIN/END UNTRUSTED EXTERNAL CONTENT ---'로 감싼다 — 파싱 전에 벗겨낸다."""
    raw = _UNTRUSTED_BEGIN_RE.sub("", raw, count=1)
    raw = _UNTRUSTED_END_RE.sub("", raw, count=1)
    return raw


def fetch_rendered_detail_html(url: str) -> str:
    """상세페이지의 공개여부(dlsrCdNm)는 정적 HTML에는 항상 빈 값으로 오고,
    페이지 로드 후 JS가 별도 AJAX 응답(openCateSearchVO.oppSeCd)으로 채워 넣는다.
    httpx(순수 HTTP)로는 이 값을 볼 수 없어 실제로는 비공개/부분공개인 문서도
    disclosure_status가 "공개"로 잘못 저장되는 버그가 있었다(2026-07-07 발견).
    목록 조회(fetch_list_page)와 동일하게 헤드리스 브라우저로 렌더링한 뒤의
    HTML을 반환해야 실제 값을 읽을 수 있다.

    PRISM 어댑터 실사 중 추가로 확인된 버그: goto()는 초기 HTML 응답만 기다리고
    React SPA의 데이터 API 호출+렌더링 완료는 안 기다린다 — networkidle 없이 바로
    html을 캡처하면 간헐적으로 아직 안 채워진 필드(예: 공개제한근거)를 빈 값으로
    읽어버린다(같은 항목인데도 타이밍에 따라 성공/실패가 갈렸음, 실사로 재현·확인)."""
    _run_browse("goto", url, timeout=_GOTO_TIMEOUT)
    _run_browse("wait", "--networkidle", timeout=_NETWORKIDLE_TIMEOUT)
    raw = _run_browse("html", timeout=_HTML_CAPTURE_TIMEOUT)
    return _strip_untrusted_wrapper(raw)


PRISM_LIST_URL = "https://www.prism.go.kr/homepage/asmt/list"


def _wait_for_row_count(*, min_count: int, timeout: float = _ROW_RENDER_TIMEOUT) -> None:
    """PRISM 목록 테이블에 최소 min_count개의 행이 렌더링될 때까지 폴링한다.
    networkidle만으로는 React 재렌더링 완료를 보장 못해(모듈 함수 독스트링 참고)
    JS 안에서 직접 폴링 루프를 돈다."""
    js_expr = (
        "(() => new Promise((resolve) => {"
        "const start = Date.now();"
        "const check = () => {"
        "const n = document.querySelectorAll('tbody tr .b_tit a.ellipsis').length;"
        f"if (n >= {min_count}) return resolve('OK');"
        f"if (Date.now() - start > {int(timeout * 1000)}) return resolve('TIMEOUT n=' + n);"
        "setTimeout(check, 200);"
        "};"
        "check();"
        "}))()"
    )
    result = _run_browse("js", js_expr, timeout=timeout + _ROW_RENDER_BUFFER).strip()
    if "OK" not in result:
        raise RuntimeError(f"PRISM 목록 재렌더링 대기 실패: {result!r} (min_count={min_count})")


def _paginate_to_prism_page(page_number: int) -> None:
    """이미 PRISM 목록 페이지에 있다고 가정하고, 스핀버튼+'이동' 버튼으로
    page_number 페이지로 이동한다(URL 쿼리파라미터로는 이동 불가 — 실사 확인)."""
    js_expr = (
        "(() => {"
        "const input = document.querySelector('input.curr_page');"
        "if (!input) return 'NO_PAGE_INPUT';"
        "const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        f"setter.call(input, '{page_number}');"
        "input.dispatchEvent(new Event('input', {bubbles: true}));"
        "input.dispatchEvent(new Event('change', {bubbles: true}));"
        "const goBtn = Array.from(document.querySelectorAll('a')).find(a => a.textContent.trim() === '이동');"
        "if (!goBtn) return 'NO_GO_BUTTON';"
        "goBtn.click();"
        "return 'OK';"
        "})()"
    )
    result = _run_browse("js", js_expr, timeout=_JS_ACTION_TIMEOUT).strip()
    if result not in ("OK", "'OK'", '"OK"'):
        raise RuntimeError(f"PRISM 페이지 이동 실패: {result!r} (page={page_number})")
    _run_browse("wait", "--networkidle", timeout=_NETWORKIDLE_TIMEOUT)


def fetch_prism_list_page(page_number: int) -> str:
    """PRISM 목록의 렌더링된 HTML을 반환한다. 페이지네이션은 URL 쿼리파라미터가
    아니라 클라이언트 상태(스핀버튼 값 + '이동' 버튼)로만 동작한다(실사로 확인) —
    직접 URL로 특정 페이지에 갈 수 없어 JS로 스핀버튼을 채우고 클릭을 시뮬레이션한다.

    실사로 확인된 버그(2026-07-07, 8,000건 규모 실제 크롤링 중 재현): 이 함수는
    이전에 "page_number>1이면 이미 목록 페이지에 있다"고 가정했는데, 실제 호출
    순서는 fetch_list()가 한 페이지의 행을 전부 클릭해 URL만 알아내고 나면(목록
    페이지에 남아있음), 그 URL들을 caller(parse_detail)가 하나씩 별도로 goto()해서
    상세페이지를 렌더링한다 — 이건 fetch_list 제너레이터가 yield로 일시정지된
    "사이"에 일어나는 일이라 이 함수는 전혀 모른다. 그래서 정확히 한 페이지 분량을
    다 처리하고 다음 페이지로 넘어가려 할 때마다, 실제로는 마지막 상세페이지에
    남아있는데 "목록 페이지에 있다"고 착각해 페이지네이션 입력창을 못 찾고
    NO_PAGE_INPUT으로 실패했다. 그래서 매번 목록 URL로 새로 goto한 뒤 필요하면
    페이지 이동을 적용하는, 이전 브라우저 상태에 의존하지 않는 방식으로 바꿨다."""
    _run_browse("goto", PRISM_LIST_URL, timeout=_GOTO_TIMEOUT)
    _run_browse("wait", "--networkidle", timeout=_NETWORKIDLE_TIMEOUT)
    if page_number > 1:
        _paginate_to_prism_page(page_number)
    raw = _run_browse("html", timeout=_HTML_CAPTURE_TIMEOUT)
    return _strip_untrusted_wrapper(raw)


def fetch_prism_detail_url(row_index: int, expected_row_count: int, page_number: int = 1) -> str:
    """PRISM 목록의 row_index번째(0-based) 행을 클릭해 실제 상세페이지 URL을 알아낸다.
    행 링크가 전부 href="javascript:void(0)"이고 DOM에 항목 ID가 없어(실사로 확인),
    클릭 전에는 URL을 알 방법이 없다. 클릭 후 브라우저 뒤로가기로 목록으로 복귀한다.

    실사로 확인된 버그 1: back() 직후 networkidle은 즉시 반환되지만(같은 페이지로
    돌아오는 건 새 네트워크 요청이 없는 클라이언트 상태 복원이라), React가 테이블을
    다시 그리는 건 그보다 늦게 끝난다. 처음엔 "row_index+1개만 기다리면 되겠지"라고
    했다가 실패했다 — row 1개만 렌더링된 중간 상태도 그 조건을 만족해버려서 row_index=1
    이후가 항상 ROW_NOT_FOUND였다. 그래서 호출부가 실제로 알고 있는 전체 페이지 행수
    (expected_row_count)가 다 찰 때까지 기다려야 한다.

    실사로 확인된 버그 2(2026-07-07, 20건 이상 수집 시도 중 발견): 페이지네이션은
    URL 변경이 없는 클라이언트 상태 전환이라 브라우저 히스토리에 새 항목을 안 남긴다
    — 그래서 2페이지 이상에서 상세페이지로 이동한 뒤 back()하면 "2페이지"가 아니라
    "최초 goto()한 1페이지"로 돌아간다. 이걸 모르면 row_index로는 여전히 클릭이
    "성공"하지만 실제로는 1페이지의(이미 수집된 적 있는) 엉뚱한 행을 클릭하게 되어
    제목은 2페이지 것인데 URL/파일은 1페이지 것과 섞이는 조용한 데이터 오염이 난다.
    page_number>1이면 back() 후 반드시 해당 페이지로 재이동해야 한다."""
    js_expr = (
        "(() => {"
        "const links = document.querySelectorAll('tbody tr .b_tit a.ellipsis');"
        f"const link = links[{row_index}];"
        "if (!link) return 'ROW_NOT_FOUND';"
        "link.click();"
        "return 'OK';"
        "})()"
    )
    result = _run_browse("js", js_expr, timeout=_JS_ACTION_TIMEOUT).strip()
    if result not in ("OK", "'OK'", '"OK"'):
        raise RuntimeError(f"PRISM 행 클릭 실패: {result!r} (row_index={row_index})")
    _run_browse("wait", "--networkidle", timeout=_NETWORKIDLE_TIMEOUT)
    url = _run_browse("url", timeout=_QUICK_JS_TIMEOUT).strip()
    _run_browse("back", timeout=_BACK_TIMEOUT)
    if page_number > 1:
        _paginate_to_prism_page(page_number)
    _wait_for_row_count(min_count=expected_row_count, timeout=_ROW_RENDER_TIMEOUT)
    return url


PRISM_API_BASE = "https://api.prism.go.kr/prism-be-asmt/v1"


def _unwrap_js_string(raw: str) -> str:
    """browse CLI가 JS 문자열 반환값을 따옴표로 감싸서 내보낼 때가 있어 한 겹 벗긴다."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def fetch_prism_file_list(asmt_id: str) -> list[dict]:
    """asmtId에 딸린 첨부/보고서 파일 목록(fileSn/fileTypeCd/fileNm/fileWkky/pdfTrsfYn)을
    가져온다. 이 API는 존재하는 파일 메타데이터(파일명 등)를 조회하는 용도로만 쓴다 —
    **이 목록에 있다고 실제로 다운로드해도 된다는 뜻이 아니다.** 실사(2026-07-07)로
    확인: 상세페이지의 진짜 "다운로드" 링크를 클릭해야만 사이트가 실제 허용 여부를
    판단한다(비공개 문서는 alert("비공개 연구보고서입니다.")로 막고 네트워크 요청
    자체를 안 보냄, 부분공개 문서는 일부 파일만 링크가 있고 그것만 정상 통과함).
    실제 다운로드 가능 여부는 `probe_and_download_prism_files()`의 클릭 검증 결과만
    신뢰한다."""
    js_expr = (
        f"fetch('{PRISM_API_BASE}/entire/info', {{"
        "method: 'POST', headers: {'Content-Type': 'application/json'}, "
        f"body: {json.dumps(json.dumps({'asmtId': asmt_id}))}"
        "}).then(r => r.text())"
    )
    raw = _run_browse("js", js_expr, timeout=_JS_FETCH_TIMEOUT)
    payload = json.loads(_unwrap_js_string(raw))
    return payload.get("resultData", {}).get("asmtFileList", [])


def fetch_prism_file_bytes(file_meta: dict) -> bytes:
    """PRISM 첨부/보고서 파일 1건을 받아온다. browse CLI의 stdout은 텍스트 채널이라
    바이너리를 직접 못 돌려주므로, 브라우저 안에서 base64 문자열로 바꿔 반환하고
    여기서 디코딩한다."""
    body = {
        "asmtId": file_meta["asmtId"],
        "fileTypeCd": file_meta["fileTypeCd"],
        "fileSn": file_meta["fileSn"],
        "fileWkky": file_meta["fileWkky"],
        "pdfTrsfYn": file_meta.get("pdfTrsfYn", "Y"),
    }
    js_expr = (
        f"fetch('{PRISM_API_BASE}/progress/download-file', {{"
        "method: 'POST', headers: {'Content-Type': 'application/json'}, "
        f"body: {json.dumps(json.dumps(body))}"
        "}).then(r => r.arrayBuffer()).then(buf => {"
        "const bytes = new Uint8Array(buf);"
        "let binary = '';"
        "const chunk = 8192;"
        "for (let i = 0; i < bytes.length; i += chunk) {"
        "binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));"
        "}"
        "return btoa(binary);"
        "})"
    )
    raw = _run_browse("js", js_expr, timeout=_FILE_BYTES_TIMEOUT)
    return base64.b64decode(_unwrap_js_string(raw))


def _install_prism_download_click_hooks() -> None:
    """window.alert()와 다운로드 XHR의 요청 바디를 가로채는 훅을 (한 번만) 설치한다.
    같은 페이지에서 여러 번 호출해도 안전(idempotent)."""
    js_expr = (
        "(() => {"
        "if (window.__rd2HooksInstalled) return 'ALREADY';"
        "window.__rd2HooksInstalled = true;"
        "window.__rd2AlertFired = false;"
        "window.__rd2LastXhrBody = null;"
        "const origAlert = window.alert;"
        "window.alert = function(msg) { window.__rd2AlertFired = true; return origAlert.call(window, msg); };"
        "const origOpen = XMLHttpRequest.prototype.open;"
        "const origSend = XMLHttpRequest.prototype.send;"
        "XMLHttpRequest.prototype.open = function(method, url) {"
        "this.__rd2IsDownload = typeof url === 'string' && url.includes('download-file');"
        "return origOpen.apply(this, arguments);"
        "};"
        "XMLHttpRequest.prototype.send = function(body) {"
        "if (this.__rd2IsDownload) { window.__rd2LastXhrBody = body; }"
        "return origSend.apply(this, arguments);"
        "};"
        "return 'OK';"
        "})()"
    )
    _run_browse("js", js_expr, timeout=_QUICK_JS_TIMEOUT)


def probe_and_download_prism_files(file_list: list[dict]) -> list[dict]:
    """현재 렌더링된 PRISM 상세페이지의 실제 "다운로드" 링크를 하나씩 진짜로 클릭해
    사이트가 차단하는지 확인하고, 차단되지 않는 파일만 다운로드해서 반환한다.

    비공개 문서는 클릭 시 alert("비공개 연구보고서입니다.")가 뜨고 네트워크 요청
    자체가 발생하지 않는다(2026-07-07 실사 확인) — 이 함수는 그 판단을 우회하지 않고
    그대로 따른다. file_list(fetch_prism_file_list 결과)는 클릭으로 알아낸 요청
    파라미터(fileTypeCd/fileSn/fileWkky)에 매칭되는 실제 파일명(fileNm)을 찾는
    용도로만 쓴다.

    반환값: 다운로드에 성공한 파일 메타데이터 dict 목록. 각 dict는 file_list의
    원본 항목에 `_raw_bytes` 키를 추가한 것.
    """
    _install_prism_download_click_hooks()
    count_raw = _run_browse(
        "js",
        "(() => Array.from(document.querySelectorAll('a'))"
        ".filter(a => a.textContent.trim() === '다운로드').length)()",
        timeout=_QUICK_JS_TIMEOUT,
    )
    count = int(_unwrap_js_string(count_raw))

    downloaded: list[dict] = []
    seen_match_ids: set[int] = set()
    for i in range(count):
        _run_browse(
            "js",
            "(() => { window.__rd2AlertFired = false; window.__rd2LastXhrBody = null; return 'OK'; })()",
            timeout=_QUICK_JS_TIMEOUT,
        )
        click_js = (
            "(() => {"
            "const links = Array.from(document.querySelectorAll('a'))"
            ".filter(a => a.textContent.trim() === '다운로드');"
            f"const link = links[{i}];"
            "if (!link) return 'NO_LINK';"
            "link.click();"
            "return 'OK';"
            "})()"
        )
        _run_browse("js", click_js, timeout=_QUICK_JS_TIMEOUT)
        _run_browse("wait", "--networkidle", timeout=_NETWORKIDLE_TIMEOUT)

        state_raw = _run_browse(
            "js",
            "(() => JSON.stringify({blocked: window.__rd2AlertFired, body: window.__rd2LastXhrBody}))()",
            timeout=_QUICK_JS_TIMEOUT,
        )
        state = json.loads(_unwrap_js_string(state_raw))
        if state.get("blocked") or not state.get("body"):
            continue

        req_body = json.loads(state["body"])
        match = next(
            (
                f
                for f in file_list
                if f.get("fileTypeCd") == req_body.get("fileTypeCd")
                and f.get("fileSn") == req_body.get("fileSn")
                and f.get("fileWkky") == req_body.get("fileWkky")
                and f.get("pdfTrsfYn") == req_body.get("pdfTrsfYn")
            ),
            None,
        )
        if match is None or id(match) in seen_match_ids:
            continue
        seen_match_ids.add(id(match))

        raw_bytes = fetch_prism_file_bytes(match)
        downloaded.append({**match, "_raw_bytes": raw_bytes})
    return downloaded
