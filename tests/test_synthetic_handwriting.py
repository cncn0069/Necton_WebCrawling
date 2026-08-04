from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import fitz

from rd2.generators.synthetic_handwriting import (
    FALLBACK_FONT_NAME,
    HANDWRITING_FONT_NAME,
    render_synthetic_handwriting_pdf,
)


_FONT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "rd2"
    / "generators"
    / "assets"
    / "fonts"
    / "NotoSansKR-Regular.ttf"
)


def _source_pdf(path: Path) -> list[tuple[float, float]]:
    document = fitz.open()
    page_sizes = [(595.276, 841.89), (841.89, 595.276)]
    for index, (width, height) in enumerate(page_sizes):
        page = document.new_page(width=width, height=height)
        page.insert_font(fontname="notokr", fontfile=str(_FONT_PATH))
        page.draw_rect(
            fitz.Rect(54, 70, width - 54, 175),
            color=(0.1, 0.25, 0.4),
            fill=(0.94, 0.97, 0.98),
        )
        page.insert_text(
            (72, 115),
            f"페이지 {index + 1} · 민원 안내 • 자동 처리 62%",
            fontname="notokr",
            fontsize=18,
            color=(0.08, 0.15, 0.25),
        )
        page.insert_text(
            (72, 155),
            "상담로그·설문·면담·업무기록",
            fontname="notokr",
            fontsize=13,
        )
    document.save(path)
    document.close()
    return page_sizes


def _page_digests(path: Path) -> list[str]:
    with fitz.open(path) as document:
        return [
            sha256(page.get_pixmap(dpi=96, alpha=False).samples).hexdigest()
            for page in document
        ]


def test_handwriting_is_reproducible_image_only_and_uses_glyph_fallback(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    expected_sizes = _source_pdf(first)
    second.write_bytes(first.read_bytes())

    first_result = render_synthetic_handwriting_pdf(
        first,
        first,
        seed=7719,
    )
    second_result = render_synthetic_handwriting_pdf(
        second,
        second,
        seed=7719,
    )

    assert first_result == second_result
    assert first_result["image_only"] is True
    assert first_result["page_count"] == 2
    assert first_result["font"] == HANDWRITING_FONT_NAME
    assert first_result["fallback_font"] == FALLBACK_FONT_NAME
    assert first_result["fallback_character_count"] == 10
    assert first_result["fallback_characters"] == {"·": 8, "•": 2}
    assert _page_digests(first) == _page_digests(second)

    with fitz.open(first) as rendered:
        assert rendered.page_count == 2
        assert [
            (round(page.rect.width, 3), round(page.rect.height, 3))
            for page in rendered
        ] == [
            (round(width, 3), round(height, 3))
            for width, height in expected_sizes
        ]
        assert all(not page.get_text() for page in rendered)
        assert all(len(page.get_images(full=True)) == 1 for page in rendered)
