"""검증이 끝난 PDF를 한윤체 기반 이미지 전용 손글씨본으로 변환한다."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from hashlib import sha256
import io
import os
from pathlib import Path
import random
import tempfile
from typing import Any

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont


SYNTHETIC_HANDWRITING_DPI = 144
SYNTHETIC_HANDWRITING_RATE = 1 / 8
HANDWRITING_FONT_NAME = "NanumHanYunCe"
FALLBACK_FONT_NAME = "NotoSansKR-Regular"

_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_HANDWRITING_FONT_PATH = (
    _ASSET_DIR / "handwriting" / f"{HANDWRITING_FONT_NAME}.ttf"
)
_FALLBACK_FONT_PATH = _ASSET_DIR / f"{FALLBACK_FONT_NAME}.ttf"


def _page_seed(seed: int, page_index: int) -> int:
    digest = sha256(
        f"{seed}:synthetic-handwriting:{page_index}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big")


@lru_cache(maxsize=256)
def _pil_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=max(6, size))


def _span_rgb(color_value: int) -> tuple[int, int, int]:
    color = (
        (color_value >> 16) & 255,
        (color_value >> 8) & 255,
        color_value & 255,
    )
    if sum(color) < 90:
        return (24, 35, 52)
    return color


def _dominant_background(
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> tuple[int, int, int]:
    x0, y0, x1, y1 = box
    x0 = max(0, min(image.width - 1, x0))
    y0 = max(0, min(image.height - 1, y0))
    x1 = max(x0 + 1, min(image.width, x1))
    y1 = max(y0 + 1, min(image.height, y1))
    pixels = np.asarray(
        image.crop((x0, y0, x1, y1)).convert("RGB"),
        dtype=np.uint8,
    ).reshape(-1, 3)
    quantized = (
        (pixels.astype(np.uint16) // 12) * 12 + 6
    ).clip(0, 255).astype(np.uint8)
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    selected = colors[int(np.argmax(counts))]
    return tuple(int(channel) for channel in selected)


def _text_spans(
    page: fitz.Page,
    *,
    protected_margin_pt: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    safe_rect = None
    if protected_margin_pt is not None:
        top_bottom, left_right = protected_margin_pt
        safe_rect = fitz.Rect(
            left_right,
            top_bottom,
            page.rect.width - left_right,
            page.rect.height - top_bottom,
        )
    spans: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not str(span.get("text", "")).strip():
                    continue
                span_rect = fitz.Rect(span["bbox"])
                span_center = fitz.Point(
                    (span_rect.x0 + span_rect.x1) / 2,
                    (span_rect.y0 + span_rect.y1) / 2,
                )
                if safe_rect is not None and not safe_rect.contains(span_center):
                    # C/군사기밀 프레임의 상·하단 라벨과 합성 provenance는
                    # 정규 보안표지다. 손글씨 효과가 그 문구를 필기체로 바꾸면
                    # 한 문서 안에서 본문 효과와 보안 등급 표현이 충돌한다.
                    continue
                spans.append(span)
    return spans


def _render_handwriting_page(
    page: fitz.Page,
    *,
    seed: int,
    dpi: int,
    handwriting_font: fitz.Font,
    protected_margin_pt: tuple[float, float] | None = None,
) -> tuple[bytes, Counter[str]]:
    scale = dpi / 72
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    original = Image.frombytes(
        "RGB",
        (pixmap.width, pixmap.height),
        pixmap.samples,
    )
    result = original.copy()
    fallback_characters: Counter[str] = Counter()
    rng = random.Random(seed)

    for span in _text_spans(
        page,
        protected_margin_pt=protected_margin_pt,
    ):
        text = str(span.get("text", ""))
        x0, y0, x1, y1 = span["bbox"]
        origin_x, baseline_y = span.get("origin", (x0, y1))
        pixel_box = (
            int(round(x0 * scale)) - 1,
            int(round(y0 * scale)) - 1,
            int(round(x1 * scale)) + 2,
            int(round(y1 * scale)) + 2,
        )
        background = _dominant_background(original, pixel_box)
        ImageDraw.Draw(result).rectangle(pixel_box, fill=background)

        base_size = max(
            8,
            int(round(float(span.get("size", 10)) * scale * 0.94)),
        )
        available_width = max(1.0, (x1 - x0) * scale)

        def font_path(char: str) -> Path:
            if handwriting_font.has_glyph(ord(char)):
                return _HANDWRITING_FONT_PATH
            return _FALLBACK_FONT_PATH

        measured_width = sum(
            _pil_font(str(font_path(char)), base_size).getlength(char)
            for char in text
        )
        if measured_width > available_width * 0.985:
            base_size = max(
                6,
                int(
                    base_size
                    * available_width
                    * 0.985
                    / measured_width
                ),
            )

        cursor_x = origin_x * scale
        baseline = baseline_y * scale
        overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        color = _span_rgb(int(span.get("color", 0)))

        for char in text:
            selected_path = font_path(char)
            uses_fallback = selected_path == _FALLBACK_FONT_PATH
            font = _pil_font(str(selected_path), base_size)
            advance = font.getlength(char)
            if not char.isspace():
                if uses_fallback:
                    fallback_characters[char] += 1
                    x_jitter = 0.0
                    y_jitter = 0.0
                    alpha = 245
                else:
                    x_jitter = rng.uniform(-0.30, 0.30)
                    y_jitter = rng.uniform(-0.55, 0.55)
                    alpha = rng.randint(220, 250)
                draw.text(
                    (cursor_x + x_jitter, baseline + y_jitter),
                    char,
                    font=font,
                    fill=(*color, alpha),
                    anchor="ls",
                )
            cursor_x += advance
            if not uses_fallback:
                cursor_x += rng.uniform(-0.08, 0.10)

        result = Image.alpha_composite(
            result.convert("RGBA"),
            overlay,
        ).convert("RGB")

    output = io.BytesIO()
    result.save(output, format="PNG", optimize=True)
    return output.getvalue(), fallback_characters


def render_synthetic_handwriting_pdf(
    source_pdf: Path,
    output_pdf: Path,
    *,
    seed: int,
    dpi: int = SYNTHETIC_HANDWRITING_DPI,
    protected_margin_pt: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """PDF를 한윤체 기반 이미지 전용 손글씨본으로 만든다.

    한윤체에 없는 글자는 Noto Sans KR로 자동 폴백한다. 입력·출력 경로가
    같아도 임시 파일을 거쳐 원자적으로 교체한다. 호출자는 원문·민감정보
    검증을 먼저 끝내야 한다.
    """

    source_pdf = source_pdf.resolve()
    output_pdf = output_pdf.resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    fallback_characters: Counter[str] = Counter()

    handwriting_font = fitz.Font(fontfile=str(_HANDWRITING_FONT_PATH))
    with fitz.open(source_pdf) as source:
        page_count = source.page_count
        if page_count < 1:
            raise ValueError("Synthetic handwriting source PDF has no pages")
        output = fitz.open()
        for page_index in range(page_count):
            png, page_fallbacks = _render_handwriting_page(
                source[page_index],
                seed=_page_seed(seed, page_index),
                dpi=dpi,
                handwriting_font=handwriting_font,
                protected_margin_pt=protected_margin_pt,
            )
            fallback_characters.update(page_fallbacks)
            source_page = source[page_index]
            page = output.new_page(
                width=source_page.rect.width,
                height=source_page.rect.height,
            )
            page.insert_image(page.rect, stream=png)

        output.set_metadata(
            {
                "title": str(source.metadata.get("title") or ""),
                "subject": "Synthetic image-only handwriting derivative",
                "creator": (
                    "RD2 PyMuPDF + Pillow synthetic handwriting"
                ),
            }
        )
        pdf_bytes = output.tobytes(garbage=4, deflate=True)
        output.close()

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_pdf.stem}-",
        suffix=".tmp.pdf",
        dir=output_pdf.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(pdf_bytes)
        with fitz.open(temporary_path) as rendered:
            if rendered.page_count != page_count:
                raise RuntimeError("Synthetic handwriting page count changed")
            text_characters = sum(len(page.get_text()) for page in rendered)
            if text_characters:
                raise RuntimeError(
                    "Synthetic handwriting PDF unexpectedly retained text"
                )
            if any(not page.get_images(full=True) for page in rendered):
                raise RuntimeError(
                    "Synthetic handwriting PDF contains a non-image page"
                )
        os.replace(temporary_path, output_pdf)
    finally:
        temporary_path.unlink(missing_ok=True)

    return {
        "applied": True,
        "seed": seed,
        "dpi": dpi,
        "image_only": True,
        "page_count": page_count,
        "font": HANDWRITING_FONT_NAME,
        "fallback_font": FALLBACK_FONT_NAME,
        "fallback_character_count": sum(fallback_characters.values()),
        "fallback_characters": dict(sorted(fallback_characters.items())),
        "protected_margin_pt": (
            {
                "top_bottom": protected_margin_pt[0],
                "left_right": protected_margin_pt[1],
            }
            if protected_margin_pt is not None
            else None
        ),
    }
