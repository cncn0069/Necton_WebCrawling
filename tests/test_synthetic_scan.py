from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import fitz

from rd2.generators.synthetic_scan import (
    BLEED_THROUGH_ALPHA,
    BLUR_SIGMA,
    INK_SPREAD_STRENGTH,
    JPEG_QUALITY,
    NOISE_SIGMA,
    ROTATION_DEGREES,
    render_synthetic_scan_pdf,
)


def _source_pdf(path: Path, *, pages: int = 4) -> None:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=595.276, height=841.89)
        page.insert_text(
            (72, 90),
            f"Synthetic scan test page {index + 1}",
            fontsize=22,
        )
        for line in range(12):
            page.insert_text(
                (72, 145 + line * 34),
                f"Line {line + 1:02d} - document text for scan effects",
                fontsize=12,
            )
    document.save(path)
    document.close()


def _page_digests(path: Path) -> list[str]:
    with fitz.open(path) as document:
        return [
            sha256(page.get_pixmap(dpi=96, alpha=False).samples).hexdigest()
            for page in document
        ]


def test_synthetic_scan_is_image_only_reproducible_and_bidirectional(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _source_pdf(first)
    second.write_bytes(first.read_bytes())

    first_result = render_synthetic_scan_pdf(
        first,
        first,
        seed=7719,
    )
    second_result = render_synthetic_scan_pdf(
        second,
        second,
        seed=7719,
    )

    assert first_result == second_result
    assert first_result["image_only"] is True
    assert first_result["page_count"] == 4
    assert _page_digests(first) == _page_digests(second)

    with fitz.open(first) as rendered:
        assert rendered.page_count == 4
        assert all(not page.get_text() for page in rendered)
        assert all(len(page.get_images(full=True)) == 1 for page in rendered)

    parameters = first_result["page_parameters"]
    rotations = [item["rotation_degrees"] for item in parameters]
    assert min(rotations) < 0 < max(rotations)
    for item in parameters:
        assert ROTATION_DEGREES[0] <= item["rotation_degrees"] <= ROTATION_DEGREES[1]
        assert BLUR_SIGMA[0] <= item["blur_sigma"] <= BLUR_SIGMA[1]
        assert INK_SPREAD_STRENGTH[0] <= item["ink_spread_strength"] <= INK_SPREAD_STRENGTH[1]
        assert BLEED_THROUGH_ALPHA[0] <= item["bleed_through_alpha"] <= BLEED_THROUGH_ALPHA[1]
        assert NOISE_SIGMA[0] <= item["noise_sigma"] <= NOISE_SIGMA[1]
        assert JPEG_QUALITY[0] <= item["jpeg_quality"] <= JPEG_QUALITY[1]
