"""Add transient analysis annotations to canonical extraction-v2 lines.

The canonical extraction artifact stores physical lines under ``pages[].lines``.
This module deliberately mutates a loaded document in place: annotation is a
derived, run-local view and is not a second persisted copy of the extraction.
Only ``cleaned_text`` and ``is_boilerplate`` are added to line objects.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterator

_OCCUPANCY_RATIO_THRESHOLD = 0.3
_MIN_OCCUPANCY_PAGES = 3
_CONTENT_CONSISTENCY_THRESHOLD = 0.8

_DASH_CHARS = "\\-‐‑‒–—−－"
_NUMERIC_PATTERN = re.compile(
    rf"^[{_DASH_CHARS}]?\s*[0-9ivxlcdmIVXLCDM]+\s*[{_DASH_CHARS}]?$"
)
_NUMERIC_GROUP_KEY = "\x00NUMERIC\x00"


def _looks_numeric(text: str) -> bool:
    return bool(_NUMERIC_PATTERN.fullmatch(text.strip()))


def _is_garbled_char(ch: str) -> bool:
    if ch in ("\n", "\r", "\t"):
        return False
    codepoint = ord(ch)
    if codepoint < 0x20 or codepoint == 0x7F:
        return True
    if codepoint == 0xFFFD:
        return True
    return 0xE000 <= codepoint <= 0xF8FF


def _clean_text(text: str) -> str:
    return "".join(ch for ch in text if not _is_garbled_char(ch))


def _iter_lines(pages: list[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for page in pages:
        for line in page.get("lines", []):
            yield page, line


def _bbox(line: dict[str, Any]) -> list[float] | None:
    value = line.get("bbox_pt")
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"invalid extraction-v2 bbox_pt for line_id={line.get('line_id')!r}")
    return value


def _detect_boilerplate_slots(pages: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Return repeated PDF ``(round(x0), round(y0))`` geometry slots."""
    num_pages = len(pages)
    if num_pages == 0:
        return set()

    slot_pages: dict[tuple[int, int], set[object]] = defaultdict(set)
    slot_texts: dict[tuple[int, int], list[str]] = defaultdict(list)
    for page, line in _iter_lines(pages):
        bbox = _bbox(line)
        if bbox is None:
            continue
        slot = (round(bbox[0]), round(bbox[1]))
        slot_pages[slot].add(page.get("page"))
        slot_texts[slot].append(str(line.get("text") or ""))

    boilerplate_slots: set[tuple[int, int]] = set()
    for slot, pages_seen in slot_pages.items():
        if len(pages_seen) < _MIN_OCCUPANCY_PAGES:
            continue
        if len(pages_seen) / num_pages < _OCCUPANCY_RATIO_THRESHOLD:
            continue

        texts = slot_texts[slot]
        most_common_count = max(texts.count(text) for text in set(texts))
        same_text_ratio = most_common_count / len(texts)
        numeric_ratio = sum(1 for text in texts if _looks_numeric(text)) / len(texts)
        if (
            same_text_ratio >= _CONTENT_CONSISTENCY_THRESHOLD
            or numeric_ratio >= _CONTENT_CONSISTENCY_THRESHOLD
        ):
            boilerplate_slots.add(slot)
    return boilerplate_slots


def _group_key(text: str) -> str:
    return _NUMERIC_GROUP_KEY if _looks_numeric(text) else text


def _detect_boilerplate_keys(pages: list[dict[str, Any]]) -> set[str]:
    """Fallback for documents whose line geometry is entirely null (HWP/HWPX)."""
    num_pages = len(pages)
    if num_pages == 0:
        return set()

    key_pages: dict[str, set[object]] = defaultdict(set)
    for page, line in _iter_lines(pages):
        key_pages[_group_key(str(line.get("text") or ""))].add(page.get("page"))

    return {
        key
        for key, pages_seen in key_pages.items()
        if len(pages_seen) >= _MIN_OCCUPANCY_PAGES
        and len(pages_seen) / num_pages >= _OCCUPANCY_RATIO_THRESHOLD
    }


def annotate_document_in_place(document: dict[str, Any]) -> dict[str, Any]:
    """Mutate one loaded extraction-v2 document and return the same object.

    PDF documents use rounded ``bbox_pt`` x/y slots. If every line has null
    geometry, as canonical HWP/HWPX extraction does, exact/numeric text
    repetition is used instead. Mixed geometry is treated as PDF-like: lines
    with null geometry are never inferred to be boilerplate from text alone.
    """
    pages = document.get("pages")
    if pages is None:
        return document
    if not isinstance(pages, list):
        raise ValueError("extraction-v2 pages must be a list")

    loaded_lines = list(_iter_lines(pages))
    has_geometry = any(_bbox(line) is not None for _, line in loaded_lines)
    if has_geometry:
        boilerplate_slots = _detect_boilerplate_slots(pages)
        boilerplate_keys: set[str] = set()
    else:
        boilerplate_slots = set()
        boilerplate_keys = _detect_boilerplate_keys(pages)

    for _page, line in loaded_lines:
        text = str(line.get("text") or "")
        bbox = _bbox(line)
        if has_geometry and bbox is not None:
            slot = (round(bbox[0]), round(bbox[1]))
            is_boilerplate = slot in boilerplate_slots
        elif has_geometry:
            is_boilerplate = False
        else:
            is_boilerplate = _group_key(text) in boilerplate_keys
        line["is_boilerplate"] = is_boilerplate
        line["cleaned_text"] = _clean_text(text)
    return document


def annotate_document(document: dict[str, Any]) -> dict[str, Any]:
    """Compatibility name for callers; annotation is now always in place."""
    return annotate_document_in_place(document)
