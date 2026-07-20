"""추출된 span에 "이건 반복되는 껍데기(표 헤더·쪽번호 등)다"라는 주석만
얹는다 — span을 지우거나 바꾸지 않는다.

원본 추출 결과(`data/extracted/...`)의 span은 단 하나도 지우거나 바꾸지 않는다.
이 모듈은 후보 탐지 단계(candidates.py)가 "이 span은 분석에서 무시해도 된다"는
힌트로 쓸 `is_boilerplate`/`cleaned_text` 두 필드만 원본 span dict에 추가한
사본을 만든다.

2026-07-20: 원래는 반올림한 좌표(x0,y0) 슬롯이 페이지마다 반복되는지로
판정했으나, `pdf_text.py`가 더 이상 bbox를 저장하지 않게 되면서(재구성 단계
폐기, AUGMENTATION_STATUS.md 참고) **정확히 같은 텍스트가 여러 페이지에
반복되는지**로 판정 기준을 바꿨다. 판단 기준(전부 만족해야 boilerplate로
표시 — 빈도만으로는 판단하지 않음):
- 조건 A(빈도): 문서 전체 페이지의 30% 이상에서 같은 텍스트(또는 숫자/로마숫자
  패턴)를 가진 span이 있다
- 조건 A'(절대 최소 횟수): **최소 3개 이상의 서로 다른 페이지**에서 등장해야
  한다. 짧은 문서(예: 3페이지)는 비율 30%가 페이지 1장만 차지해도 넘어버려서,
  문서에 딱 1번만 나온 고유한 제목("1. 사업개요")이나 공고번호까지 boilerplate로
  오판되는 게 실측(2026-07-14)으로 확인됨 — 그래서 비율과 별개로 최소 반복
  횟수를 강제한다
- 조건 B(내용 일관성): 텍스트 기반 그룹핑에서는 자동으로 항상 만족된다 — 그룹
  키 자체가 "정확히 같은 텍스트"거나 "숫자/로마숫자 패턴"이라, 같은 그룹 안
  내용은 정의상 100% 일관되기 때문(과거 좌표 슬롯 방식은 위치는 같은데 내용이
  자꾸 바뀌는 경우를 걸러내려고 이 조건이 별도로 필요했음).

**트레이드오프**: 이제 "같은 위치"가 아니라 "같은 내용(또는 숫자 패턴)"만
본다. 실제 반복 헤더/쪽번호는 위치와 내용이 둘 다 반복되므로 여전히 잡힌다.
다만 위치는 매번 다른데 우연히 같은 짧은 문구(예: "담당부서:")가 여러 페이지에
반복되는 경우도 이제는 boilerplate로 잡힌다 — 후보 탐지 관점에서는 "분석
가치가 낮은 반복 문구"로 취급하는 게 크게 틀린 방향은 아니라고 보고 받아들인
트레이드오프다.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Any

_OCCUPANCY_RATIO_THRESHOLD = 0.3
_MIN_OCCUPANCY_PAGES = 3  # 비율과 별개로 최소 이만큼은 실제로 반복돼야 함(짧은 문서 오탐 방지)

# 페이지번호에 흔히 쓰이는 여러 대시 문자(하이픈, en/em dash, 마이너스 기호,
# 전각 대시 등)까지 포함 — 실측 중 전각 대시('－103－')를 놓치는 사례를 발견해 보강함.
_DASH_CHARS = "\\-‐‑‒–—−－"
_NUMERIC_PATTERN = re.compile(
    rf"^[{_DASH_CHARS}]?\s*[0-9ivxlcdmIVXLCDM]+\s*[{_DASH_CHARS}]?$"
)

# 숫자/로마숫자 패턴 span을 전부 한 그룹으로 묶기 위한 키 — 실제 텍스트로는
# 절대 나올 수 없는 값이라 진짜 텍스트와 충돌하지 않는다.
_NUMERIC_GROUP_KEY = "\x00NUMERIC\x00"


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


def _group_key(text: str) -> str:
    return _NUMERIC_GROUP_KEY if _looks_numeric(text) else text


def _detect_boilerplate_keys(pages: list[dict[str, Any]]) -> set[str]:
    """조건 A·A'를 모두 만족하는 그룹 키(정확한 텍스트 또는 숫자패턴 키) 집합을 반환한다."""
    num_pages = len(pages)
    if num_pages == 0:
        return set()

    key_pages: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        for span in page["spans"]:
            key_pages[_group_key(span["text"])].add(page["page_no"])

    boilerplate_keys: set[str] = set()
    for key, pages_seen in key_pages.items():
        if len(pages_seen) < _MIN_OCCUPANCY_PAGES:
            continue  # 절대 최소 반복 횟수 미달 — 짧은 문서의 1회성 콘텐츠 오탐 방지
        if len(pages_seen) / num_pages < _OCCUPANCY_RATIO_THRESHOLD:
            continue
        boilerplate_keys.add(key)

    return boilerplate_keys


def annotate_document(extracted_doc: dict[str, Any]) -> dict[str, Any]:
    """추출된 문서 dict를 받아 `is_boilerplate`/`cleaned_text`가 추가된 사본을 반환한다.

    원본 dict는 변경하지 않는다(깊은 복사 후 필드만 추가) — span 개수·순서·
    `span_id`·`text`는 원본과 100% 동일하게 유지된다.
    """
    annotated = copy.deepcopy(extracted_doc)
    if "pages" not in annotated:
        return annotated  # error/스캔본 등 pages가 없는 결과는 그대로 반환

    boilerplate_keys = _detect_boilerplate_keys(annotated["pages"])

    for page in annotated["pages"]:
        for span in page["spans"]:
            span["is_boilerplate"] = _group_key(span["text"]) in boilerplate_keys
            span["cleaned_text"] = _clean_text(span["text"])

    return annotated
