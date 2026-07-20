from __future__ import annotations

from rd2.augmentation.annotate import annotate_document


def _doc(pages_texts: list[list[str]]) -> dict:
    """pages_texts[i] = i번째 페이지(1-indexed)의 span 텍스트 리스트."""
    return {
        "pages": [
            {
                "page_no": page_no,
                "spans": [{"span_id": i, "text": text} for i, text in enumerate(texts)],
            }
            for page_no, texts in enumerate(pages_texts, start=1)
        ]
    }


def _flags(annotated: dict) -> list[bool]:
    return [span["is_boilerplate"] for page in annotated["pages"] for span in page["spans"]]


def test_exact_text_repeated_across_enough_pages_is_boilerplate():
    doc = _doc([["코드", "본문 1"], ["코드", "본문 2"], ["코드", "본문 3"]])
    annotated = annotate_document(doc)
    flags = [[s["is_boilerplate"] for s in p["spans"]] for p in annotated["pages"]]
    assert flags == [[True, False], [True, False], [True, False]]


def test_repeated_text_below_min_occupancy_pages_is_not_boilerplate():
    """최소 3페이지 미만이면 아무리 반복돼도 boilerplate로 안 잡는다."""
    doc = _doc([["헤더"], ["헤더"]])
    annotated = annotate_document(doc)
    assert _flags(annotated) == [False, False]


def test_unique_one_off_text_in_short_document_is_not_boilerplate():
    """실측 회귀 케이스(2026-07-14): 3페이지 문서에서 딱 1번 나온 고유 제목은
    boilerplate로 오판되면 안 된다."""
    doc = _doc([["1. 사업개요"], ["다른 내용"], ["또 다른 내용"]])
    annotated = annotate_document(doc)
    assert _flags(annotated) == [False, False, False]


def test_numeric_pattern_spans_are_grouped_as_boilerplate_even_with_different_values():
    """쪽번호처럼 값은 바뀌지만 패턴(숫자/로마숫자)이 반복되면 boilerplate."""
    doc = _doc([["-1-", "첫 페이지 내용"], ["-2-", "둘째 페이지 내용"], ["-3-", "셋째 페이지 내용"]])
    annotated = annotate_document(doc)
    page_flags = [[s["is_boilerplate"] for s in p["spans"]] for p in annotated["pages"]]
    assert page_flags == [[True, False], [True, False], [True, False]]


def test_occupancy_ratio_below_threshold_is_not_boilerplate():
    """최소 3페이지는 채워도 전체 대비 비율(30%)이 안 되면 boilerplate 아님."""
    pages = [["반복문구"]] * 3 + [["그냥 내용"]] * 10  # 3/13 ≈ 23% < 30%
    doc = _doc(pages)
    annotated = annotate_document(doc)
    repeated_flags = [annotated["pages"][i]["spans"][0]["is_boilerplate"] for i in range(3)]
    assert repeated_flags == [False, False, False]


def test_cleaned_text_strips_control_and_pua_chars():
    doc = _doc([["정상\x00텍스트"], ["아무 내용"], ["또 다른"]])
    annotated = annotate_document(doc)
    assert annotated["pages"][0]["spans"][0]["cleaned_text"] == "정상텍스트"


def test_document_without_pages_key_is_returned_unchanged():
    doc = {"error": "extraction failed"}
    assert annotate_document(doc) == doc


def test_original_document_is_not_mutated():
    doc = _doc([["코드"], ["코드"], ["코드"]])
    original_span = doc["pages"][0]["spans"][0]
    annotate_document(doc)
    assert "is_boilerplate" not in original_span
    assert "cleaned_text" not in original_span
