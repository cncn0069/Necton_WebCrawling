"""선언된 C/S 전용 템플릿마다 검수용 합성 PDF 한 건을 생성한다.

이 스크립트는 원본 문서의 개인정보·기관명·본문을 복사하지 않는다. 각 샘플의
본문과 식별값은 모두 합성하고, 템플릿의 구조적 근거가 된 실제 원본 문서는
``TEMPLATE_SOURCE_MAP.md`` 및 출력 manifest에만 기록한다.

사용 예:
    .venv\\Scripts\\python.exe scripts\\generate_template_samples.py

기본 출력:
    output/pdf/template_samples/*.pdf
    output/pdf/template_samples/template_samples_manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


import random

from rd2.generators.agency_categories import get_agency_category  # noqa: E402
from rd2.generators.agency_resolver import (  # noqa: E402
    is_military_secret_agency,
    select_military_secret_grade,
    select_whitelisted_agency,
)
from rd2.generators.doc_templates import TEMPLATE_VARIANTS, find_template, validate_row  # noqa: E402
from rd2.generators.template_matrix import TEMPLATE_TARGETS  # noqa: E402
from rd2.generators.pdf_render import render_document_pdf  # noqa: E402
from rd2.generators.security_mark import (  # noqa: E402
    generate_classification_stamp,
    generate_military_secret_mark,
)
from rd2.storage.naming import (  # noqa: E402
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_BID_RENOTICE,
    DOC_TYPE_INTERPRETATION_COMPILATION,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_NOTICE,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_PRE_SPEC_NOTICE,
    DOC_TYPE_PRESS_RELEASE,
    DOC_TYPE_PUBLIC_OFFERING,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
)


_REPO_ROOT = Path(__file__).parent.parent


def _display_path(path: Path) -> Path | str:
    try:
        return path.relative_to(_REPO_ROOT)
    except ValueError:
        return path


@dataclass(frozen=True)
class SourceReference:
    """템플릿의 구조를 확인할 때 본 실제 원본 문서 한 건."""

    path: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "role": self.role}


@dataclass(frozen=True)
class TemplateSample:
    template_id: str
    filename: str
    row: dict[str, str | bool]
    sources: tuple[SourceReference, ...]
    provenance_level: str
    provenance_note: str


_OFFICIAL_FORM_SOURCE = SourceReference(
    path=(
        "data/orginl_info/personnel/"
        "DCT2A56AFE5393CFF372DBCA1E1C2796D78_9급 공채 임용예정자 실무수습 발령.pdf"
    ),
    role="표준 공문 상단·결재선·하단 블록의 구조적 근거",
)

_SOURCE_BY_DOC_TYPE: dict[str, tuple[SourceReference, ...]] = {
    DOC_TYPE_OFFICIAL_DOCUMENT: (
        SourceReference(
            path=(
                "data/orginl_info/official_document/1-500/"
                "B10CB261967523862000_결재문서본문.pdf"
            ),
            role="실제 내부결재 공문의 기관명·수신·제목·번호문단·결재선·시행정보 구조 참고",
        ),
    ),
    DOC_TYPE_POLICY_MATERIAL: (
        SourceReference(
            path=(
                "data/molit/policy_material/1-500/"
                "4879_20260202174102313_전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황.pdf"
            ),
            role="실제 정책 참고자료의 참고 라벨·제목 박스·작성부서·□/ㅇ/* 개요체 구조 참고",
        ),
    ),
    DOC_TYPE_REPORT: (
        SourceReference(
            path=(
                "data/orginl_info/report/1-500/"
                "B10CB261957374394000_결재문서본문.pdf"
            ),
            role="실제 결과보고 공문의 관련근거·보고문·가나다 계층·붙임 구조 참고",
        ),
    ),
    DOC_TYPE_MEETING_MINUTES: (
        SourceReference(
            path=(
                "data/molit/meeting_minutes/1-500/"
                "4888_2026년 제1회 수도권정비실무위원회 회의록_홈페이지 게시.hwpx"
            ),
            role="실제 회의록의 회의개요와 안건번호·안건명·논의내용·논의결과 표 구조 참고",
        ),
    ),
    DOC_TYPE_PLAN: (
        SourceReference(
            path=(
                "data/orginl_info/plan/1-500/"
                "B10CB261967585853000_결재문서본문.pdf"
            ),
            role="실제 운영계획 공문의 추진개요 표·세부계획·행정사항·붙임 구조 참고",
        ),
    ),
    DOC_TYPE_APPROVAL: (
        SourceReference(
            path=(
                "data/orginl_info/approval/1-500/"
                "B10CB261957405327000_결재문서본문.pdf"
            ),
            role="실제 승인요청 공문의 관련근거·요청문·붙임·미완료 결재선 구조 참고",
        ),
    ),
    DOC_TYPE_REPLY_NOTIFICATION: (
        SourceReference(
            path=(
                "data/orginl_info/approval/1-500/"
                "S10CB261958278805000_결재문서본문.pdf"
            ),
            role="실제 대외 통보 공문의 개인/기관 수신·안내문·조치표·발신명의 구조 참고",
        ),
    ),
    DOC_TYPE_PRESS_RELEASE: (
        SourceReference(
            path=(
                "data/korea_kr/press_release/"
                "156770636_★ (260712) 재정전략회의_보도자료_최종.pdf"
            ),
            role="실제 보도자료의 보도시점/배포일시 표기, 헤드라인+글머리 요약, "
            "말미 부처별 담당자 연락처 표 구조 참고",
        ),
    ),
    DOC_TYPE_NOTICE: (
        SourceReference(
            path="data/orginl_info/notice/1-500/B10CB261967477496000_결재문서본문.pdf",
            role="실제 공고 기안문의 기관명·수신·관련근거·붙임·결재선 구조 참고",
        ),
    ),
    DOC_TYPE_BID_RENOTICE: (
        SourceReference(
            path="data/mohw/bid_renotice/1-500/354304_입찰재공고서.hwp",
            role="실제 입찰 재공고서의 재공고 사유·공고사항·일정 표 구조 참고",
        ),
    ),
    DOC_TYPE_PUBLIC_OFFERING: (
        SourceReference(
            path=(
                "data/mohw/public_offering/1-500/"
                "1480268_2024년 장애인거주시설 인권실태조사 전문조사원 양성 및 실태조사 공고.pdf"
            ),
            role="실제 공모 공고문의 모집개요·신청자격·일정 구조 참고",
        ),
    ),
    DOC_TYPE_INTERPRETATION_COMPILATION: (
        SourceReference(
            path=(
                "data/moel_policy/interpretation_compilation/"
                "20260500911_'26년 폭염관련 산업안전보건규칙 질의회시집.pdf"
            ),
            role="실제 질의회시집의 질의/회시 문답 구조와 근거법령 표기 참고",
        ),
    ),
    DOC_TYPE_PRE_SPEC_NOTICE: (
        SourceReference(
            path=(
                "data/mohw/pre_spec_notice/1-500/"
                "1485351_250415_제안요청서(전자바우처 통합카드사업)_사전규격공개.pdf"
            ),
            role="실제 사전규격공개 제안요청서의 □/○ 개요체와 평가 배점표 구조 참고",
        ),
    ),
}

_SOURCE_PROVENANCE_NOTE = {
    doc_type: "같은 문서유형의 실제 원본에서 페이지 계층과 표·결재·하단 구조를 확인하고 합성 내용만 적용함."
    for doc_type in _SOURCE_BY_DOC_TYPE
}

_DOC_TYPE_TITLE = {
    DOC_TYPE_OFFICIAL_DOCUMENT: "업무 검토 공문",
    DOC_TYPE_POLICY_MATERIAL: "정책 검토 참고자료",
    DOC_TYPE_REPORT: "검토보고서",
    DOC_TYPE_MEETING_MINUTES: "실무회의 회의록",
    DOC_TYPE_PLAN: "추진계획(안)",
    DOC_TYPE_APPROVAL: "승인 검토(안)",
    DOC_TYPE_REPLY_NOTIFICATION: "검토결과 통보",
    DOC_TYPE_PRESS_RELEASE: "보도자료(안)",
    DOC_TYPE_NOTICE: "공고(안)",
    DOC_TYPE_BID_RENOTICE: "입찰 재공고(안)",
    DOC_TYPE_PUBLIC_OFFERING: "공모 공고(안)",
    DOC_TYPE_INTERPRETATION_COMPILATION: "질의회시 정비(안)",
    DOC_TYPE_PRE_SPEC_NOTICE: "사전규격공개(안)",
}


def _row(
    *,
    row_id: str,
    clause_no: str,
    doc_type: str,
    title: str,
    agency: str,
    department: str,
    body_text: str,
    reason: str,
    document_status: str,
    classification: str = "S",
    committee_name: str | None = None,
    session_kind: str | None = None,
    chair_role: str | None = None,
    member_role: str | None = None,
) -> dict[str, str | bool]:
    return {
        "row_id": row_id,
        "seed_type": "template_sample",
        "seed_prism_id": "",
        "clause_no": clause_no,
        "cso_classification": classification,
        "title": title,
        "ordering_agency": agency,
        "department": department,
        "unit_task": "C/S 문서 템플릿 검수",
        "production_date": "2026-07-16",
        "subject_category": "템플릿 검수용 합성 문서",
        "content_summary": "실제 원본의 형식 관례만 반영한 합성 검수 샘플",
        "non_disclosure_reason": reason,
        "body_text": body_text,
        "disclosure_status": "비공개",
        "document_status": document_status,
        "source": "synthetic-template-sample",
        "source_url": "",
        "doc_type": doc_type,
        "is_synthetic": True,
        "field_source": "all_fields=synthetic; provenance=template_source_map",
        "status": "ok",
        "model": "",
        "tokens_in": "",
        "tokens_out": "",
        "gen_time_s": "0",
        "sampling_seed": "20260716",
        "prompt_version": "template-samples-v1-20260716",
        # meeting_minutes 전용 — 국회본회의 회의록 셸(등록형 헤더·의사일정·발언자 태그)의
        # 위원회명·역할 라벨. 회의록이 아닌 doc_type에서는 pdf_render가 조용히 무시한다.
        "meeting_committee_name": committee_name or "",
        "meeting_session_kind": session_kind or "",
        "meeting_chair_role": chair_role or "",
        "meeting_member_role": member_role or "",
    }


SAMPLES: tuple[TemplateSample, ...] = (
    TemplateSample(
        template_id="T1-1", filename="T1-1_legal_confidential.pdf",
        row=_row(row_id="template-t1-1", clause_no="1", doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            title="법정 비공개 자료 열람 제한 검토", agency="국가정보원", department="법무담당관",
            body_text="관계 법률에 따라 비공개로 관리되는 자료의 보호 대상과 열람 제한 범위를 검토함.\n인가된 담당자만 업무상 필요한 범위에서 열람하도록 관리함.",
            reason="제9조 제1항 제1호: 관계 법률에 따른 비공개 정보", document_status="내부검토중", classification="C"),
        sources=(_OFFICIAL_FORM_SOURCE,), provenance_level="partial_structural_reference",
        provenance_note="표준 공문 구조를 바탕으로 법적 근거·보호대상·열람 제한 섹션을 합성함.",
    ),
    TemplateSample(
        template_id="T2-1", filename="T2-1_national_security.pdf",
        row=_row(row_id="template-t2-1", clause_no="2", doc_type=DOC_TYPE_POLICY_MATERIAL,
            title="대외 협력 현안 대응방향 검토", agency="국가정보원", department="국제협력과",
            body_text="대외 협력 현안의 대응 방향과 국가이익 보호 대상을 검토함.\n공개 시 협상 관계와 외교상 신뢰에 미칠 우려를 분석함.",
            reason="제9조 제1항 제2호: 외교관계의 중대한 이익 보호", document_status="내부검토중", classification="C"),
        sources=(_OFFICIAL_FORM_SOURCE,), provenance_level="partial_structural_reference",
        provenance_note="표준 공문 구조에 국가이익 보호 대상과 공개 시 우려 섹션을 합성함.",
    ),
    TemplateSample(
        template_id="T3-1", filename="T3-1_public_safety.pdf",
        row=_row(row_id="template-t3-1", clause_no="3", doc_type=DOC_TYPE_REPORT,
            title="다중이용시설 안전 취약요인 점검보고", agency="정부부처", department="안전점검과",
            body_text="다중이용시설의 보호 대상과 안전 취약요인을 점검함.\n세부 취약정보 공개가 국민의 생명·신체 보호에 미칠 위험과 제한 범위를 검토함.",
            reason="제9조 제1항 제3호: 국민의 생명·신체 보호 지장 우려", document_status="내부검토중", classification="C"),
        sources=(_OFFICIAL_FORM_SOURCE,), provenance_level="partial_structural_reference",
        provenance_note="표준 공문 구조에 보호 대상·위험 분석·공개 제한 범위 섹션을 합성함.",
    ),
    TemplateSample(
        template_id="T4-1", filename="T4-1_legal_proceeding.pdf",
        row=_row(row_id="template-t4-1", clause_no="4", doc_type=DOC_TYPE_MEETING_MINUTES,
            title="진행 중 사건 대응회의 자료", agency="검찰청", department="법무지원과",
            body_text="진행 중 사건 대응방안 논의\n공개 시 공정한 업무 수행에 영향을 줄 수 있는 정보의 범위를 논의함.",
            reason="제9조 제1항 제4호: 진행 중인 재판 관련 정보", document_status="결재진행중", classification="C",
            committee_name="검찰청 법무지원과 사건대응회의", session_kind="임시회의", chair_role="주재자", member_role="실무진"),
        sources=(_OFFICIAL_FORM_SOURCE,), provenance_level="partial_structural_reference",
        provenance_note="표준 공문 구조에 사건·절차 개요와 진행 상태·공개 제한 섹션을 합성함.",
    ),
    TemplateSample(
        template_id="T5-1",
        filename="T5-1_approval_pending.pdf",
        row=_row(
            row_id="template-t5-1",
            clause_no="5",
            doc_type=DOC_TYPE_APPROVAL,
            title="2026년도 중점사업 추진방안 검토(안)",
            agency="수원시청 기획조정실",
            department="기획조정과",
            body_text=(
                "2026년도 중점사업 추진방안에 대한 관계 부서 의견을 수렴하고자 함.\n"
                "협의기관 의견을 조회 중이며 회신 내용을 반영해 보완안을 작성할 예정임."
            ),
            reason="제9조 제1항 제5호: 내부 검토 및 관계 기관 협의 진행 중",
            document_status="타기관협의중",
        ),
        sources=(
            _OFFICIAL_FORM_SOURCE,
            SourceReference(
                path=(
                    "data/orginl_info/approval/"
                    "DCT121B6FD91677367CD89FFFFDC296B6E2_"
                    "2018년도 BDI 제9차 정기이사회 의결사항 승인 검토.hwp"
                ),
                role="승인 검토 문서의 업무 맥락 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="표준 공문·결재선과 승인 검토 문서의 구조만 참고했으며, 본문은 합성함.",
    ),
    TemplateSample(
        template_id="T5-2",
        filename="T5-2_audit_interim.pdf",
        row=_row(
            row_id="template-t5-2",
            clause_no="5",
            doc_type=DOC_TYPE_AUDIT_RESULT,
            title="2026년 종합감사 중간보고",
            agency="근로복지공단",
            department="감사실",
            body_text=(
                "2026년 연간 감사 기본계획에 따라 5개 지역본부의 복무·예산집행·계약업무 "
                "전반에 대한 종합감사를 실시하고 있음.\n"
                "2023년부터 2025년까지 3년간의 업무 처리 현황을 감사대상으로 하며, 직전 "
                "종합감사 지적사항 이행실태와 유사기관 감사사례 분석을 통해 도출한 착안사항을 "
                "중심으로 감사반 5명을 투입해 대상 지역본부를 순회하는 실지감사를 진행 중임.\n"
                "실지감사 종료 후 확인서를 송부해 지적사항에 대한 소명 기간을 부여할 예정임."
            ),
            reason="제9조 제1항 제5호: 감사 진행 중",
            document_status="감사진행중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/1_특별조사단_조사보고서_관련_추가_공개_파일196개/"
                    "2026년 동 종합감사 결과보고(공개용).pdf"
                ),
                role="감사실시개요(배경·목적·중점·대상·방법·결과처리)의 서술 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note=(
            "원본은 감사가 종결·확정된 결과보고이므로, 총괄표·지적사항·처분요구·재정조치 "
            "내역은 모두 제외하고 Ⅰ.감사실시개요 단계(실지감사 진행 중)만 재구성함."
        ),
    ),
    TemplateSample(
        template_id="T5-3",
        filename="T5-3_bid_review.pdf",
        row=_row(
            row_id="template-t5-3",
            clause_no="5",
            doc_type=DOC_TYPE_BID_NOTICE,
            title="전자 행정서비스 통합운영 장비 구매 입찰공고(안) 검토",
            agency="한국지능정보사회진흥원",
            department="운영지원과",
            body_text="전자 행정서비스 통합운영 장비 구매 입찰",
            reason="제9조 제1항 제5호: 입찰공고 게시 전 내부 검토 중",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/1_특별조사단_조사보고서_관련_추가_공개_파일196개/입찰공고.pdf",
                role="전자입찰공고의 건명·공고일·입찰서제출·개찰일시·금액 표 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note=(
            "실제 전자입찰공고의 개요 표·낙찰자 결정방법·참가자격 구조를 참고하되, "
            "공고 게시 전 내부검토(안) 단계이므로 일정은 모두 '예정'으로 표기함."
        ),
    ),
    TemplateSample(
        template_id="T5-4",
        filename="T5-4_personnel_evaluation.pdf",
        row=_row(
            row_id="template-t5-4",
            clause_no="5",
            doc_type=DOC_TYPE_PERSONNEL,
            title="2026년 하반기 승진후보군 평가계획(안)",
            agency="다솔시 인사위원회",
            department="총무과",
            body_text="승진후보군 평가 기준과 대상자 현황을 검토하고 있으며, 평가기간은 진행 중임.",
            reason="제9조 제1항 제5호: 인사 의사결정 과정 진행 중",
            document_status="결재진행중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/personnel/"
                    "DCT9604D4D0A1C41CA7F2CE539D3D65CFD2_5급 공무원 인사발령.pdf"
                ),
                role="소속·직급·성명 표와 결재선의 공문 구조 참고",
            ),
        ),
        provenance_level="partial_structural_reference",
        provenance_note=(
            "공개 가능한 실물 인사평가 원본은 없어서, 인사발령 문서의 표·결재선 관례를 "
            "참고해 평가 진행 중 상태로 설계함."
        ),
    ),
    TemplateSample(
        template_id="T5-5",
        filename="T5-5_meeting_pending.pdf",
        row=_row(
            row_id="template-t5-5",
            clause_no="5",
            doc_type=DOC_TYPE_MEETING_MINUTES,
            title="2026년 제2회 도시계획 실무위원회 심의 진행 현황",
            agency="새길시 도시계획위원회",
            department="도시계획과",
            body_text=(
                "도시계획 변경안 심의\n"
                "교통영향 검토자료 보완 필요 여부를 논의 중이며, 관계 기관 의견을 조회 중임."
            ),
            reason="제9조 제1항 제5호: 위원회 심의 및 관계 기관 협의 진행 중",
            document_status="타기관협의중",
            committee_name="새길시 도시계획위원회", session_kind="정례회의",
            chair_role="위원장", member_role="위원",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/molit/meeting_minutes/1-500/"
                    "4888_2026년 제1회 수도권정비실무위원회 회의록_홈페이지 게시.hwpx"
                ),
                role="회의개요·질문/답변·논의결과 표기 관례 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="원본은 공개된 종결 회의록이므로, 최종 의결 대신 <보류> 상태로 재구성함.",
    ),
    TemplateSample(
        template_id="T5-6",
        filename="T5-6_audit_meeting.pdf",
        row=_row(
            row_id="template-t5-6",
            clause_no="5",
            doc_type=DOC_TYPE_MEETING_MINUTES,
            title="2026년 종합감사 지적사항 심의 회의",
            agency="근로복지공단",
            department="감사실",
            body_text=(
                "2026년 종합감사 잠정 지적사항 심의\n"
                "실지감사 결과 도출된 잠정 지적사항별 소명 의견을 검토 중이며, 확정 전까지 "
                "지적사항 항목과 대상 부서명은 비공개로 관리함."
            ),
            reason="제9조 제1항 제5호: 감사 지적사항 확정 전 심의 진행 중",
            document_status="감사진행중",
            committee_name="근로복지공단 감사결과심의위원회", session_kind="임시회의",
            chair_role="위원장", member_role="위원",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/1_특별조사단_조사보고서_관련_추가_공개_파일196개/"
                    "2026년 동 종합감사 결과보고(공개용).pdf"
                ),
                role="지적사항 항목 구성과 소명 절차 서술 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note=(
            "원본의 지적사항·소명 절차 흐름을 참고하되, 확정된 지적사항 대신 심의 중인 "
            "잠정 지적사항으로 재구성함."
        ),
    ),
    TemplateSample(
        template_id="T5-7",
        filename="T5-7_audit_official_document.pdf",
        row=_row(
            row_id="template-t5-7",
            clause_no="5",
            doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            title="2026년 종합감사 실지감사 협조 요청",
            agency="근로복지공단",
            department="감사실",
            body_text=(
                "2026년 종합감사 실지감사 협조 요청\n"
                "5개 지역본부를 대상으로 진행 중인 실지감사 일정에 맞춰 관련 자료 제출과 "
                "현장 확인에 협조하여 주시기 바라며, 감사 종료 후 확인서를 통해 지적사항에 "
                "대한 소명 기회를 별도로 안내할 예정임."
            ),
            reason="제9조 제1항 제5호: 감사 진행 중 실지감사 협조 요청",
            document_status="감사진행중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/1_특별조사단_조사보고서_관련_추가_공개_파일196개/"
                    "2026년 동 종합감사 결과보고(공개용).pdf"
                ),
                role="감사배경·감사방법(시민감사관 참여, 실지감사 일정) 서술 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="원본의 감사방법·일정 서술을 협조 요청 공문 형식으로 재구성함.",
    ),
    TemplateSample(
        template_id="T5-8",
        filename="T5-8_audit_report.pdf",
        row=_row(
            row_id="template-t5-8",
            clause_no="5",
            doc_type=DOC_TYPE_REPORT,
            title="2026년 종합감사 진행상황 보고",
            agency="근로복지공단",
            department="감사실",
            body_text=(
                "2026년 종합감사 진행상황\n"
                "5개 지역본부 대상 실지감사를 완료하고 잠정 지적사항에 대한 수감 부서 소명을 "
                "받는 중임. 소명 검토가 끝나는 대로 지적사항과 처분요구(안)을 확정해 별도 "
                "보고할 예정임."
            ),
            reason="제9조 제1항 제5호: 감사 결과 확정 전 진행상황 보고",
            document_status="감사진행중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/1_특별조사단_조사보고서_관련_추가_공개_파일196개/"
                    "2026년 동 종합감사 결과보고(공개용).pdf"
                ),
                role="Ⅰ.감사실시개요~Ⅲ.감사결과 총평의 서술 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note=(
            "원본은 지적사항·재정조치까지 확정된 종결 보고서이므로, 총괄표·처분요구·재정조치 "
            "내역은 제외하고 실지감사 완료~소명 검토 단계까지만 재구성함."
        ),
    ),
    TemplateSample(
        template_id="T5-9",
        filename="T5-9_bid_contract_approval.pdf",
        row=_row(
            row_id="template-t5-9",
            clause_no="5",
            doc_type=DOC_TYPE_APPROVAL,
            title="가로등주 구매 계약 체결방법 결정 승인 요청",
            agency="한국토지주택공사",
            department="단지사업팀",
            body_text=(
                "가로등주 구매 계약 체결방법 결정 승인 요청\n"
                "제한경쟁 입찰과 수의계약 중 계약 체결방법을 검토하고 있으며, 계약심사협의회 "
                "심사를 거쳐 최종 방법을 확정할 예정임."
            ),
            reason="제9조 제1항 제5호: 계약 체결방법 결정 관련 내부 검토 및 승인 진행 중",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/approval/1-500/B10CB261957405327000_결재문서본문.pdf",
                role="승인 요청 공문의 관련근거·요청문·검토내용·처리계획·미완료 결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 승인 요청 공문 구조를 참고하고 사업명·검토내용은 합성함.",
    ),
    TemplateSample(
        template_id="T5-16",
        filename="T5-16_technology_development_report.pdf",
        row=_row(
            row_id="template-t5-16",
            clause_no="5",
            doc_type=DOC_TYPE_REPORT,
            title="차세대 물류로봇 실증 기술개발 연구",
            agency="한국지능정보사회진흥원",
            department="기술개발팀",
            body_text=(
                "차세대 물류로봇 실증 기술개발 연구\n"
                "로봇 제어 알고리즘의 실증 데이터 확보 및 성능 검증을 위한 실외 주행시험을 "
                "진행 중임.\n"
                "실증 결과에 따라 상용화 여부와 후속 정책 반영 방향을 검토할 예정임."
            ),
            reason="제9조 제1항 제5호: 기술개발 연구 진행 중, 활용결과 미확정",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/PRISM/research_report/"
                    "1051000-202300135_정책연구_활용결과_보고서.pdf"
                ),
                role="정책연구 활용결과 보고서(서식 8)의 항목 구성(연구기간·활용구분·연구목적·"
                "연구주요내용·활용결과) 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note=(
            "실제 서식의 표 구성을 그대로 참고하되, 연구가 아직 진행 중이므로 활용구분을 "
            "'미활용(진행중)'으로, 활용결과를 '연구 종료 후 별도 보고 예정'으로 재구성함."
        ),
    ),
    TemplateSample(
        template_id="T6-1",
        filename="T6-1_personnel_order.pdf",
        row=_row(
            row_id="template-t6-1",
            clause_no="6",
            doc_type=DOC_TYPE_PERSONNEL,
            title="2026년 하반기 5급 공무원 인사발령",
            agency="성남시청",
            department="총무과",
            body_text="인사발령 대상자 개인정보가 포함되어 있으며 비식별 처리 전 원본을 검토 중임.",
            reason="제9조 제1항 제6호: 개인정보 포함, 비식별 처리 진행 중",
            document_status="비식별처리중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/personnel/"
                    "DCT9604D4D0A1C41CA7F2CE539D3D65CFD2_5급 공무원 인사발령.pdf"
                ),
                role="인사발령 도입문·발령사항 표·결재선의 직접 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="원본의 개인·기관 식별값을 사용하지 않고 표의 모든 값은 합성함.",
    ),
    TemplateSample(
        template_id="T6-2",
        filename="T6-2_civil_reply.pdf",
        row=_row(
            row_id="template-t6-2",
            clause_no="6",
            doc_type=DOC_TYPE_REPLY_NOTIFICATION,
            title="생활소음 민원 사실관계 확인 안내",
            agency="고양시청",
            department="민원행정과",
            body_text=(
                "인근 공사장 생활소음 관련 민원을 접수하였음.\n"
                "현장점검과 사실관계 확인을 진행 중이며, 확인 내용을 바탕으로 검토할 예정임."
            ),
            reason="제9조 제1항 제6호: 민원인 개인정보 포함, 민원 처리 진행 중",
            document_status="민원처리중",
        ),
        sources=(_OFFICIAL_FORM_SOURCE,),
        provenance_level="partial_structural_reference",
        provenance_note=(
            "실물 민원회신 원본은 현재 수집되지 않아, 실물 표준 공문 형식과 일반 회신 관례를 "
            "조합해 설계함."
        ),
    ),
    TemplateSample(
        template_id="T6-3",
        filename="T6-3_personnel_pii_report.pdf",
        row=_row(
            row_id="template-t6-3",
            clause_no="6",
            doc_type=DOC_TYPE_REPORT,
            title="2026년 하반기 5급 채용후보자 인사검증 결과보고",
            agency="수원시청",
            department="총무과",
            body_text=(
                "2026년 하반기 5급 공채 채용후보자 인사검증 결과\n"
                "채용후보자 김도윤(연락처 010-2456-7813, 수원시 팔달구 거주)에 대해 경력조회 및 "
                "신원조회를 실시함.\n"
                "전 근무지(수원시청 세무과, 2019~2024)의 근무평정과 결격사유 조회 결과 특이사항이 "
                "없음을 확인함."
            ),
            reason="제9조 제1항 제6호: 채용후보자 개인정보 포함",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/report/1-500/"
                    "B10CB261957374394000_결재문서본문.pdf"
                ),
                role="결과보고 공문의 관련근거·보고문·가나다 계층·붙임 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 결과보고 구조를 참고하되, 채용후보자의 성명·연락처·주소는 합성함.",
    ),
    TemplateSample(
        template_id="T6-4",
        filename="T6-4_welfare_pii_official_document.pdf",
        row=_row(
            row_id="template-t6-4",
            clause_no="6",
            doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            title="2026년 긴급복지 지원대상자 명단 통보",
            agency="고양시청",
            department="복지정책과",
            body_text=(
                "2026년 긴급복지 지원대상자 명단 통보\n"
                "한도윤(고양시 일산동구 거주, 연락처 010-3312-5567) 등 지원대상자에 대한 생계·의료 "
                "긴급지원 결정 내역을 통보하니, 지원비 지급 및 사후관리에 참고하시기 바람."
            ),
            reason="제9조 제1항 제6호: 복지 지원대상자 개인정보 포함",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/official_document/1-500/B10CB261967523862000_결재문서본문.pdf",
                role="공문의 기관명·수신·제목·관련근거·붙임·결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 공문 구조를 참고하되, 지원대상자의 성명·연락처·주소는 합성함.",
    ),
    TemplateSample(
        template_id="T6-5",
        filename="T6-5_welfare_pii_approval.pdf",
        row=_row(
            row_id="template-t6-5",
            clause_no="6",
            doc_type=DOC_TYPE_APPROVAL,
            title="장애인활동지원 등급 재산정 승인 요청",
            agency="고양시청",
            department="장애인복지과",
            body_text=(
                "장애인활동지원 등급 재산정 승인 요청\n"
                "신청인 임가언(연락처 010-4487-2216, 고양시 일산동구 거주)에 대한 활동지원 등급 "
                "재산정 결과를 검토하였으며, 재산정 등급 적용에 대한 승인을 요청함."
            ),
            reason="제9조 제1항 제6호: 복지 신청인 개인정보 포함",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/approval/1-500/B10CB261957405327000_결재문서본문.pdf",
                role="승인 요청 공문의 관련근거·요청문·검토내용·결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 승인 요청 구조를 참고하되, 신청인의 성명·연락처·주소는 합성함.",
    ),
    TemplateSample(
        template_id="T6-6",
        filename="T6-6_subject_pii_meeting.pdf",
        row=_row(
            row_id="template-t6-6",
            clause_no="6",
            doc_type=DOC_TYPE_MEETING_MINUTES,
            title="민원 조사대상자 신원확인 심의",
            agency="성남시청",
            department="감사담당관",
            body_text=(
                "민원 조사대상자 신원확인 심의\n"
                "조사대상자 신우철(연락처 010-5521-8834, 성남시 분당구 거주)에 대한 민원 접수 "
                "경위와 조사 필요성을 논의 중이며, 신원정보는 조사 종료 시까지 외부에 공개하지 "
                "않음."
            ),
            reason="제9조 제1항 제6호: 조사대상자 개인정보 포함",
            document_status="내부검토중",
            committee_name="성남시청 민원조사심의회", session_kind="임시회의",
            chair_role="위원장", member_role="위원",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/molit/meeting_minutes/1-500/"
                    "4888_2026년 제1회 수도권정비실무위원회 회의록_홈페이지 게시.hwpx"
                ),
                role="회의개요·질문/답변·논의결과 표기 관례 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 회의록 구조를 참고하되, 조사대상자의 성명·연락처·주소는 합성함.",
    ),
    TemplateSample(
        template_id="T7-2",
        filename="T7-2_unit_price.pdf",
        row=_row(
            row_id="template-t7-2",
            clause_no="7",
            doc_type=DOC_TYPE_BID_NOTICE,
            title="해외자료 조사·수집 연구용역 계약금액 공개심사 건",
            agency="국립한글박물관",
            department="기획운영과",
            body_text="해외 소재 한글자료 조사·수집 연구 용역",
            reason="제9조 제1항 제7호: 계약상대자 계약금액·단가 등 영업상 비밀, 공개심사 중",
            document_status="공개심사중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/1_특별조사단_조사보고서_관련_추가_공개_파일196개/"
                    "1371000-202600007_용역계약서(재일 한인 한글자료 조사·수집(도쿄) 연구 용역) (2).pdf"
                ),
                role="용역계약서의 발주처·계약상대자·계약금액·계약기간 정보 블록과 첨부문서 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 용역계약서의 정보 블록 구조를 참고하되 계약상대자·금액·과업명은 합성함.",
    ),
    TemplateSample(
        template_id="T7-1",
        filename="T7-1_technology_patent_report.pdf",
        row=_row(
            row_id="template-t7-1",
            clause_no="7",
            doc_type=DOC_TYPE_REPORT,
            title="차세대 배터리 전해질 특허출원 기술검토 보고서",
            agency="한국에너지기술연구원",
            department="연구기획실",
            body_text=(
                "차세대 배터리 전해질 조성 기술 특허출원 검토\n"
                "출원 대상 기술은 리튬이온전지 전해질에 신규 첨가제(불소계 화합물)를 적용해 "
                "저온 방전 성능을 32% 개선한 조성비(전해질 대비 첨가제 2.5중량%)이며, 특허출원 "
                "전까지 조성비와 실험 데이터는 외부에 공개하지 않음."
            ),
            reason="제9조 제1항 제7호: 특허출원 전 기술 조성비·실험데이터 등 영업상 비밀",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/report/1-500/"
                    "B10CB261957374394000_결재문서본문.pdf"
                ),
                role="결과보고 공문의 관련근거·보고문·가나다 계층·붙임 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 결과보고 구조를 참고하되, 조성비·실험데이터 등 기술 내용은 합성함.",
    ),
    TemplateSample(
        template_id="T7-3",
        filename="T7-3_technology_patent_official_document.pdf",
        row=_row(
            row_id="template-t7-3",
            clause_no="7",
            doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            title="신약 후보물질 기술이전 협의 요청",
            agency="한국화학연구원",
            department="기술이전센터",
            body_text=(
                "신약 후보물질 KRICT-2201 기술이전 협의 요청\n"
                "후보물질 KRICT-2201(합성경로 및 활성 시험 데이터 포함)의 기술이전 조건을 협의"
                "하고자 하니 관련 자료 검토 후 회신 바람. 합성경로와 활성 시험 데이터는 특허출원 "
                "전까지 대외비로 관리함."
            ),
            reason="제9조 제1항 제7호: 특허출원 전 합성경로·활성데이터 등 영업상 비밀",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/official_document/1-500/B10CB261967523862000_결재문서본문.pdf",
                role="공문의 기관명·수신·제목·관련근거·붙임·결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 공문 구조를 참고하되, 후보물질명·합성경로 등 기술 내용은 합성함.",
    ),
    TemplateSample(
        template_id="T7-4",
        filename="T7-4_ma_terms_approval.pdf",
        row=_row(
            row_id="template-t7-4",
            clause_no="7",
            doc_type=DOC_TYPE_APPROVAL,
            title="OO정밀 지분 인수 협상조건 승인 요청",
            agency="한국산업은행",
            department="투자관리부",
            body_text=(
                "OO정밀 지분 인수 협상조건 승인 요청\n"
                "인수 대상 지분 34%, 주당 인수희망가 18,500원(총 인수금액 약 287억원)으로 협상 "
                "중이며, 상대방 측 최종 제시가 확인 후 이사회 승인을 요청할 예정임."
            ),
            reason="제9조 제1항 제7호: M&A 협상 중인 인수가격·지분율 등 영업상 비밀",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/approval/1-500/B10CB261957405327000_결재문서본문.pdf",
                role="승인 요청 공문의 관련근거·요청문·검토내용·결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 승인 요청 구조를 참고하되, 인수대상·가격·지분율은 합성함.",
    ),
    TemplateSample(
        template_id="T7-5",
        filename="T7-5_ma_terms_meeting.pdf",
        row=_row(
            row_id="template-t7-5",
            clause_no="7",
            doc_type=DOC_TYPE_MEETING_MINUTES,
            title="OO정밀 지분 인수 협상조건 심의",
            agency="한국산업은행",
            department="투자관리부",
            body_text=(
                "OO정밀 지분 인수 협상조건 심의\n"
                "인수 대상 지분 34%, 주당 희망가 18,500원 협상안을 검토 중이며, 상대방 제시 "
                "조건과의 차이를 논의함."
            ),
            reason="제9조 제1항 제7호: M&A 협상 중인 인수가격·지분율 등 영업상 비밀",
            document_status="내부검토중",
            committee_name="한국산업은행 M&A 검토위원회", session_kind="임시회의",
            chair_role="위원장", member_role="위원",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/molit/meeting_minutes/1-500/"
                    "4888_2026년 제1회 수도권정비실무위원회 회의록_홈페이지 게시.hwpx"
                ),
                role="회의개요·질문/답변·논의결과 표기 관례 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 회의록 구조를 참고하되, 인수대상·가격·지분율은 합성함.",
    ),
    TemplateSample(
        template_id="T7-6",
        filename="T7-6_security_diagnosis_report.pdf",
        row=_row(
            row_id="template-t7-6",
            clause_no="7",
            doc_type=DOC_TYPE_REPORT,
            title="공공시스템 보안취약점 진단 결과보고서",
            agency="한국인터넷진흥원",
            department="보안진단팀",
            body_text=(
                "OO시스템 모의해킹 진단 결과\n"
                "로그인 페이지에서 SQL 인젝션 취약점(위험도 상) 및 관리자 페이지 접근제어 미흡"
                "(내부망 IP 미검증)이 발견되어 시스템 운영기관에 조치를 요청함. 취약점 상세 "
                "내역과 공격 재현 절차는 조치 완료 전까지 비공개로 관리함."
            ),
            reason="제9조 제1항 제7호: 미조치 보안취약점 상세 내역, 공개 시 악용 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/report/1-500/"
                    "B10CB261957374394000_결재문서본문.pdf"
                ),
                role="결과보고 공문의 관련근거·보고문·가나다 계층·붙임 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 결과보고 구조를 참고하되, 취약점 상세 내역은 합성함.",
    ),
    TemplateSample(
        template_id="T7-7",
        filename="T7-7_business_strategy_policy_material.pdf",
        row=_row(
            row_id="template-t7-7",
            clause_no="7",
            doc_type=DOC_TYPE_POLICY_MATERIAL,
            title="2026년 동남아시아 신규 진출 전략(안)",
            agency="한국지능정보사회진흥원",
            department="해외사업처",
            body_text=(
                "2026년 동남아시아 신규 진출 전략(안)\n"
                "베트남·인도네시아 2개국을 우선 진출 대상으로 선정하고, 현지 합작법인 설립을 "
                "통한 시장 진입 방식을 검토함. 목표 시장점유율과 투자 규모는 이사회 의결 전까지 "
                "대외비로 관리함."
            ),
            reason="제9조 제1항 제7호: 이사회 의결 전 경영전략상 목표·투자규모 등 영업상 비밀",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/molit/policy_material/1-500/"
                    "4879_20260202174102313_전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황.pdf"
                ),
                role="정책 참고자료의 참고 라벨·제목 박스·작성부서·□/ㅇ/* 개요체 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 정책자료 구조를 참고하되, 진출 대상국·투자규모 등 내용은 합성함.",
    ),
    TemplateSample(
        template_id="T8-1",
        filename="T8-1_real_estate_speculation_plan.pdf",
        row=_row(
            row_id="template-t8-1",
            clause_no="8",
            doc_type=DOC_TYPE_PLAN,
            title="OO지구 공공주택지구 지정 추진계획(안)",
            agency="한국토지주택공사",
            department="택지개발과",
            body_text=(
                "OO지구 공공주택지구 지정 추진계획(안)\n"
                "OO시 OO동 일대 84만㎡를 신규 공공주택지구 후보지로 검토 중이며, 지구 지정 "
                "이전까지 후보지 위치와 면적은 대외비로 관리함. 지정 고시 전 정보가 유출될 경우 "
                "인근 토지에 대한 투기 수요를 유발할 우려가 있음."
            ),
            reason="제9조 제1항 제8호: 지구 지정 전 후보지 정보, 공개 시 투기 유발 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/plan/1-500/"
                    "B10CB261967585853000_결재문서본문.pdf"
                ),
                role="추진계획 공문의 관련근거·추진개요 표·세부계획·붙임 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 추진계획 공문 구조를 참고하되, 후보지 위치·면적은 합성함.",
    ),
    TemplateSample(
        template_id="T8-2",
        filename="T8-2_real_estate_speculation_policy_material.pdf",
        row=_row(
            row_id="template-t8-2",
            clause_no="8",
            doc_type=DOC_TYPE_POLICY_MATERIAL,
            title="2026년 신규 택지 후보지 선정기준 검토",
            agency="국토교통부",
            department="공공주택추진단",
            body_text=(
                "2026년 신규 택지 후보지 선정기준 검토\n"
                "교통접근성, 개발 가능면적, 기존 시가지와의 연계성을 기준으로 수도권 3개 권역의 "
                "후보지를 비교 검토함. 후보지 명단은 지구 지정 발표 전까지 비공개로 관리함."
            ),
            reason="제9조 제1항 제8호: 지구 지정 전 후보지 명단, 공개 시 투기 유발 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/molit/policy_material/1-500/"
                    "4879_20260202174102313_전문교육기관ㆍ항공훈련기관 지정ㆍ인가 및 안전관리 현황.pdf"
                ),
                role="정책 참고자료의 참고 라벨·제목 박스·작성부서·□/ㅇ/* 개요체 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 정책자료 구조를 참고하되, 후보지·선정기준 내용은 합성함.",
    ),
    TemplateSample(
        template_id="T8-3",
        filename="T8-3_real_estate_speculation_report.pdf",
        row=_row(
            row_id="template-t8-3",
            clause_no="8",
            doc_type=DOC_TYPE_REPORT,
            title="OO지구 택지개발 후보지 검토 결과보고",
            agency="한국토지주택공사",
            department="택지개발과",
            body_text=(
                "OO지구 택지개발 후보지 검토 결과\n"
                "OO시 OO동 일대를 유력 후보지로 검토하였으며, 토지주 동향과 지가 변동 추이를 "
                "모니터링 중임. 지구 지정 이전 후보지 정보가 공개되면 투기 수요 유발이 우려됨."
            ),
            reason="제9조 제1항 제8호: 지구 지정 전 후보지 정보, 공개 시 투기 유발 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/report/1-500/"
                    "B10CB261957374394000_결재문서본문.pdf"
                ),
                role="결과보고 공문의 관련근거·보고문·가나다 계층·붙임 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 결과보고 구조를 참고하되, 후보지 위치·지가 동향은 합성함.",
    ),
    TemplateSample(
        template_id="T8-4",
        filename="T8-4_real_estate_speculation_meeting.pdf",
        row=_row(
            row_id="template-t8-4",
            clause_no="8",
            doc_type=DOC_TYPE_MEETING_MINUTES,
            title="OO지구 택지개발 후보지 선정 심의",
            agency="국토교통부",
            department="공공주택추진단",
            body_text=(
                "OO지구 택지개발 후보지 선정 심의\n"
                "OO시 OO동 일대 84만㎡ 후보지에 대한 교통영향 및 환경성 검토 결과를 논의 중이며, "
                "후보지 명단은 지구 지정 발표 시까지 비공개로 관리함."
            ),
            reason="제9조 제1항 제8호: 지구 지정 전 후보지 정보, 공개 시 투기 유발 우려",
            document_status="내부검토중",
            committee_name="국토교통부 택지후보지선정위원회", session_kind="임시회의",
            chair_role="위원장", member_role="위원",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/molit/meeting_minutes/1-500/"
                    "4888_2026년 제1회 수도권정비실무위원회 회의록_홈페이지 게시.hwpx"
                ),
                role="회의개요·질문/답변·논의결과 표기 관례 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 회의록 구조를 참고하되, 후보지 위치·검토내용은 합성함.",
    ),
    TemplateSample(
        template_id="T8-5",
        filename="T8-5_cornering_official_document.pdf",
        row=_row(
            row_id="template-t8-5",
            clause_no="8",
            doc_type=DOC_TYPE_OFFICIAL_DOCUMENT,
            title="방역물자 매점매석 의심업체 조사 협조 요청",
            agency="식품의약품안전처",
            department="유통관리과",
            body_text=(
                "방역물자 매점매석 의심업체 조사 협조 요청\n"
                "OO물류센터에 보건용 마스크 120만매를 비정상적으로 비축한 정황이 확인되어 "
                "매점매석 여부를 조사 중이니 유통 현황 자료 제공에 협조하시기 바람. 조사 대상 "
                "업체명과 물량은 조사 종료 전까지 비공개로 관리함."
            ),
            reason="제9조 제1항 제8호: 매점매석 조사 진행 중인 업체명·물량, 공개 시 매석 이익 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/official_document/1-500/B10CB261967523862000_결재문서본문.pdf",
                role="공문의 기관명·수신·제목·관련근거·붙임·결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 공문 구조를 참고하되, 업체명·물량 등 내용은 합성함.",
    ),
    TemplateSample(
        template_id="T8-6",
        filename="T8-6_cornering_report.pdf",
        row=_row(
            row_id="template-t8-6",
            clause_no="8",
            doc_type=DOC_TYPE_REPORT,
            title="OO물류센터 마스크 매점매석 의심 조사 결과보고",
            agency="식품의약품안전처",
            department="유통관리과",
            body_text=(
                "OO물류센터 마스크 매점매석 의심 조사 결과\n"
                "보건용 마스크 120만매 비축 정황에 대해 입고·출고 기록을 대조 확인 중이며, "
                "매점매석 여부가 확정될 때까지 업체명과 조사 세부내용은 비공개로 관리함."
            ),
            reason="제9조 제1항 제8호: 매점매석 조사 진행 중인 업체명·물량, 공개 시 매석 이익 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/orginl_info/report/1-500/"
                    "B10CB261957374394000_결재문서본문.pdf"
                ),
                role="결과보고 공문의 관련근거·보고문·가나다 계층·붙임 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 결과보고 구조를 참고하되, 업체명·물량 등 내용은 합성함.",
    ),
    TemplateSample(
        template_id="T8-7",
        filename="T8-7_cornering_approval.pdf",
        row=_row(
            row_id="template-t8-7",
            clause_no="8",
            doc_type=DOC_TYPE_APPROVAL,
            title="매점매석 의심업체 현장조사 실시 승인 요청",
            agency="식품의약품안전처",
            department="유통관리과",
            body_text=(
                "매점매석 의심업체 현장조사 실시 승인 요청\n"
                "OO물류센터의 보건용 마스크 비정상 비축 정황에 대해 현장조사 실시를 승인 "
                "요청하며, 조사 대상 업체명과 물량은 조사 종료 전까지 비공개로 관리할 예정임."
            ),
            reason="제9조 제1항 제8호: 매점매석 조사 진행 중인 업체명·물량, 공개 시 매석 이익 우려",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path="data/orginl_info/approval/1-500/B10CB261957405327000_결재문서본문.pdf",
                role="승인 요청 공문의 관련근거·요청문·검토내용·결재선 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="실제 승인 요청 구조를 참고하되, 업체명·물량 등 내용은 합성함.",
    ),
)


def _expanded_samples() -> tuple[TemplateSample, ...]:
    """목표 템플릿마다 값이 다른 합성 검수 PDF 6건을 만든다."""
    base_by_id = {sample.template_id: sample for sample in SAMPLES}
    expanded: list[TemplateSample] = []
    for target in TEMPLATE_TARGETS:
        base = base_by_id.get(target.template_id)
        if base is None:
            classification = "C" if target.clause_no in {"1", "2", "3", "4"} else "S"
            # 2026-07-21 plan-eng-review: SAMPLES에 손으로 정의 안 된 템플릿 타겟은
            # 예전엔 가상 기관명("가온행정기관")을 썼다 — 실존하지 않는 이름이라 R3
            # 위반. select_whitelisted_agency는 1~4호는 조항별 화이트리스트에서,
            # 그 외/미매칭 조항은 실존하는 generic "정부부처"로 고른다. template_id로
            # 시드를 고정해 같은 템플릿은 재실행해도 같은 기관명이 나온다.
            fallback_agency, _logo = select_whitelisted_agency(
                target.clause_no, random.Random(target.template_id)
            )
            base = TemplateSample(
                template_id=target.template_id,
                filename="",
                row=_row(
                    row_id=f"template-{target.template_id.lower()}",
                    clause_no=target.clause_no,
                    doc_type=target.doc_type,
                    title=f"{target.subclause_label} {_DOC_TYPE_TITLE[target.doc_type]}",
                    agency=fallback_agency, department="업무담당과",
                    body_text=(
                        f"{target.subclause_label} 관련 검토 목적과 보호 대상을 확인함.\n"
                        "공개 제한 범위와 현재 처리 경과를 검토하고 후속 계획을 수립함."
                    ),
                    reason=f"제9조 제1항 제{target.clause_no}호: {target.subclause_label}",
                    document_status="내부검토중", classification=classification,
                ),
                sources=_SOURCE_BY_DOC_TYPE[target.doc_type],
                provenance_level="structural_reference",
                provenance_note=_SOURCE_PROVENANCE_NOTE[target.doc_type],
            )
        for index in range(1, target.expected_documents + 1):
            row = dict(base.row)
            row["row_id"] = f"template-{target.template_id.lower()}-{index:02d}"
            row["cso_subclause_key"] = target.subclause_key
            row["title"] = f"{base.row['title']} #{index}"
            expanded.append(TemplateSample(
                template_id=target.template_id,
                filename=f"{target.template_id}_{target.subclause_key}_{target.doc_type}_{index:02d}.pdf",
                row=row, sources=base.sources,
                provenance_level=base.provenance_level,
                provenance_note=base.provenance_note,
            ))
    return tuple(expanded)


ALL_SAMPLES = _expanded_samples()


def _verify_samples(*, require_source_files: bool = False) -> None:
    """선언 템플릿과 샘플/원본 경로가 1:1인지 생성 전에 검증한다."""
    declared_ids = {spec.template_id for spec in TEMPLATE_VARIANTS.values()}
    sample_ids = {sample.template_id for sample in ALL_SAMPLES}
    if declared_ids != sample_ids:
        missing = sorted(declared_ids - sample_ids)
        extra = sorted(sample_ids - declared_ids)
        raise RuntimeError(f"템플릿 샘플 선언 불일치: missing={missing}, extra={extra}")

    for sample in ALL_SAMPLES:
        spec = find_template(
            str(sample.row["clause_no"]), str(sample.row["doc_type"]),
            str(sample.row["cso_subclause_key"]),
        )
        if spec is None or spec.template_id != sample.template_id:
            raise RuntimeError(f"{sample.template_id}: 템플릿 조합을 찾을 수 없음")

        violations = validate_row(spec, sample.row)
        if violations:
            joined = "\n  ".join(violations)
            raise RuntimeError(f"{sample.template_id}: 샘플 검증 실패\n  {joined}")

        if require_source_files:
            for source in sample.sources:
                if not (_REPO_ROOT / source.path).is_file():
                    raise RuntimeError(f"{sample.template_id}: 원본 참조 파일 없음: {source.path}")


def _manifest_entry(sample: TemplateSample, output_path: Path) -> dict:
    row = sample.row
    return {
        "template_id": sample.template_id,
        "sample_pdf": output_path.name,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "bytes": output_path.stat().st_size,
        "clause_no": row["clause_no"],
        "doc_type": row["doc_type"],
        "document_status": row["document_status"],
        "title": row["title"],
        "provenance_level": sample.provenance_level,
        "provenance_note": sample.provenance_note,
        "source_documents": [source.as_dict() for source in sample.sources],
        "synthetic_content": True,
    }


def _selected_samples(samples_per_template: int | None) -> tuple[TemplateSample, ...]:
    if samples_per_template is None:
        return ALL_SAMPLES
    if samples_per_template < 1:
        raise ValueError("samples_per_template은 1 이상이어야 합니다")
    counts: dict[str, int] = {}
    selected: list[TemplateSample] = []
    for sample in ALL_SAMPLES:
        count = counts.get(sample.template_id, 0)
        if count < samples_per_template:
            selected.append(sample)
            counts[sample.template_id] = count + 1
    return tuple(selected)


def generate_samples(
    output_dir: Path,
    *,
    require_source_files: bool = False,
    samples_per_template: int | None = None,
) -> list[dict]:
    _verify_samples(require_source_files=require_source_files)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # C(기밀) 샘플에는 실제 파이프라인(generate_cs_pilot.py)과 동일하게 대외비
    # 분류 스탬프를 적용한다 — 2026-07-20 plan-eng-review에서 발견: 이 스크립트가
    # 여태 stamp_path를 안 넘겨서 T2-1 등 C 샘플에 마킹이 전혀 없었다(Approach D).
    stamp_path = output_dir / "_stamp_confidential.png"
    generate_classification_stamp(stamp_path, seed=20260716)

    # 국방부/국가정보원 샘플(T1-1, T2-1 등)은 "대외비" 대신 군사기밀 [별표 2] 등급
    # 마크를 쓴다(2026-07-21 사용자 결정) — 등급별 마크는 3종류뿐이라 재사용한다.
    # 등급은 template_id로 시드를 고정해 재실행해도 같은 마크가 나오게 한다.
    military_mark_cache: dict[str, Path] = {}

    def _military_mark_for_grade(grade: str) -> Path:
        cached = military_mark_cache.get(grade)
        if cached is not None:
            return cached
        mark_path = output_dir / f"_stamp_military_{grade}.png"
        generate_military_secret_mark(mark_path, grade, seed=20260716)
        military_mark_cache[grade] = mark_path
        return mark_path

    manifest: list[dict] = []
    for sample in _selected_samples(samples_per_template):
        output_path = output_dir / sample.filename
        agency = str(sample.row["ordering_agency"])
        category = get_agency_category(agency)
        is_confidential = str(sample.row.get("cso_classification") or "").upper() == "C"
        if is_confidential and is_military_secret_agency(agency):
            grade = select_military_secret_grade(random.Random(sample.template_id))
            military_mark = _military_mark_for_grade(grade)
            row_stamp_path, row_stamp_top_path = military_mark, military_mark
        else:
            row_stamp_path = stamp_path if is_confidential else None
            row_stamp_top_path = None
        render_document_pdf(
            sample.row, category, output_path,
            stamp_path=row_stamp_path, stamp_top_path=row_stamp_top_path,
        )

        if output_path.read_bytes()[:4] != b"%PDF":
            raise RuntimeError(f"{sample.template_id}: PDF 헤더 검증 실패: {output_path}")
        manifest.append(_manifest_entry(sample, output_path))
        print(f"[ok] {sample.template_id} -> {_display_path(output_path)}")

    manifest_path = output_dir / "template_samples_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[manifest] {_display_path(manifest_path)}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "output" / "pdf" / "template_samples"),
        help="PDF와 manifest를 저장할 디렉터리",
    )
    parser.add_argument(
        "--samples-per-template",
        type=int,
        default=None,
        help="템플릿별 생성할 샘플 수. 생략하면 목표 수량(각 6건)을 모두 생성",
    )
    parser.add_argument(
        "--verify-source-files",
        action="store_true",
        help="git에서 제외된 data/ 원본 참조 문서가 로컬에 모두 있는지도 검증",
    )
    args = parser.parse_args()
    generate_samples(
        Path(args.output_dir),
        require_source_files=args.verify_source_files,
        samples_per_template=args.samples_per_template,
    )


if __name__ == "__main__":
    main()
