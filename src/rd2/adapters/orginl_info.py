"""정보공개포털(open.go.kr) "원문정보" 게시판 어댑터.

open_go_kr.py가 긁는 "정보목록"(infoList) 게시판과는 완전히 다른 게시판이다 —
2026-07-13 실사로 확인: 정보목록은 첨부파일이 전혀 없고(100건 샘플 전부
ORGNAL_YN="N"), 원문정보(orginlInfoList)엔 실제 첨부파일이 있다(2018년 250건
샘플 전부 ORGNAL_YN="Y"). 목록/상세 조회 방식은 open_go_kr.py와 같은 패턴
(browse_client 경유 — 목록 AJAX는 봇탐지, 상세페이지는 JS가 채우는 값이 있어
헤드리스 브라우저 필요)이지만, 실제 파일 다운로드는 wonmun 4단계 체인
(browse_client.fetch_orginl_file_bytes, TODOS.md "원문정보(orginlInfoList)
어댑터 + 파일 다운로드 체인" 항목에 실사 경위 전부 기록됨)을 추가로 탄다.

다운로드 정책(2026-07-13 plan-eng-review + Codex 교차검증 결정): disclosure_status
(공개/부분공개/비공개) 무관하게 파일이 실제 존재하면 수집한다 — 부분공개/비공개
문서 자체가 이미 사이트에서 필터링(wonmun STEP3 개인정보필터링)된 버전만
제공되므로 O/C/S 라벨과 내용 민감도 사이 모순이 없다(사용자 확인).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from rd2.adapters import browse_client
from rd2.adapters.base import DEFAULT_FILES_ROOT, SourceAdapter
from rd2.adapters.file_select import pick_primary_file
from rd2.adapters.open_go_kr import _infer_doc_type
from rd2.adapters.retry import with_retry
from rd2.schema.models import CsoClassification, DisclosureStatus, Document
from rd2.storage.files import save_body_file
from rd2.storage.naming import SOURCE_ORGINL_INFO

DETAIL_ENDPOINT = "https://www.open.go.kr/othicInfo/infoList/infoListDetl.do"

# open_go_kr.py의 _DISCLOSURE_TEXT_MAP과 동일 — 두 어댑터가 같은 사이트의
# 다른 게시판이라 겹치는 로직이지만, 지금은 복사해서 구현하고 실제 중복 규모를
# 본 뒤 리팩토링하기로 결정함(2026-07-13 plan-eng-review DRY 이슈 5번).
_DISCLOSURE_TEXT_MAP = {
    "공개": DisclosureStatus.OPEN,
    "부분공개": DisclosureStatus.PARTIAL,
    "비공개": DisclosureStatus.CLOSED,
}


class OriginalInfoAdapter(SourceAdapter):
    source_name = SOURCE_ORGINL_INFO

    def __init__(self, files_root: Path | None = None):
        self.files_root = files_root or DEFAULT_FILES_ROOT

    def fetch_list(
        self,
        *,
        start_date: date,
        end_date: date,
        max_items: int | None = None,
        row_page: int = 10,
        skip: int = 0,
    ) -> Iterator[dict]:
        """목록 조회 — ORGNAL_YN != "Y"(첨부파일 없음)인 항목은 상세페이지 조회
        없이 여기서 바로 건너뛴다. 이 게시판의 존재 이유가 "실제 파일이 있는
        문서"라서, 파일 없는 항목까지 detail/download 단계로 넘기는 건 낭비다."""
        page = (skip // row_page) + 1
        remaining_skip = skip % row_page
        yielded = 0
        while True:
            def _do_request(p: int = page) -> dict:
                return browse_client.fetch_orginl_list_page(
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    view_page=p,
                    row_page=row_page,
                )

            payload = with_retry(_do_request)
            items = payload.get("result", {}).get("rtnList", [])
            if not items:
                return
            if remaining_skip:
                items = items[remaining_skip:]
                remaining_skip = 0
            for item in items:
                if item.get("ORGNAL_YN") != "Y":
                    continue
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            page += 1

    def _download_and_save_files(self, oc: dict, title: str) -> tuple[str | None, list[str]]:
        """oc["fileList"]의 파일을 전부 wonmun 체인으로 실제 다운로드해서 저장한다.

        doc_type은 여기서 한 번만 계산해 save_body_file()과 to_schema()가 반드시
        같은 값을 쓰도록 한다 — 다른 값을 쓰면 폴더/DB가 어긋나는 사고가 난다
        (2026-07-09 mohw.py에서 실제로 겪은 사고, TODOS.md 기록 참고)."""
        file_list = oc.get("fileList", [])
        file_list = [f for f in file_list if f.get("fileId")]
        if not file_list:
            return None, []

        doc_type = _infer_doc_type(title)
        identifier = oc.get("prdnNstRgstNo", "")

        saved: list[tuple[str, dict]] = []
        for file_meta in file_list:
            is_pdf = "N" if file_meta["fileNm"].lower().endswith(".pdf") else "Y"

            def _do_download(fm: dict = file_meta, ip: str = is_pdf) -> tuple[bytes, str]:
                return browse_client.fetch_orginl_file_bytes(
                    oc=oc, file_id=fm["fileId"], esb_file_name=fm["fileNm"], is_pdf=ip
                )

            raw_bytes, _content_type = with_retry(_do_download)
            path = save_body_file(
                self.files_root, self.source_name, doc_type, identifier, file_meta["fileNm"], raw_bytes
            )
            saved.append((path, file_meta))

        primary_meta = pick_primary_file(file_list, title, filename_key="fileNm")
        body_file_path = next(path for path, fm in saved if fm is primary_meta)
        other_file_paths = [path for path, fm in saved if fm is not primary_meta]
        return body_file_path, other_file_paths

    def parse_detail(self, raw_item: dict, *, download_files: bool = True) -> dict:
        def _do_fetch() -> dict:
            return browse_client.fetch_orginl_detail_data(
                prdn_nstt_regist_no=raw_item["PRDCTN_INSTT_REGIST_NO"],
                prdn_dt=raw_item["PRDCTN_DT"],
                nst_se_cd=raw_item["INSTT_SE_CD"],
            )

        oc = with_retry(_do_fetch)
        title = oc.get("infoSj") or raw_item.get("INFO_SJ", "")

        detail = dict(raw_item)
        detail["_oc"] = oc
        detail["_title"] = title
        detail["_body_file_path"] = None
        detail["_other_file_paths"] = []
        if download_files:
            body_file_path, other_file_paths = self._download_and_save_files(oc, title)
            detail["_body_file_path"] = body_file_path
            detail["_other_file_paths"] = other_file_paths
        return detail

    def to_schema(self, enriched_item: dict) -> Document:
        oc = enriched_item["_oc"]
        title = enriched_item["_title"]

        disclosure_text = oc.get("dlsrCdNm")
        if not disclosure_text:
            raise ValueError("공개여부(dlsrCdNm)를 확인할 수 없음 — 공개로 간주하지 않고 격리 처리")
        if disclosure_text not in _DISCLOSURE_TEXT_MAP:
            raise ValueError(f"알 수 없는 공개여부 값: {disclosure_text!r}")
        disclosure_status = _DISCLOSURE_TEXT_MAP[disclosure_text]

        production_date = None
        prdn_dt = oc.get("prdnDt")
        if prdn_dt:
            try:
                production_date = datetime.strptime(prdn_dt[:8], "%Y%m%d").date()
            except ValueError:
                pass

        # open_go_kr.py와 동일한 근사 매핑 — 이 게시판도 상세페이지가 공개여부만
        # 제공하고 구체적 C/S 조항 근거는 제공하지 않는다.
        non_disclosure_reason = None
        if disclosure_status == DisclosureStatus.CLOSED:
            cso_classification = CsoClassification.C
            non_disclosure_reason = "비공개 — 구체적 법적 근거 조항은 이 소스(원문정보)에서 확인 불가"
        elif disclosure_status == DisclosureStatus.PARTIAL:
            cso_classification = CsoClassification.S
            non_disclosure_reason = "부분공개 — 구체적 법적 근거 조항은 이 소스(원문정보)에서 확인 불가"
        else:
            cso_classification = CsoClassification.O

        source_url = (
            f"{DETAIL_ENDPOINT}?prdnNstRgstNo={oc.get('prdnNstRgstNo', '')}"
            f"&prdnDt={oc.get('prdnDt', '')}&nstSeCd={oc.get('nstSeCd', '')}"
        )

        return Document(
            title=title,
            ordering_agency=oc.get("prcsNstNm") or "",
            department=oc.get("chrgDeptNm"),
            unit_task=oc.get("unitJobNm"),
            production_date=production_date,
            disclosure_status=disclosure_status,
            subject_category=oc.get("nstClNm"),
            content_summary=oc.get("docNo"),
            body_file_path=enriched_item.get("_body_file_path"),
            other_file_paths=enriched_item.get("_other_file_paths") or [],
            non_disclosure_reason=non_disclosure_reason,
            cso_classification=cso_classification,
            source=self.source_name,
            source_url=source_url,
            doc_type=_infer_doc_type(title),
            is_synthetic=False,
        )
