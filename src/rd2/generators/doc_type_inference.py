"""C/S 합성 문서의 doc_type을 조항별로 추론한다 (레이아웃 다양성 확보용).

2026-07-14: C/S 트랙은 지금까지 doc_type이 전부 DOC_TYPE_SYNTHETIC_DOCUMENT
고정값이라 문서 형식에 아무 다양성이 없었다. 조항(clause_no)이 실제로
가리키는 업무 성격에 맞춰 storage/naming.py의 기존 DOC_TYPE_* 분류를
그대로 재사용해 추론한다 — 새 분류 체계를 만들지 않는다.

조항 5(감사·검사·입찰계약·기술개발·인사관리)는 하나의 조항이 여러 실제
문서 장르를 포괄하므로, 시나리오/요약 텍스트의 키워드로 세분화한다.
"""

from __future__ import annotations

from rd2.storage.naming import (
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_APPROVAL,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
)

_CLAUSE_DOC_TYPE: dict[str, str] = {
    "1": DOC_TYPE_OFFICIAL_DOCUMENT,
    "2": DOC_TYPE_POLICY_MATERIAL,
    "3": DOC_TYPE_REPORT,
    "4": DOC_TYPE_MEETING_MINUTES,
    # "5"는 _infer_clause5_doc_type()에서 키워드로 세분화한다.
    "7": DOC_TYPE_REPORT,
    "8": DOC_TYPE_PLAN,
}

_CLAUSE5_KEYWORD_MAP: tuple[tuple[str, str], ...] = (
    ("감사", DOC_TYPE_AUDIT_RESULT),
    ("검사", DOC_TYPE_AUDIT_RESULT),
    ("입찰", DOC_TYPE_BID_NOTICE),
    ("계약", DOC_TYPE_BID_NOTICE),
    ("인사", DOC_TYPE_PERSONNEL),
    # 회의/위원회 심의 계열 — T5-5 회의록 템플릿 대상 (2026-07-16 추가).
    # 입찰·인사보다 뒤에 둬서 "입찰 평가위원회"류는 기존 매핑을 유지한다.
    ("회의", DOC_TYPE_MEETING_MINUTES),
    ("위원회", DOC_TYPE_MEETING_MINUTES),
)

# 조항 7도 하나의 조항이 여러 장르(기술보고서 vs 단가·계약 문서)를 포괄한다 —
# 단가/입찰/계약/납품 키워드면 bid_notice(T7-2 단가표 템플릿 대상)로 세분화
# (2026-07-16 조합 템플릿 작업에서 추가).
_CLAUSE7_KEYWORD_MAP: tuple[tuple[str, str], ...] = (
    ("단가", DOC_TYPE_BID_NOTICE),
    ("입찰", DOC_TYPE_BID_NOTICE),
    ("계약", DOC_TYPE_BID_NOTICE),
    ("납품", DOC_TYPE_BID_NOTICE),
)

_CLAUSE6_KEYWORD_MAP: tuple[tuple[str, str], ...] = (
    # 승인·결재 문구를 복지 키워드보다 먼저 검사한다. 예: "복지급여 지급 승인"은
    # 일반 자료제출 공문이 아니라 결재문 템플릿이 맞다.
    ("승인", DOC_TYPE_APPROVAL),
    ("결재", DOC_TYPE_APPROVAL),
    ("민원 처리 결과", DOC_TYPE_REPLY_NOTIFICATION),
    ("민원 회신", DOC_TYPE_REPLY_NOTIFICATION),
    ("답변", DOC_TYPE_REPLY_NOTIFICATION),
    ("복지", DOC_TYPE_OFFICIAL_DOCUMENT),
    ("수급", DOC_TYPE_OFFICIAL_DOCUMENT),
    ("급여", DOC_TYPE_OFFICIAL_DOCUMENT),
    ("지원대상", DOC_TYPE_OFFICIAL_DOCUMENT),
    ("조사대상", DOC_TYPE_MEETING_MINUTES),
    ("피조사", DOC_TYPE_MEETING_MINUTES),
)


def _infer_by_keywords(
    keyword_map: tuple[tuple[str, str], ...], keyword_text: str, fallback: str
) -> str:
    for keyword, doc_type in keyword_map:
        if keyword in keyword_text:
            return doc_type
    return fallback


def infer_doc_type(clause_no: str, *, keyword_text: str = "") -> str:
    """조항 번호(+조항5/7의 경우 키워드 텍스트)로 storage/naming.py의 기존
    DOC_TYPE_* 값 중 하나를 고른다. 정의되지 않은 조항 번호는 공문으로 폴백."""
    if clause_no == "5":
        # "내부검토" 일반형 — 결재/승인 성격 문서로 폴백
        return _infer_by_keywords(_CLAUSE5_KEYWORD_MAP, keyword_text, DOC_TYPE_APPROVAL)
    if clause_no == "6":
        return _infer_by_keywords(_CLAUSE6_KEYWORD_MAP, keyword_text, DOC_TYPE_PERSONNEL)
    if clause_no == "7":
        return _infer_by_keywords(_CLAUSE7_KEYWORD_MAP, keyword_text, DOC_TYPE_REPORT)
    return _CLAUSE_DOC_TYPE.get(clause_no, DOC_TYPE_OFFICIAL_DOCUMENT)
