"""정확 중복(raw)과 표면 변형까지 잡는 정규화 중복(normalized) hash.

설계 문서 docs/design-coverage-matrix-diversity-audit-20260723.md §14.3(exact
canonicalization)과 §14.6(normalization 규칙)을 구현한다.

raw hash는 공백/줄바꿈/유니코드 형태 차이만 흡수한다 — 내용 자체가 다르면
다른 hash가 나와야 한다. normalized hash는 거기에 기관명·날짜·전화번호·문서번호
같은 표면 변형을 typed placeholder로 치환해 "기관명·날짜·숫자만 다르고 본문은
같은" 문서까지 잡는다. NFKC는 글자 모양 차이를 합칠 수 있어 쓰지 않는다(NFC만).

placeholder로 치환된 원본 값과 그 hash는 v1 artifact에 저장하지 않는다(§7.2,
§14.3) — 이 모듈도 원본 숫자/이름을 반환하지 않고 hash만 반환한다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

CONTENT_NORMALIZATION_VERSION = "content-normalize-v1"

_SECTION_MARKER = "\n<SECTION:BODY>\n"

_WHITESPACE_RUN = re.compile(r"[ \t ]+")


def _canonicalize_text(text: str) -> str:
    """§14.3의 1~6단계: NFC → 개행 통일 → 줄별 trim → 공백 압축 → 빈 줄 압축 → 전체 trim."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WHITESPACE_RUN.sub(" ", line.strip()) for line in text.split("\n")]

    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        blank = line == ""
        if blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = blank

    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return "\n".join(collapsed)


def _combine(title: str, body: str) -> str:
    """§14.3 7단계: title + "\\n<SECTION:BODY>\\n" + body."""
    return f"{_canonicalize_text(title or '')}{_SECTION_MARKER}{_canonicalize_text(body or '')}"


def raw_content_hash(title: str, body: str) -> str:
    return hashlib.sha256(_combine(title, body).encode("utf-8")).hexdigest()


# 치환 순서(§14.6): 문서번호 -> 사건번호 -> 전화번호 -> 날짜 -> 금액 -> 인원 -> 일반 숫자.
# 법 조항 번호(제N조/제N항/제N호)는 어느 단계에서도 치환하지 않는다 — 문서번호
# 패턴은 연도-일련번호 형태("제2024-1234호")나 부서명-일련번호 형태만 잡고,
# 일반 숫자 패턴은 뒤에 조/항/호가 바로 붙는 숫자를 lookahead로 제외한다.
_DOCNO_PATTERNS = (
    re.compile(r"제\s*\d{4}-\d+\s*호"),
    re.compile(r"[가-힣A-Za-z]{2,15}-\d{2,}(?:\(\d{4}\))?"),
)
_CASE_NO_PATTERN = re.compile(r"\d{4}[가-힣]{1,3}\d+")
_PHONE_PATTERN = re.compile(r"\d{2,4}-\d{3,4}-\d{4}")
_DATE_PATTERNS = (
    re.compile(r"\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\.?"),
    re.compile(r"\d{4}년\s*\d{1,2}월\s*\d{1,2}일"),
)
_MONEY_PATTERN = re.compile(r"\d{1,3}(?:,\d{3})+\s*원|\d+\s*(?:만|억)\s*원|\d+\s*원")
_COUNT_PATTERN = re.compile(r"\d+\s*명")
# 법 조항 참조(제9조/제1항/제2호)는 숫자 뒤에 공백 없이/조금 있고 조·항·호가
# 바로 오므로 lookahead로 제외한다.
_GENERAL_NUMBER_PATTERN = re.compile(r"\d+(?!\s*(?:조|항|호))")


def normalize_placeholders(text: str) -> str:
    result = text
    for pattern in _DOCNO_PATTERNS:
        result = pattern.sub("<DOCNO>", result)
    result = _CASE_NO_PATTERN.sub("<CASE_NO>", result)
    result = _PHONE_PATTERN.sub("<PHONE>", result)
    for pattern in _DATE_PATTERNS:
        result = pattern.sub("<DATE>", result)
    result = _MONEY_PATTERN.sub("<MONEY>", result)
    result = _COUNT_PATTERN.sub("<COUNT>", result)
    result = _GENERAL_NUMBER_PATTERN.sub("<NUM>", result)
    return result


def _substitute_agency(text: str, ordering_agency: str | None) -> str:
    """§14.6: 기관명은 exact ordering_agency 값만 치환한다 — 임의 NER은 하지 않는다.

    별칭 테이블은 저장소 어디에도 아직 없어(agency_resolver.py 확인) 만들지
    않는다 — exact match만 지원한다.
    """
    if not ordering_agency:
        return text
    return text.replace(ordering_agency, "<AGENCY>")


def normalized_content_hash(title: str, body: str, *, ordering_agency: str | None = None) -> str:
    combined = _combine(title, body)
    combined = _substitute_agency(combined, ordering_agency)
    combined = normalize_placeholders(combined)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
