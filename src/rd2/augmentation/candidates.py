"""annotate된 문서에서 조항별(정보공개법 제9조 5~8호) "기밀도 상승 후보" span을
정규식/키워드로 탐지한다.

`is_boilerplate=false`인 span만 검사 대상이다(annotate.py가 이미 표시해둔
반복 헤더·쪽번호는 애초에 후보가 될 수 없음 — annotate.py 참고). 원본 span은
여기서도 전혀 바꾸지 않는다 — 후보 목록만 뽑아낼 뿐이다.

제6호(개인정보)만 다른 조항과 메커니즘이 다르다: 실제 공개문서에는 진짜
개인정보가 거의 없으므로, "이미 있는 개인정보를 찾는" 게 아니라 "이 자리를
통째로 가상의 이름·직책·번호로 교체할 수 있는 자리표시 성격의 짧은 span"을
찾는다.
"""

from __future__ import annotations

import re
from typing import Any

# 5·7호는 금액 표현이 핵심 신호라 정규식으로 잡고, 조항별로 추가 키워드를 본다.
_MONEY_PATTERN = re.compile(r"\d[\d,]*\s*(원|만원|억원|백만원|천원)")

# 실측(2026-07-15)으로 지나치게 광범위한 단어를 걸러냄:
# - "평가"/"검토"(5호)는 학생평가·교원평가 등 조항과 무관한 내용을 대량으로 잡음
# - "수익"(7호)은 "수익자 부담"(교육사업 용어) 등 무관한 내용을 잡음
# - "개발"/"지정"/"고시"/"수용"/"지구"(8호)는 커리큘럼 개발·부서 지정처럼 부동산과
#   무관한 내용을 잡고, "지구"는 "복사지 구입"처럼 우연히 겹치는 오탐도 있었음
# → 부동산·감사계약 맥락이 뚜렷한 복합어 위주로 좁힘(문서 수 실측 후 충분함 확인).
_CLAUSE_KEYWORDS: dict[str, list[str]] = {
    "5": ["계약", "입찰", "감사", "인사", "심사", "낙찰"],
    "7": ["원가", "단가", "영업", "매출", "계약금액", "낙찰가"],
    "8": [
        "택지", "공시지가", "용도지역", "택지개발", "재개발", "재건축", "도시개발",
        "토지수용", "그린벨트", "개발제한구역", "부동산", "산업단지", "지구단위계획",
        "택지지구", "개발지구",
    ],
}
_CLAUSE_6_KEYWORDS = ["담당자", "문의", "연락처", "담당", "성명", "작성자", "책임자"]
_CLAUSE_6_SHORT_MAX_LEN = 20  # 단독 직책/라벨 문구(예: "홍보담당관")로 간주할 최대 길이
_CLAUSE_6_LABEL_MAX_LEN = 40  # "라벨: 값" 패턴은 조금 더 길어도 허용
# "담당자 :", "문의:" 처럼 라벨 뒤에 콜론이 오는 명확한 자리표시 패턴만 인정.
_CLAUSE_6_LABEL_PATTERN = re.compile(r"(담당자|문의|연락처|작성자|책임자|성명)\s*[:：]")

_MAX_CANDIDATES_PER_DOC = 20  # 문서 하나가 후보 풀을 독점하지 않게 하는 상한


def _matches_clause_5_or_7_or_8(text: str, clause_no: str) -> bool:
    if clause_no in ("5", "7") and _MONEY_PATTERN.search(text):
        return True
    return any(kw in text for kw in _CLAUSE_KEYWORDS[clause_no])


def _matches_clause_6(text: str) -> bool:
    # 실측(2026-07-16)으로 발견된 문제: "담당자" 같은 키워드가 예산 항목 설명
    # 문장 중간에 우연히 낀 경우(예: "원격교육 업무담당자 역량강화과정 운영비")까지
    # 후보로 잡혀서, 치환하면 문장 전체가 맥락 없이 "이름+번호"로 날아가 버렸다.
    # → "라벨: 값" 형태로 명확히 자리표시인 경우, 또는 아주 짧은 단독 직책/라벨
    # 문구인 경우만 후보로 인정한다(예산 설명문에 우연히 섞인 긴 문장은 제외).
    if _CLAUSE_6_LABEL_PATTERN.search(text) and len(text) <= _CLAUSE_6_LABEL_MAX_LEN:
        return True
    if len(text) <= _CLAUSE_6_SHORT_MAX_LEN and any(kw in text for kw in _CLAUSE_6_KEYWORDS):
        return True
    return False


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

            candidates.append(
                {
                    "source_pdf_path": annotated_doc.get("source_pdf_path"),
                    "source": annotated_doc.get("source"),
                    "doc_type": annotated_doc.get("doc_type"),
                    "doc_id": annotated_doc.get("doc_id"),
                    "span_id": span["span_id"],
                    "text": span["text"],
                    "bbox": span["bbox"],
                    "page_no": page["page_no"],
                }
            )
            if len(candidates) >= _MAX_CANDIDATES_PER_DOC:
                return candidates

    return candidates
