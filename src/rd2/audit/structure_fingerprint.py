"""형식 반복(structure fingerprint) — 설계 문서 §7.4.

PDF 이미지 유사도 대신 generated CSV에서 결정적으로 얻을 수 있는 값만 쓴다.
ordered heading/table 구조는 현재 CSV 계약에 없어(§7.4) v1 fingerprint에
넣지 않는다.
"""

from __future__ import annotations

import re

from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_NUMBERED_LIST_LINE = re.compile(r"^\s*(?:\d{1,3}[.)]|[①-⑳]|[가-힣]\.)\s+")


def _bucket_label(value: int, edges: tuple[int, ...]) -> str:
    lower = 0
    for edge in edges:
        if value <= edge:
            return f"{lower}-{edge}"
        lower = edge + 1
    return f"{lower}+"


def paragraph_count_bucket(body_text: str) -> str:
    paragraphs = [p for p in _PARAGRAPH_SPLIT.split(body_text or "") if p.strip()]
    if not paragraphs:
        return "0"
    return _bucket_label(len(paragraphs), (2, 5, 10))


def numbered_list_count_bucket(body_text: str) -> str:
    count = sum(1 for line in (body_text or "").split("\n") if _NUMBERED_LIST_LINE.match(line))
    if count == 0:
        return "0"
    return _bucket_label(count, (2, 5, 10))


def body_length_bucket(body_text: str) -> str:
    length = len(body_text or "")
    if length == 0:
        return "0"
    return _bucket_label(length, (200, 500, 1000, 2000, 4000))


def structure_fingerprint(template_id: str, doc_type: str, body_text: str) -> str:
    payload = {
        "template_id": template_id,
        "doc_type": doc_type,
        "paragraph_count_bucket": paragraph_count_bucket(body_text),
        "numbered_list_count_bucket": numbered_list_count_bucket(body_text),
        "body_length_bucket": body_length_bucket(body_text),
    }
    return "sha256:" + canonical_sha256(payload, normalization_version=NORMALIZATION_VERSION)
