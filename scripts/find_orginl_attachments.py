"""원문정보(orginlInfoList) 게시판에 실제 첨부파일이 있는지 확인하는 파인더.

어댑터/저장소/conformance 없이 목록 API만 페이지네이션하며 ORGNAL_YN="Y"
(실제 원문파일 존재)인 항목의 ID/URL/파일명만 JSONL로 기록한다.

배경(2026-07-13): "정보목록"(infoList, open_go_kr.py가 긁는 게시판)은 실사 결과
첨부파일이 전혀 없음을 확인(100건 샘플 전부 ORGNAL_YN="N"). 별도 게시판인
"원문정보"에 실제 파일이 있을 것으로 추정했으나 이 역시 다운로더/어댑터를 먼저
만들기 전에 실존을 증명해야 한다는 지적(plan-eng-review, Codex 아웃사이드보이스
#1/#15)에 따라 이 스크립트를 먼저 만든다. 이 스크립트로 확인된 문서 하나를 갖고
상세페이지 다운로드 버튼을 실제로 클릭해 wonmun 체인의 진짜 네트워크 요청 구조를
관찰하는 게 다음 단계(TODOS.md "원문정보(orginlInfoList) 어댑터 + 파일 다운로드
체인" 항목 참고) — STEP1~4는 UI 라벨 텍스트로 지은 이름일 뿐 실제 요청 구조는
아직 모른다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.adapters import browse_client  # noqa: E402
from rd2.adapters.retry import with_retry  # noqa: E402

LIST_URL = "https://www.open.go.kr/othicInfo/infoList/orginlInfoList.do"
DETAIL_ENDPOINT = "https://www.open.go.kr/othicInfo/infoList/infoListDetl.do"


def fetch_orginl_list_page(*, start_date: str, end_date: str, view_page: int, row_page: int) -> dict:
    """원문정보 목록 AJAX를 헤드리스 브라우저 오리진에서 fetch()로 호출한다.

    open_go_kr.py의 fetch_list_page와 동일 패턴 — 엔드포인트만 다르다
    (infoList.ajax → orginlInfoList.ajax). 이 URL로 먼저 goto해야 같은
    오리진에서 fetch가 통과한다(정보목록과 동일한 봇탐지 우회 방식으로 실사 확인됨).
    """
    browse_client._run_browse("goto", LIST_URL, timeout=browse_client._GOTO_TIMEOUT)
    body = (
        f"kwd=&preKwds=&reSrchFlag=off&othbcSeCd=&insttSeCd=&eduYn=N"
        f"&startDate={start_date}&endDate={end_date}&insttCdNm=&insttCd="
        f"&searchMainYn=&viewPage={view_page}&rowPage={row_page}&sort=s"
        f"&url=%2FothicInfo%2FinfoList%2ForginlInfoList.ajax&callBackFn=searchFn_callBack"
    )
    js_expr = (
        "fetch('/othicInfo/infoList/orginlInfoList.ajax', {"
        "method: 'POST',"
        "headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', "
        "'X-Requested-With': 'XMLHttpRequest'},"
        f"body: {json.dumps(body)}"
        "}).then(r => r.text())"
    )
    raw = browse_client._run_browse("js", js_expr, timeout=browse_client._JS_FETCH_TIMEOUT)
    raw = browse_client._strip_untrusted_wrapper(raw.strip())
    return json.loads(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pages", type=int, help="조회할 페이지 수")
    parser.add_argument("--start-date", default="2013-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--row-page", type=int, default=100)
    parser.add_argument(
        "--out", default=None,
        help="결과 JSONL 경로 (기본: scripts/../orginl_attachments_found.jsonl)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    out_path = Path(args.out) if args.out else repo_root / "orginl_attachments_found.jsonl"

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date().strftime("%Y%m%d")
    end_date = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date()
        if args.end_date else date.today()
    ).strftime("%Y%m%d")

    print(f"Date range: {start_date} ~ {end_date}")
    print(f"Output: {out_path}")

    total_scanned = 0
    total_found = 0
    with out_path.open("a", encoding="utf-8") as out_f:
        for page in range(1, args.pages + 1):
            def _do_request(p: int = page) -> dict:
                return fetch_orginl_list_page(
                    start_date=start_date, end_date=end_date,
                    view_page=p, row_page=args.row_page,
                )

            payload = with_retry(_do_request)
            items = payload.get("result", {}).get("rtnList", [])
            if not items:
                print(f"page {page}: empty, stopping")
                break

            found_this_page = 0
            for item in items:
                total_scanned += 1
                if item.get("ORGNAL_YN") != "Y":
                    continue
                found_this_page += 1
                total_found += 1
                record = {
                    "prdn_nstt_regist_no": item.get("PRDCTN_INSTT_REGIST_NO"),
                    "prdn_dt": item.get("PRDCTN_DT"),
                    "instt_se_cd": item.get("INSTT_SE_CD"),
                    "title": item.get("INFO_SJ"),
                    "agency": item.get("PROC_INSTT_NM"),
                    "othbc_se_cd": item.get("OTHBC_SE_CD"),
                    "file_nm": item.get("FILE_NM"),
                    "detail_url": (
                        f"{DETAIL_ENDPOINT}?prdnNstRgstNo={item.get('PRDCTN_INSTT_REGIST_NO')}"
                        f"&prdnDt={item.get('PRDCTN_DT')}&nstSeCd={item.get('INSTT_SE_CD')}"
                    ),
                }
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()

            print(f"page {page}: {len(items)} scanned, {found_this_page} with ORGNAL_YN=Y")

    print()
    print(f"Total scanned: {total_scanned}, Total ORGNAL_YN=Y found: {total_found}")


if __name__ == "__main__":
    main()
