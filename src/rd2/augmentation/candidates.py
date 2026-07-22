"""Detect clause and administrative candidates from extraction-v2 lines.

Canonical extraction stores physical lines in ``pages[].lines``. PDF lines that
are adjacent inside one block are merged only for candidate analysis; the
persisted extraction remains unchanged and candidate records retain the source
``line_ids`` needed to resolve the derived segment. Geometry-null HWP lines are
already logical lines and are never merged here.

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

import hashlib
import heapq
import json
import re
from dataclasses import dataclass
from typing import Any

from rd2.storage.naming import (
    DOC_TYPE_APPROVAL,
    DOC_TYPE_AUDIT_RESULT,
    DOC_TYPE_BID_NOTICE,
    DOC_TYPE_BID_RENOTICE,
    DOC_TYPE_BUDGET_MATERIAL,
    DOC_TYPE_DIRECTIVE,
    DOC_TYPE_GUIDE,
    DOC_TYPE_INTERPRETATION_COMPILATION,
    DOC_TYPE_MEETING_MINUTES,
    DOC_TYPE_NOTICE,
    DOC_TYPE_NOTIFICATION,
    DOC_TYPE_OFFICIAL_DOCUMENT,
    DOC_TYPE_PERSONNEL,
    DOC_TYPE_PLAN,
    DOC_TYPE_POLICY_MATERIAL,
    DOC_TYPE_PRE_SPEC_NOTICE,
    DOC_TYPE_PRESS_RELEASE,
    DOC_TYPE_PUBLIC_OFFERING,
    DOC_TYPE_REGULATION,
    DOC_TYPE_REPLY_NOTIFICATION,
    DOC_TYPE_REPORT,
    DOC_TYPE_RESEARCH_REPORT,
    DOC_TYPE_STATUS_REPORT,
)

# 5·7호의 금액 표현은 정규식으로 잡되, 금액만 있는 span은 앞뒤에 해당 조항
# 키워드가 있을 때만 최종 후보로 채택한다. 금액 단독 매치는 일반 예산표를 너무
# 넓게 잡으므로 조항별 키워드를 문맥 근거로 함께 요구한다.
_MONEY_PATTERN = re.compile(r"\d[\d,]*\s*(원|만원|억원|백만원|천원)")

# 실측(2026-07-15, 2026-07-21 재검증)으로 지나치게 광범위한 단어를 걸러냄:
# - "평가"/"검토"(5호)는 학생평가·교원평가 등 조항과 무관한 내용을 대량으로 잡음
# - "수익"/"영업"(7호)은 수익자 부담, 영업인가·영업용 주택 등 무관한 내용을 잡음
# - "개발"/"지정"/"고시"/"수용"/"지구"(8호)는 커리큘럼 개발·부서 지정처럼 부동산과
#   무관한 내용을 잡고, 단독 "부동산"도 일반 현황·담당부서 회신을 대량으로 잡음.
#   "지구"는 "복사지 구입"처럼 우연히 겹치는 오탐도 있었음
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
        "원가", "단가", "매출", "계약금액", "낙찰가",
        # 영업·경영상 비밀 동의어
        "영업비밀", "기술이전", "로열티", "지식재산권",
        "원가구조", "납품단가", "수주금액", "용역대가",
    ],
    "8": [
        "택지", "공시지가", "용도지역", "택지개발", "재개발", "재건축", "도시개발",
        "토지수용", "그린벨트", "개발제한구역", "산업단지", "지구단위계획",
        "택지지구", "개발지구",
        # 지자체별 정비·보상·인허가 용어(재개발·재건축 외에 정비구역 지정 방식이 다양함)
        "정비구역", "지목변경",
        "토지보상", "손실보상", "수용재결", "개발행위허가",
        "공공주택지구", "매립지", "간척지", "분양가상한제", "미분양",
    ],
}

# 짧은 일반 키워드가 무관한 복합어 안에 포함되는 경우만 지운 뒤 매칭한다.
# 같은 span에 별도의 실제 키워드가 있으면 그 occurrence는 그대로 남는다.
_KEYWORD_EXCLUDED_PHRASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("5", "계약"): ("무기계약직", "계약직"),
    ("5", "감사"): (
        "감사드립니다",
        "감사드리",
        "감사합니다",
        "감사와 축하",
        "감사의 말씀",
        "감사원",
    ),
    ("5", "심사"): ("심사청구",),
    ("5", "인사"): ("인사드립니다", "인사 합니다", "인사합니다", "인사말"),
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

_MAX_CANDIDATES_PER_DOC = 60  # 정밀도순 선별 후에도 특정 문서가 후보 풀을 독점하지 않게 하는 상한
_CONTEXT_SPAN_RADIUS = 2
_MAX_CONTEXT_CHARS_PER_SPAN = 240
_MERGE_MAX_CHAIN = 40
_MERGE_GAP_RATIO = 0.8
_CLAUSE_NOS = ("5", "6", "7", "8")
CANDIDATE_RULE_VERSION = "candidate-rules-v2-20260722"


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
ADMIN_STATUS_RULES_BY_DOC_TYPE: dict[str, tuple[AdministrativeStatusRule, ...]] = {
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
    # 2026-07-21 office-hours/plan-eng-review로 신규 추가 — 아래 7개 문서유형
    # (press_release/notice/bid_renotice/public_offering/budget_material/
    # interpretation_compilation/pre_spec_notice)은 이 파일의 다른 규칙과 달리
    # 아직 실제 문서로 검증되지 않은 초안이다. 기존 규칙들이 전부 실사 검증을
    # 거쳐 오탐 단어를 걸러낸 것과 달리(위 5·7·8호 실측 이력, "평가"/"검토"
    # 단독 사용 오탐 등 참고), 이 7개는 korea_kr/alio 등 소스의 추출→주석
    # 파이프라인이 아직 실행된 적이 없어(설계 문서 참고) 대조할 annotated
    # 문서가 없었다. 인접 문서유형(policy_material/notification/bid_notice
    # 등)의 이미 검증된 패턴에서 유추해 작성했으며, 첫 실행 후 반드시 실사
    # 재검증이 필요하다.
    DOC_TYPE_PRESS_RELEASE: (
        AdministrativeStatusRule("공개예정일미도래", ("배포 예정", "보도 예정", "엠바고")),
        AdministrativeStatusRule("초안", ("보도자료(안)", "초안", "검토안")),
        AdministrativeStatusRule("타기관협의중", ("관계기관", "부처 협의", "의견 조회")),
    ),
    DOC_TYPE_NOTICE: (
        AdministrativeStatusRule("공개예정일미도래", ("공고 예정", "게시 예정", "시행 예정")),
        AdministrativeStatusRule("초안", ("공고안", "초안", "검토안")),
        AdministrativeStatusRule("타기관협의중", ("관계기관", "의견수렴", "협의 중")),
    ),
    DOC_TYPE_BID_RENOTICE: (
        AdministrativeStatusRule("내부검토중", ("재공고 검토", "재입찰 검토", "검토안")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "재공고문", "별첨"), "attachment_inventory"),
    ),
    DOC_TYPE_PUBLIC_OFFERING: (
        AdministrativeStatusRule("공개예정일미도래", ("공모 예정", "접수 예정", "시행 예정")),
        AdministrativeStatusRule("내부검토중", ("공모요강 검토", "심사기준 검토", "검토안")),
        AdministrativeStatusRule("타기관협의중", ("관계기관", "의견수렴")),
    ),
    DOC_TYPE_BUDGET_MATERIAL: (
        AdministrativeStatusRule("내부검토중", ("예산(안)", "편성 중", "검토 중")),
        AdministrativeStatusRule("타기관협의중", ("기획재정부", "관계기관", "협의 중")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "산출내역서", "별첨"), "attachment_inventory"),
    ),
    DOC_TYPE_INTERPRETATION_COMPILATION: (
        AdministrativeStatusRule("내부검토중", ("회신 검토", "검토 중", "검토 의견")),
        AdministrativeStatusRule("문서정리중", ("정비 중", "개정 예정", "폐지 예정")),
    ),
    DOC_TYPE_PRE_SPEC_NOTICE: (
        AdministrativeStatusRule("내부검토중", ("사전규격 검토", "제안요청 검토", "검토안")),
        AdministrativeStatusRule("타기관협의중", ("의견수렴", "관계기관", "협의 중")),
        AdministrativeStatusRule("첨부미등록", ("붙임", "제안요청서"), "attachment_inventory"),
    ),
}


def _matches_clause_5_or_7_or_8(text: str, clause_no: str) -> bool:
    matched_rules, _ = _clause_match_evidence(text, clause_no)
    return bool(matched_rules)


def _matches_clause_6(text: str) -> bool:
    # 실측(2026-07-16)으로 발견된 문제: "담당자" 같은 키워드가 예산 항목 설명
    # 문장 중간에 우연히 낀 경우(예: "원격교육 업무담당자 역량강화과정 운영비")까지
    # 후보로 잡혀서, 치환하면 문장 전체가 맥락 없이 "이름+번호"로 날아가 버렸다.
    # → "라벨: 값" 형태로 명확히 자리표시인 경우만 후보로 인정한다(단독 직책/부서명
    # 문구는 실측(2026-07-15)으로 조직도 라벨일 뿐 PII 자리가 아님을 확인해 제외).
    if not _CLAUSE_6_LABEL_PATTERN.search(text) or len(text) > _CLAUSE_6_LABEL_MAX_LEN:
        return False
    return not _CLAUSE_6_GOV_TITLE_PATTERN.search(text)


def _matched_keywords(text: str, clause_no: str) -> list[str]:
    """텍스트에 실제로 등장한 키워드 중 더 긴 복합어에 포함된 일반어는 뺀다.

    예를 들어 ``수의계약``이 잡혔을 때 ``계약``까지 별도 근거로 세면 같은 신호를
    이중으로 점수화하게 된다. 복합어만 남겨 후보 점수가 과장되지 않게 한다.
    """
    matched: list[str] = []
    for keyword in _CLAUSE_KEYWORDS[clause_no]:
        searchable_text = text
        for phrase in sorted(
            _KEYWORD_EXCLUDED_PHRASES.get((clause_no, keyword), ()),
            key=len,
            reverse=True,
        ):
            searchable_text = searchable_text.replace(phrase, "")
        if keyword in searchable_text:
            matched.append(keyword)
    return [
        keyword
        for keyword in matched
        if not any(keyword != other and keyword in other for other in matched)
    ]


def _clause_match_evidence(text: str, clause_no: str) -> tuple[list[str], int]:
    """후보 선정 근거와 근거 강도 점수를 함께 반환한다.

    점수는 법적 확정도가 아니라 후보 우선순위다. 단순 금액 표현은 1점으로 두고,
    조항 문맥이 명확한 키워드는 길이와 복합어 여부에 따라 2~4점을 부여한다.
    """
    if clause_no == "6":
        if not _matches_clause_6(text):
            return [], 0
        return ["pattern:personal_information_label"], 5

    rules: list[str] = []
    score = 0
    if clause_no in ("5", "7") and _MONEY_PATTERN.search(text):
        rules.append("pattern:money")
        score += 1

    for keyword in _matched_keywords(text, clause_no):
        rules.append(f"keyword:{keyword}")
        score += min(4, 2 + len(keyword) // 4)
    return rules, score


@dataclass(frozen=True)
class _DerivedSegment:
    page: int
    line_ids: tuple[object, ...]
    text: str
    bbox_pt: tuple[float, float, float, float] | None
    block_id: object


def _require_v2(document: dict[str, Any]) -> None:
    if document.get("schema_version") != 2:
        raise ValueError("candidate detection requires extraction schema_version=2")
    if not document.get("extraction_id"):
        raise ValueError("candidate detection requires extraction_id")
    if not document.get("source_path"):
        raise ValueError("candidate detection requires source_path")
    if not document.get("source") or not document.get("doc_type"):
        raise ValueError("candidate detection requires source and doc_type")
    pages = document.get("pages")
    if not isinstance(pages, list):
        raise ValueError("candidate detection requires pages[]")
    line_ids: set[object] = set()
    required_line_fields = {"line_id", "block_id", "order", "text", "bbox_pt"}
    for page in pages:
        if "page" not in page or not isinstance(page.get("lines"), list):
            raise ValueError("candidate detection requires pages[].page and pages[].lines[]")
        for line in page["lines"]:
            missing = required_line_fields - line.keys()
            if missing:
                raise ValueError(f"candidate detection missing v2 line fields: {sorted(missing)}")
            line_id = line["line_id"]
            if line_id in line_ids:
                raise ValueError(f"duplicate extraction-v2 line_id: {line_id!r}")
            line_ids.add(line_id)


def _line_bbox(line: dict[str, Any]) -> tuple[float, float, float, float] | None:
    value = line.get("bbox_pt")
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"invalid bbox_pt for line_id={line.get('line_id')!r}")
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _can_merge_wrapped(segment: _DerivedSegment, line: dict[str, Any]) -> bool:
    """Apply the established vertical-gap/x-overlap merge rule to PDF lines."""
    if len(segment.line_ids) >= _MERGE_MAX_CHAIN:
        return False
    next_bbox = _line_bbox(line)
    if segment.bbox_pt is None or next_bbox is None:
        return False  # geometry-null HWP/HWPX lines are already logical lines
    if segment.block_id is None or line.get("block_id") != segment.block_id:
        return False

    x0a, y0a, x1a, y1a = segment.bbox_pt
    x0b, y0b, x1b, y1b = next_bbox
    line_height = max(y1a - y0a, 1.0)
    gap = y0b - y1a
    is_below_and_close = -0.5 <= gap <= line_height * _MERGE_GAP_RATIO
    x_overlaps = min(x1a, x1b) - max(x0a, x0b) > 0
    return is_below_and_close and x_overlaps


def _start_segment(page_no: int, line: dict[str, Any], text: str) -> _DerivedSegment:
    if "line_id" not in line:
        raise ValueError("candidate detection requires a line_id on every line")
    return _DerivedSegment(
        page=page_no,
        line_ids=(line["line_id"],),
        text=text,
        bbox_pt=_line_bbox(line),
        block_id=line.get("block_id"),
    )


def _append_line(segment: _DerivedSegment, line: dict[str, Any], text: str) -> _DerivedSegment:
    assert segment.bbox_pt is not None
    next_bbox = _line_bbox(line)
    assert next_bbox is not None
    x0a, y0a, x1a, _y1a = segment.bbox_pt
    x0b, _y0b, x1b, y1b = next_bbox
    needs_space = not segment.text.endswith(" ") and not text.startswith(" ")
    return _DerivedSegment(
        page=segment.page,
        line_ids=(*segment.line_ids, line["line_id"]),
        text=segment.text + (" " if needs_space else "") + text,
        bbox_pt=(min(x0a, x0b), y0a, max(x1a, x1b), y1b),
        block_id=segment.block_id,
    )


def _eligible_segments(document: dict[str, Any]) -> list[_DerivedSegment]:
    """Build non-boilerplate derived segments once, preserving document order."""
    segments: list[_DerivedSegment] = []
    for page in document["pages"]:
        page_no = int(page["page"])
        lines = sorted(page["lines"], key=lambda line: int(line.get("order", 0)))
        current: _DerivedSegment | None = None
        for line in lines:
            if "cleaned_text" not in line or "is_boilerplate" not in line:
                raise ValueError(
                    "candidate detection requires annotate_document_in_place() first"
                )
            text = str(line.get("cleaned_text") or "")
            if line["is_boilerplate"] or not text.strip():
                if current is not None:
                    segments.append(current)
                    current = None
                continue

            if current is None:
                current = _start_segment(page_no, line, text)
            elif _can_merge_wrapped(current, line):
                current = _append_line(current, line, text)
            else:
                segments.append(current)
                current = _start_segment(page_no, line, text)
        if current is not None:
            segments.append(current)
    return segments


def _clip_context(text: str) -> str:
    text = text.strip()
    if len(text) <= _MAX_CONTEXT_CHARS_PER_SPAN:
        return text
    return f"{text[:_MAX_CONTEXT_CHARS_PER_SPAN - 1]}…"


def _candidate_context(
    segments: list[_DerivedSegment],
    index: int,
) -> tuple[list[str], list[str], str]:
    before = [
        _clip_context(segment.text)
        for segment in segments[max(0, index - _CONTEXT_SPAN_RADIUS) : index]
    ]
    after = [
        _clip_context(segment.text)
        for segment in segments[index + 1 : index + 1 + _CONTEXT_SPAN_RADIUS]
    ]
    anchor = _clip_context(segments[index].text)
    parts = [*(f"[이전] {text}" for text in before), f"[대상] {anchor}"]
    parts.extend(f"[다음] {text}" for text in after)
    return before, after, "\n".join(parts)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _candidate_id(
    document: dict[str, Any],
    segment: _DerivedSegment,
    *,
    candidate_kind: str,
    qualifier: str,
) -> str:
    identity = {
        "candidate_kind": candidate_kind,
        "extraction_id": document["extraction_id"],
        "line_ids": list(segment.line_ids),
        "qualifier": qualifier,
        # extraction_id is content/config based, so byte-identical files can
        # share it. source_path keeps their candidate identities distinct.
        "source_path": document["source_path"],
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _base_candidate(
    document: dict[str, Any],
    segment: _DerivedSegment,
    *,
    candidate_kind: str,
    qualifier: str,
) -> dict[str, Any]:
    return {
        "candidate_id": _candidate_id(
            document,
            segment,
            candidate_kind=candidate_kind,
            qualifier=qualifier,
        ),
        "extraction_id": document["extraction_id"],
        "source_path": document["source_path"],
        "source": document.get("source"),
        "doc_type": document.get("doc_type"),
        "doc_id": document.get("doc_id"),
        "line_ids": list(segment.line_ids),
        "text": segment.text,
        "text_sha256": _sha256_text(segment.text),
        "page": segment.page,
        "candidate_kind": candidate_kind,
    }


def _push_top_candidate(
    heap: list[tuple[tuple[int, int, str], dict[str, Any]]],
    candidate: dict[str, Any],
    document_index: int,
) -> None:
    quality = (candidate["match_score"], -document_index, candidate["candidate_id"])
    item = (quality, candidate)
    if len(heap) < _MAX_CANDIDATES_PER_DOC:
        heapq.heappush(heap, item)
    elif quality > heap[0][0]:
        heapq.heapreplace(heap, item)


def _finalize_clause_heap(
    heap: list[tuple[tuple[int, int, str], dict[str, Any]]],
) -> list[dict[str, Any]]:
    candidates = [candidate for _quality, candidate in heap]
    candidates.sort(
        key=lambda candidate: (
            -candidate["match_score"],
            candidate["_document_index"],
            candidate["candidate_id"],
        )
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        candidate.pop("_document_index")
    return candidates


def find_all_candidates(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Evaluate all four clauses and administrative rules in one segment traversal."""
    _require_v2(document)
    results: dict[str, list[dict[str, Any]]] = {
        clause_no: [] for clause_no in _CLAUSE_NOS
    }
    results["administrative"] = []
    if not document["pages"]:
        return results

    segments = _eligible_segments(document)
    clause_heaps: dict[str, list[tuple[tuple[int, int, str], dict[str, Any]]]] = {
        clause_no: [] for clause_no in _CLAUSE_NOS
    }
    admin_rules = ADMIN_STATUS_RULES_BY_DOC_TYPE.get(document.get("doc_type") or "", ())
    seen_admin: set[tuple[tuple[object, ...], str]] = set()

    for document_index, segment in enumerate(segments):
        context_before, context_after, context_text = _candidate_context(
            segments, document_index
        )

        for clause_no in _CLAUSE_NOS:
            matched_rules, anchor_score = _clause_match_evidence(segment.text, clause_no)
            if not matched_rules:
                continue

            context_rules: list[str] = []
            context_score = 0
            for context_part in (*context_before, *context_after):
                part_rules, part_score = _clause_match_evidence(context_part, clause_no)
                context_rules.extend(rule for rule in part_rules if rule not in context_rules)
                context_score += part_score

            if matched_rules == ["pattern:money"] and not any(
                rule.startswith("keyword:") for rule in context_rules
            ):
                continue

            candidate = _base_candidate(
                document,
                segment,
                candidate_kind="clause",
                qualifier=clause_no,
            )
            candidate.update(
                {
                    "clause": clause_no,
                    "matched_rules": matched_rules,
                    "context_rules": context_rules,
                    "match_score": anchor_score + min(2, context_score),
                    "context_before": context_before,
                    "context_after": context_after,
                    "context_text": context_text,
                    "_document_index": document_index,
                }
            )
            _push_top_candidate(clause_heaps[clause_no], candidate, document_index)

        if len(results["administrative"]) >= _MAX_CANDIDATES_PER_DOC:
            continue
        for rule in admin_rules:
            if not any(keyword in segment.text for keyword in rule.keywords):
                continue
            key = (segment.line_ids, rule.document_status)
            if key in seen_admin:
                continue
            seen_admin.add(key)
            candidate = _base_candidate(
                document,
                segment,
                candidate_kind="administrative_status",
                qualifier=rule.document_status,
            )
            candidate.update(
                {
                    "document_status": rule.document_status,
                    "status_evidence": segment.text,
                    "verification_requirement": rule.verification_requirement,
                }
            )
            results["administrative"].append(candidate)
            if len(results["administrative"]) >= _MAX_CANDIDATES_PER_DOC:
                break

    for clause_no, heap in clause_heaps.items():
        results[clause_no] = _finalize_clause_heap(heap)
    return results


def find_candidates(document: dict[str, Any], clause_no: str) -> list[dict[str, Any]]:
    """Compatibility entry point for v2 unit callers and single-clause use."""
    if clause_no not in _CLAUSE_NOS:
        raise ValueError(f"unsupported clause: {clause_no}")
    return find_all_candidates(document)[clause_no]


def find_administrative_candidates(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility entry point for v2 unit callers."""
    return find_all_candidates(document)["administrative"]
