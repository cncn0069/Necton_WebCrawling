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

from _common import ensure_src_on_path

ensure_src_on_path()

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
            body_text="진행 중인 사건의 절차 현황과 대응 쟁점을 검토함.\n공개 시 공정한 업무 수행에 영향을 줄 수 있는 정보의 범위를 논의함.",
            reason="제9조 제1항 제4호: 진행 중인 재판 관련 정보", document_status="결재진행중", classification="C"),
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
            title="2026년 상반기 복무점검 중간보고",
            agency="근로복지공단",
            department="감사실",
            body_text=(
                "상반기 복무점검 추진 현황을 중간 보고함.\n"
                "대상 부서 자료 징구 및 현장 확인을 진행 중이며, 확인 결과는 후속 검토 예정임."
            ),
            reason="제9조 제1항 제5호: 감사 진행 중",
            document_status="감사진행중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/alio/audit_result/"
                    "2021040202182090_101_2021년 연말연시 취약시기 복무점검 감사결과보고서.pdf"
                ),
                role="감사개요·진행 경과 중심의 보고서 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="원본은 종결된 감사결과보고서이므로, 결과·처분 항목을 제거하고 중간보고로 재구성함.",
    ),
    TemplateSample(
        template_id="T5-3",
        filename="T5-3_bid_review.pdf",
        row=_row(
            row_id="template-t5-3",
            clause_no="5",
            doc_type=DOC_TYPE_BID_NOTICE,
            title="전자 행정서비스 통합운영 사업 제안요청서(안) 검토",
            agency="한국지능정보사회진흥원",
            department="운영지원과",
            body_text="전자 행정서비스 통합운영 사업의 제안요청서와 평가기준을 검토 중임.",
            reason="제9조 제1항 제5호: 입찰공고 전 내부 검토 중",
            document_status="내부검토중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/mohw/pre_spec_notice/1-500/"
                    "1485351_250415_제안요청서(전자바우처 통합카드사업)_사전규격공개.pdf"
                ),
                role="제안요청서의 □/○ 개요체와 평가 배점표 구조 참고",
            ),
        ),
        provenance_level="structural_reference",
        provenance_note="사전규격공개 원본의 형식만 참고하고 사업명·평가내용은 합성함.",
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
        template_id="T7-2",
        filename="T7-2_unit_price.pdf",
        row=_row(
            row_id="template-t7-2",
            clause_no="7",
            doc_type=DOC_TYPE_BID_NOTICE,
            title="통합업무장비 납품단가 협상자료 공개심사 건",
            agency="조달청",
            department="장비구매과",
            body_text="협력업체 납품단가가 포함된 협상자료의 공개 범위를 심사 중임.",
            reason="제9조 제1항 제7호: 업체 납품단가 등 영업상 비밀, 공개심사 중",
            document_status="공개심사중",
        ),
        sources=(
            SourceReference(
                path=(
                    "data/mohw/bid_notice/1-500/"
                    "1485628_제안요청서(전자바우처 통합카드사업).pdf"
                ),
                role="조달·제안요청 문서의 표 구성과 공문 구조 참고",
            ),
        ),
        provenance_level="partial_structural_reference",
        provenance_note=(
            "실물 납품단가 원본은 보유하지 않아, 조달 문서 형식 관례를 참고해 단가표를 합성함."
        ),
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
