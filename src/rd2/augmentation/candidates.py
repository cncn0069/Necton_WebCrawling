"""annotate된 문서에서 조항별 또는 행정상태별 "기밀도 상승 후보" span을
정규식/키워드로 탐지한다.

`is_boilerplate=false`인 span만 검사 대상이다(annotate.py가 이미 표시해둔
반복 헤더·쪽번호는 애초에 후보가 될 수 없음 — annotate.py 참고). 원본 span은
여기서도 전혀 바꾸지 않는다 — 후보 목록만 뽑아낼 뿐이다.

제6호(개인정보)만 다른 조항과 메커니즘이 다르다: 실제 공개문서에는 진짜
개인정보가 거의 없으므로, "이미 있는 개인정보를 찾는" 게 아니라 "이 자리를
통째로 가상의 이름·직책·번호로 교체할 수 있는 자리표시 성격의 짧은 span"을
찾는다.

행정상태 후보는 제9조 조항과 별개다. 같은 문서가 제1~8호 어느 조항에
해당하더라도 ``document_status``를 함께 가질 수 있다. 상태 신호는 문서유형마다
다르게 잡는다. 다만 PDF 본문에 "붙임" 또는 "결재"가 있다고 해서 실제로
첨부가 누락됐거나 결재가 미완료됐다는 뜻은 아니므로, 후보에는 이후 메타데이터
확인에 필요한 ``verification_requirement``도 함께 기록한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_DIRECTIVE,
    DOC_TYPE_GUIDE,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_NOTIFICATION,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_REGULATION,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
    DOC_TYPE_RESEARCH_REPORT,
    DOC_TYPE_STATUS_REPORT,
)

# 5·7호는 금액 표현이 핵심 신호라 정규식으로 잡고, 조항별로 추가 키워드를 본다.
_MONEY_PATTERN = re.compile(r"\d[\d,]*\s*(원|만원|억원|백만원|천원)")

# 실측(2026-07-15)으로 지나치게 광범위한 단어를 걸러냄:
# - "평가"/"검토"(5호)는 학생평가·교원평가 등 조항과 무관한 내용을 대량으로 잡음
# - "수익"(7호)은 "수익자 부담"(교육사업 용어) 등 무관한 내용을 잡음
# - "개발"/"지정"/"고시"/"수용"/"지구"(8호)는 커리큘럼 개발·부서 지정처럼 부동산과
#   무관한 내용을 잡고, "지구"는 "복사지 구입"처럼 우연히 겹치는 오탐도 있었음
# → 부동산·감사계약 맥락이 뚜렷한 복합어 위주로 좁힘(문서 수 실측 후 충분함 확인).
#
# 지금까지는 moe/mohw/molit(중앙부처 위주) 문서만 수집돼 있지만, 앞으로 다양한
# 지자체·부처 문서가 들어올 걸 대비해 각 호 법조문이 열거하는 하위 개념(감사 종류,
# 계약 방식, 인사 행위, 부동산 규제 수단 등)의 실무 동의어를 넓혀둔다. 단, 위 교훈대로
# 단독으로 쓰이면 무관한 문맥을 대량으로 잡는 단어(평가/검토/수용/지정/개발 등)는
# 피하고, 그 자체로 조항 맥락이 뚜렷한 복합어 위주로만 추가한다.
#
# 실측(2026-07-15, 재확인)으로 새로 추가했다가 다시 뺀 단어: "감사원"(96건 —
# "국회·감사원 등 지적사항 반영" 식 상투구가 대부분, 실제 감사 진행 내용이 아님),
# "실태점검"(30건 — 교통안전 실태점검 등 5호와 무관한 일반 점검이 다수), "인사발령"
# (1건, 건강보험 안내문 문맥으로 완전히 무관), "특허"(137건 — 특허법무대학원·특허
# 표준선점 등 기술개발 서술이 대부분이라 "영업상 비밀"과 무관), "도시재생"(487건 —
# "도시재생뉴딜사업" 자체가 국토부 정책 전반에 만연한 일반 명칭이라 투기·매점매석과
# 무관한 정책 설명이 대부분), "용도변경"(130건 — 건축법상 리모델링·대수선 절차 용어로
# 부동산 투기 맥락과 무관한 경우가 대부분). 위 6개는 "개발"/"지정"과 같은 이유로 제외.
_CLAUSE_KEYWORDS: dict[str, list[str]] = {
    "5": [
        "계약", "입찰", "감사", "인사", "심사", "낙찰",
        # 감사·감독·검사 동의어(지자체 자체감사 등 중앙부처와 다른 용어 대응)
        "특정감사", "종합감사", "복무감사", "자체감사", "지도점검",
        # 입찰계약 방식 동의어(수의계약·공모형 계약은 "입찰" 키워드로 안 잡힘)
        "수의계약", "제안공모", "적격심사",
        # 인사관리 행위(승진·징계 등 인사 관련 의사결정)
        "인사위원회", "징계위원회", "승진심사",
        # 의사결정/내부검토 과정
        "내부검토", "검토보고",
    ],
    "7": [
        "원가", "단가", "영업", "매출", "계약금액", "낙찰가",
        # 영업·경영상 비밀 동의어
        "영업비밀", "기술이전", "로열티", "지식재산권",
        "원가구조", "납품단가", "수주금액", "용역대가",
    ],
    "8": [
        "택지", "공시지가", "용도지역", "택지개발", "재개발", "재건축", "도시개발",
        "토지수용", "그린벨트", "개발제한구역", "부동산", "산업단지", "지구단위계획",
        "택지지구", "개발지구",
        # 지자체별 정비·보상·인허가 용어(재개발·재건축 외에 정비구역 지정 방식이 다양함)
        "정비구역", "지목변경",
        "토지보상", "손실보상", "수용재결", "개발행위허가",
        "공공주택지구", "매립지", "간척지", "분양가상한제", "미분양",
    ],
}
_CLAUSE_6_LABEL_MAX_LEN = 40  # "라벨: 값" 패턴은 조금 더 길어도 허용
# "담당자 :", "문의:" 처럼 라벨 뒤에 콜론이 오는 명확한 자리표시 패턴만 인정.
# 지자체 서식(재산세·건축인허가 등)에는 "소유자/세대주/신청인/대표자/보호자" 같은
# moe/mohw/molit 코퍼스에 없던 라벨이 흔해서 함께 추가해둔다.
_CLAUSE_6_LABEL_PATTERN = re.compile(
    r"(담당자|문의|연락처|작성자|책임자|성명|주민등록번호|생년월일|주소|전화번호|"
    r"휴대전화|이메일|계좌번호|소유자|세대주|신청인|대표자|보호자)\s*[:：]"
)
# 실측(2026-07-15)으로 "라벨: 값"이 공무원 직위명과 함께 나오는 경우(예: "프로그램
# 성과 담당자 : 소은주 책임교육정책관")는 직무 수행 관련 공무원 성명이라 정보공개법
# 6호 비공개 사유로 보기 어렵다는 걸 확인해 제외한다. 반대로 직위명 없이 남는 라벨은
# 입찰참가자(사업자 대표), 외부 연구자, 민간기관 직원 등 사인(私人)의 정보인 경우가
# 많아(예: "작성책임자: 주국민은행일반사무관리부대리정은경") 후보로 남긴다.
# 지자체 문서에는 시장/군수/구청장/읍면동장/지방의회 의원 등 중앙부처에 없던 직위명이
# 나오므로 같은 이유로 제외 목록에 함께 넣는다. "의원"은 지방의회 의원과
# 병원(예: "내과의원")이 동음이의라 바로 걸면 의료기관 문맥까지 오탐하므로,
# 반드시 지역명이 붙는 "OO의원" 형태(시의원/도의원/구의원/군의원)로만 좁혀서 넣는다.
_CLAUSE_6_GOV_TITLE_PATTERN = re.compile(
    r"정책관|기획관|국장|과장|사무관|서기관|주무관|팀장|위원회|청장|실장|본부장|공무원|팀원|TF|"
    r"시장|군수|구청장|읍장|면장|동장|시의원|도의원|구의원|군의원|국회의원|조합장"
)

_MAX_CANDIDATES_PER_DOC = 20  # 문서 하나가 후보 풀을 독점하지 않게 하는 상한


@dataclass(frozen=True)
class AdministrativeStatusRule:
    """문서유형 하나에서 행정상태 가능성을 보여주는 본문 신호.

    ``verification_requirement``가 있으면 본문 신호만으로 확정하면 안 된다.
    예를 들어 "붙임"은 실제 첨부 존재 여부와 대조해야 ``첨부미등록``이 된다.
    """

    document_status: str
    keywords: tuple[str, ...]
    verification_requirement: str | None = None


# 문서유형별로 같은 행정상태라도 서로 다른 표현을 사용한다. 이 표는 "상태 확정"
# 규칙이 아니라 원본 형식 보존 재구성 단계에 보낼 후보를 고르는 규칙이다.
_ADMIN_STATUS_RULES_BY_DOC_TYPE: dict[str, tuple[AdministrativeStatusRule, ...]] = {
    DOC_TYPE_OFFICIAL_DOCUMENT: (
        AdministrativeStatusRule("결재진행중", ("결재 중", "검토 중", "기안"), "approval_history"),
        AdministrativeStatusRule("첨부미등록", ("붙임", "별첨", "첨부"), "attachment_inventory"),
        AdministrativeStatusRule("타기관협의중", ("관계기관", "관계 부처", "의견 조회", "협의 중")),
        AdministrativeStatusRule("공개심사중", ("정보공개", "공개 여부", "부분공개", "비공개")),
        AdministrativeStatusRule("시스템등록오류", ("전자문서", "시스템 등록", "등록 오류")),
    ),
    DOC_TYPE_APPROVAL: (
        AdministrativeStatusRule("결재진행중", ("결재 중", "결재상신", "기안"), "approval_history"),
        AdministrativeStatusRule("초안", ("초안", "검토안", "(안)")),
        AdministrativeStatusRule("내부검토중", ("내부 검토", "검토 중", "검토 의견")),
        AdministrativeStatusRule("타기관협의중", ("관계기관", "의견 조회", "협의 중")),
    ),
    DOC_TYPE_AUDIT_RESULT: (
        AdministrativeStatusRule("감사진행중", ("감사 진행", "감사 중", "조사 중", "점검 중")),
        AdministrativeStatusRule("결재진행중", ("검토 중", "결재 중"), "approval_history"),
        AdministrativeStatusRule("첨부미등록", ("붙임", "증빙자료", "별첨"), "attachment_inventory"),
    ),
    DOC_TYPE_BID_NOTICE: (
        AdministrativeStatusRule("내부검토중", ("사전규격", "제안요청", "검토안", "평가 기준(안)")),
        AdministrativeStatusRule("타기관협의중", ("의견수렴", "관계기관", "협의 중")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "제안요청서", "과업지시서"), "attachment_inventory"),
    ),
    DOC_TYPE_MEETING_MINUTES: (
        AdministrativeStatusRule("내부검토중", ("계속 심의", "보류", "추가 검토")),
        AdministrativeStatusRule("타기관협의중", ("관계기관", "의견 조회", "협의")),
        AdministrativeStatusRule("결재진행중", ("결재 중", "검토 중"), "approval_history"),
    ),
    DOC_TYPE_PERSONNEL: (
        AdministrativeStatusRule("결재진행중", ("인사(안)", "결재 중", "후보자"), "approval_history"),
        AdministrativeStatusRule("비식별처리중", ("성명", "주민등록번호", "연락처", "주소"), "pii_redaction_state"),
        AdministrativeStatusRule("초안", ("초안", "검토안", "(안)")),
    ),
    DOC_TYPE_REPLY_NOTIFICATION: (
        AdministrativeStatusRule("민원처리중", ("민원", "사실관계 확인", "현장 확인", "조사 중")),
        AdministrativeStatusRule("비식별처리중", ("성명", "연락처", "주소"), "pii_redaction_state"),
        AdministrativeStatusRule("공개심사중", ("정보공개", "공개 여부", "부분공개")),
    ),
    DOC_TYPE_PLAN: (
        AdministrativeStatusRule("공개예정일미도래", ("공개 예정", "시행 예정", "고시 예정")),
        AdministrativeStatusRule("초안", ("초안", "계획(안)", "검토안")),
        AdministrativeStatusRule("타기관협의중", ("의견수렴", "관계기관", "협의 중")),
    ),
    DOC_TYPE_REPORT: (
        AdministrativeStatusRule("감사진행중", ("중간보고", "조사 중", "감사 진행", "점검 중")),
        AdministrativeStatusRule("내부검토중", ("검토 중", "검토 의견", "검토안")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "별첨", "참고자료"), "attachment_inventory"),
    ),
    DOC_TYPE_POLICY_MATERIAL: (
        AdministrativeStatusRule("공개예정일미도래", ("시행 예정", "행정예고", "입법예고")),
        AdministrativeStatusRule("타기관협의중", ("의견수렴", "관계기관", "협의 중")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "별첨", "참조"), "attachment_inventory"),
    ),
    DOC_TYPE_NOTIFICATION: (
        AdministrativeStatusRule("공개예정일미도래", ("공개 예정", "시행 예정", "행정예고", "입법예고")),
        AdministrativeStatusRule("초안", ("초안", "고시안")),
        AdministrativeStatusRule("타기관협의중", ("의견수렴", "관계기관")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "별첨"), "attachment_inventory"),
    ),
    DOC_TYPE_DIRECTIVE: (
        AdministrativeStatusRule("공개예정일미도래", ("공개 예정", "시행 예정", "행정예고")),
        AdministrativeStatusRule("내부검토중", ("검토 중", "검토안", "초안")),
    ),
    DOC_TYPE_REGULATION: (
        AdministrativeStatusRule("공개예정일미도래", ("공개 예정", "시행 예정", "입법예고")),
        AdministrativeStatusRule("초안", ("초안", "예규안")),
        AdministrativeStatusRule("타기관협의중", ("의견수렴", "관계기관")),
    ),
    DOC_TYPE_RESEARCH_REPORT: (
        AdministrativeStatusRule("내부검토중", ("중간보고", "연구 진행", "검토 중")),
        AdministrativeStatusRule("타기관협의중", ("공동연구", "의견수렴", "협의 중")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "별첨", "부록"), "attachment_inventory"),
    ),
    DOC_TYPE_STATUS_REPORT: (
        AdministrativeStatusRule("감사진행중", ("조사 중", "점검 중", "잠정")),
        AdministrativeStatusRule("시스템등록오류", ("시스템", "등록 오류", "자료 오류")),
    ),
    DOC_TYPE_GUIDE: (
        AdministrativeStatusRule("공개예정일미도래", ("공개 예정", "시행 예정", "배포 예정")),
        AdministrativeStatusRule("문서정리중", ("정비 중", "폐기 예정", "중복")),
    ),
}


def _matches_clause_5_or_7_or_8(text: str, clause_no: str) -> bool:
    if clause_no in ("5", "7") and _MONEY_PATTERN.search(text):
        return True
    return any(kw in text for kw in _CLAUSE_KEYWORDS[clause_no])


def _matches_clause_6(text: str) -> bool:
    # 실측(2026-07-16)으로 발견된 문제: "담당자" 같은 키워드가 예산 항목 설명
    # 문장 중간에 우연히 낀 경우(예: "원격교육 업무담당자 역량강화과정 운영비")까지
    # 후보로 잡혀서, 치환하면 문장 전체가 맥락 없이 "이름+번호"로 날아가 버렸다.
    # → "라벨: 값" 형태로 명확히 자리표시인 경우만 후보로 인정한다(단독 직책/부서명
    # 문구는 실측(2026-07-15)으로 조직도 라벨일 뿐 PII 자리가 아님을 확인해 제외).
    if not _CLAUSE_6_LABEL_PATTERN.search(text) or len(text) > _CLAUSE_6_LABEL_MAX_LEN:
        return False
    return not _CLAUSE_6_GOV_TITLE_PATTERN.search(text)


def _base_candidate(annotated_doc: dict[str, Any], page: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_pdf_path": annotated_doc.get("source_pdf_path"),
        "source": annotated_doc.get("source"),
        "doc_type": annotated_doc.get("doc_type"),
        "doc_id": annotated_doc.get("doc_id"),
        "span_id": span["span_id"],
        "text": span["text"],
        "bbox": span.get("bbox"),  # HWP 유래 span은 좌표 개념이 없어 None
        "page_no": page["page_no"],
    }


def find_candidates(annotated_doc: dict[str, Any], clause_no: str) -> list[dict[str, Any]]:
    """annotated_doc에서 clause_no(5/6/7/8) 조항 후보 span을 최대
    _MAX_CANDIDATES_PER_DOC개까지 찾아 반환한다. 원본은 변경하지 않는다."""
    if "pages" not in annotated_doc:
        return []

    candidates: list[dict[str, Any]] = []
    for page in annotated_doc["pages"]:
        for span in page["spans"]:
            if span["is_boilerplate"]:
                continue
            text = span["cleaned_text"]
            if not text.strip():
                continue

            matched = _matches_clause_6(text) if clause_no == "6" else _matches_clause_5_or_7_or_8(text, clause_no)
            if not matched:
                continue

            candidates.append(_base_candidate(annotated_doc, page, span))
            if len(candidates) >= _MAX_CANDIDATES_PER_DOC:
                return candidates

    return candidates


def find_administrative_candidates(annotated_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """문서유형별 행정상태 신호를 가진 원본 span 후보를 반환한다.

    조항 후보와 달리 ``document_status``와 그 상태를 확정하기 전에 확인할
    메타데이터 요건을 함께 기록한다. 이 함수는 상태를 사실로 단정하지 않는다.
    """
    if "pages" not in annotated_doc:
        return []

    rules = _ADMIN_STATUS_RULES_BY_DOC_TYPE.get(annotated_doc.get("doc_type") or "", ())
    if not rules:
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for page in annotated_doc["pages"]:
        for span in page["spans"]:
            if span["is_boilerplate"]:
                continue
            text = span["cleaned_text"]
            if not text.strip():
                continue

            for rule in rules:
                if not any(keyword in text for keyword in rule.keywords):
                    continue
                key = (span["span_id"], rule.document_status)
                if key in seen:
                    continue
                seen.add(key)
                candidate = _base_candidate(annotated_doc, page, span)
                candidate.update(
                    {
                        "candidate_kind": "administrative_status",
                        "document_status": rule.document_status,
                        "status_evidence": text,
                        "verification_requirement": rule.verification_requirement,
                    }
                )
                candidates.append(candidate)
                if len(candidates) >= _MAX_CANDIDATES_PER_DOC:
                    return candidates

    return candidates
