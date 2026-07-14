"""추출된 span에 "이건 반복되는 껍데기(표 헤더·쪽번호 등)다"라는 주석만
얹는다 — span을 지우거나 바꾸지 않는다.

나중에 span 치환 결과를 실제 PDF 레이아웃에 다시 합성해야 하므로, 원본
추출 결과(`data/extracted/...`)의 span은 단 하나도 지우거나 바꾸지 않는다.
이 모듈은 문서 feature 추출·LLM 후보 선정 단계(다음 단계, 아직 미구현)가
"이 span은 분석에서 무시해도 된다"는 힌트로 쓸 `is_boilerplate`/`cleaned_text`
두 필드만 원본 span dict에 추가한 사본을 만든다.

판단 기준(전부 만족해야 boilerplate로 표시 — 빈도만으로는 판단하지 않음):
- 조건 A(빈도): 문서 전체 페이지의 30% 이상에서 같은 (반올림한) 좌표 슬롯에
  span이 있다
- 조건 A'(절대 최소 횟수): 그 슬롯이 **최소 3개 이상의 서로 다른 페이지**에서
  등장해야 한다. 짧은 문서(예: 3페이지)는 비율 30%가 페이지 1장만 차지해도
  넘어버려서, 문서에 딱 1번만 나온 고유한 제목("1. 사업개요")이나 공고번호
  ("보건복지부공고제2010-282호")까지 boilerplate로 오판되는 게 실측
  (2026-07-14, `241236_입찰공고-보건복지부공고제2010-282호.json`, 3페이지
  문서)으로 확인됨 — 등장이 1번뿐이면 "내용 일관성"도 비교 대상이 자기
  자신뿐이라 자동으로 100%가 되어버려 조건 B로도 못 걸러진다. 그래서 비율과
  별개로 최소 반복 횟수를 강제한다
- 조건 B(내용 일관성): 그 슬롯에 나온 텍스트 중 80% 이상이 완전히 동일한
  문자열이거나("코드"·"금액"처럼 항상 같은 문구), 80% 이상이 숫자/로마숫자
  패턴이다(쪽번호처럼 페이지마다 값은 바뀌지만 패턴은 규칙적인 경우)
조건 A만 만족하고 조건 B가 불만족(같은 자리인데 내용이 매번 다른 진짜
문장)인 경우는 boilerplate로 표시하지 않는다. `data/moe/budget_material/
76330_...json` 참고: '코드'(x0=98)는 88% 페이지에서 100% 동일 문구로 반복
→ boilerplate. '2,000' 같은 값은 등장은 잦아도 위치가 완전히 흩어져 있어 제외됨.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any

_OCCUPANCY_RATIO_THRESHOLD = 0.3
_MIN_OCCUPANCY_PAGES = 3  # 비율과 별개로 최소 이만큼은 실제로 반복돼야 함(짧은 문서 오탐 방지)
_CONTENT_CONSISTENCY_THRESHOLD = 0.8

# 페이지번호에 흔히 쓰이는 여러 대시 문자(하이픈, en/em dash, 마이너스 기호,
# 전각 대시 등)까지 포함 — 실측 중 전각 대시('－103－')를 놓치는 사례를 발견해 보강함.
_DASH_CHARS = "\\-‐‑‒–—−－"
_NUMERIC_PATTERN = re.compile(
    rf"^[{_DASH_CHARS}]?\s*[0-9ivxlcdmIVXLCDM]+\s*[{_DASH_CHARS}]?$"
)


def _looks_numeric(text: str) -> bool:
    return bool(_NUMERIC_PATTERN.fullmatch(text.strip()))


def _is_garbled_char(ch: str) -> bool:
    if ch in ("\n", "\r", "\t"):
        return False
    cp = ord(ch)
    if cp < 0x20 or cp == 0x7F:
        return True
    if cp == 0xFFFD:
        return True
    if 0xE000 <= cp <= 0xF8FF:  # PUA(Private Use Area) — 실측된 불릿류 포함
        return True
    return False


def _clean_text(text: str) -> str:
    return "".join(ch for ch in text if not _is_garbled_char(ch))


def _detect_boilerplate_slots(pages: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """(반올림한 x0, y0) 슬롯 중 조건 A·B를 모두 만족하는 것만 반환한다."""
    num_pages = len(pages)
    if num_pages == 0:
        return set()

    slot_pages: dict[tuple[int, int], set[int]] = defaultdict(set)
    slot_texts: dict[tuple[int, int], list[str]] = defaultdict(list)

    for page in pages:
        for span in page["spans"]:
            x0, y0 = span["bbox"][0], span["bbox"][1]
            slot = (round(x0), round(y0))
            slot_pages[slot].add(page["page_no"])
            slot_texts[slot].append(span["text"])

    boilerplate_slots: set[tuple[int, int]] = set()
    for slot, pages_seen in slot_pages.items():
        if len(pages_seen) < _MIN_OCCUPANCY_PAGES:
            continue  # 절대 최소 반복 횟수 미달 — 짧은 문서의 1회성 콘텐츠 오탐 방지
        occupancy_ratio = len(pages_seen) / num_pages
        if occupancy_ratio < _OCCUPANCY_RATIO_THRESHOLD:
            continue

        texts = slot_texts[slot]
        most_common_count = max(texts.count(t) for t in set(texts))
        same_text_ratio = most_common_count / len(texts)
        numeric_ratio = sum(1 for t in texts if _looks_numeric(t)) / len(texts)

        if same_text_ratio >= _CONTENT_CONSISTENCY_THRESHOLD or numeric_ratio >= _CONTENT_CONSISTENCY_THRESHOLD:
            boilerplate_slots.add(slot)

    return boilerplate_slots


def annotate_document(extracted_doc: dict[str, Any]) -> dict[str, Any]:
    """추출된 문서 dict를 받아 `is_boilerplate`/`cleaned_text`가 추가된 사본을 반환한다.

    원본 dict는 변경하지 않는다(깊은 복사 후 필드만 추가) — span 개수·순서·
    `span_id`·`bbox`·`text`는 원본과 100% 동일하게 유지된다.
    """
    annotated = copy.deepcopy(extracted_doc)
    if "pages" not in annotated:
        return annotated  # error/스캔본 등 pages가 없는 결과는 그대로 반환

    boilerplate_slots = _detect_boilerplate_slots(annotated["pages"])

    for page in annotated["pages"]:
        for span in page["spans"]:
            x0, y0 = span["bbox"][0], span["bbox"][1]
            slot = (round(x0), round(y0))
            span["is_boilerplate"] = slot in boilerplate_slots
            span["cleaned_text"] = _clean_text(span["text"])

    return annotated
