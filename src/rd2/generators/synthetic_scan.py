"""검증이 끝난 PDF를 재현 가능한 이미지 전용 합성 스캔본으로 변환한다."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import tempfile
from typing import Any

import cv2
import fitz
import numpy as np


SYNTHETIC_SCAN_DPI = 240
ROTATION_DEGREES = (-0.65, 0.65)
BLUR_SIGMA = (0.75, 1.20)
INK_SPREAD_STRENGTH = (0.20, 0.32)
BLEED_THROUGH_ALPHA = (0.04, 0.08)
JPEG_QUALITY = (80, 90)
NOISE_SIGMA = (0.9, 1.6)


def _page_seed(seed: int, page_index: int) -> int:
    digest = sha256(f"{seed}:synthetic-scan:{page_index}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _render_page(page: fitz.Page, *, dpi: int) -> np.ndarray:
    pixmap = page.get_pixmap(
        dpi=dpi,
        colorspace=fitz.csRGB,
        alpha=False,
    )
    return (
        np.frombuffer(pixmap.samples, dtype=np.uint8)
        .reshape(pixmap.height, pixmap.width, 3)
        .copy()
    )


def _paper_tone(
    image: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    height, width = image.shape[:2]
    x = np.linspace(-1, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(-1, 1, height, dtype=np.float32)[:, None]
    shading = 0.99 - 0.025 * (x * x + 0.5 * y * y)
    grain = rng.normal(0, 0.006, (height, width)).astype(np.float32)
    result = image.astype(np.float32) * (shading + grain)[..., None]
    paper = np.array([248, 246, 239], dtype=np.float32)
    result = result * 0.965 + paper * 0.035
    return np.clip(result, 0, 255).astype(np.uint8)


def _spread_ink(image: np.ndarray, *, strength: float) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
    ink = np.clip((244.0 - gray) / 244.0, 0.0, 1.0)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    spread = cv2.dilate(ink, kernel, iterations=1)
    spread = cv2.GaussianBlur(spread, (5, 5), 0.85)
    halo = np.clip(spread - ink * 0.35, 0.0, 1.0)
    result = image.astype(np.float32) * (
        1.0 - strength * halo[..., None]
    )
    return np.clip(result, 0, 255).astype(np.uint8)


def _bleed_through(
    image: np.ndarray,
    reverse_page: np.ndarray,
    rng: np.random.Generator,
    *,
    alpha: float,
) -> np.ndarray:
    ghost = cv2.flip(reverse_page, 1)
    ghost_gray = cv2.cvtColor(ghost, cv2.COLOR_RGB2GRAY)
    ghost_gray = cv2.GaussianBlur(ghost_gray, (17, 17), 4.8)
    transform = np.float32(
        [
            [1, 0, int(rng.integers(4, 12))],
            [0, 1, int(rng.integers(3, 10))],
        ]
    )
    shifted = cv2.warpAffine(
        ghost_gray,
        transform,
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    ghost_ink = np.clip(
        (246.0 - shifted.astype(np.float32)) / 246.0,
        0.0,
        1.0,
    )
    result = image.astype(np.float32) * (
        1.0 - alpha * ghost_ink[..., None]
    )
    return np.clip(result, 0, 255).astype(np.uint8)


def _scan_page(
    image: np.ndarray,
    reverse_page: np.ndarray,
    *,
    seed: int,
) -> tuple[bytes, dict[str, float | int]]:
    rng = np.random.default_rng(seed)
    ink_strength = float(rng.uniform(*INK_SPREAD_STRENGTH))
    bleed_alpha = float(rng.uniform(*BLEED_THROUGH_ALPHA))
    rotation = float(rng.uniform(*ROTATION_DEGREES))
    blur_sigma = float(rng.uniform(*BLUR_SIGMA))
    noise_sigma = float(rng.uniform(*NOISE_SIGMA))
    jpeg_quality = int(rng.integers(JPEG_QUALITY[0], JPEG_QUALITY[1] + 1))

    result = _paper_tone(image, rng)
    result = _spread_ink(result, strength=ink_strength)
    result = _bleed_through(
        result,
        reverse_page,
        rng,
        alpha=bleed_alpha,
    )

    height, width = result.shape[:2]
    matrix = cv2.getRotationMatrix2D(
        (width / 2, height / 2),
        rotation,
        1.0,
    )
    result = cv2.warpAffine(
        result,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(247, 246, 240),
    )
    result = cv2.GaussianBlur(result, (5, 5), blur_sigma)
    noise = rng.normal(0, noise_sigma, (height, width)).astype(np.float32)
    result = np.clip(
        result.astype(np.float32) + noise[..., None],
        0,
        255,
    ).astype(np.uint8)

    bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
    encoded, jpeg = cv2.imencode(
        ".jpg",
        bgr,
        [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
    )
    if not encoded:
        raise RuntimeError("Failed to encode synthetic scan page as JPEG")
    return jpeg.tobytes(), {
        "rotation_degrees": round(rotation, 3),
        "ink_spread_strength": round(ink_strength, 3),
        "bleed_through_alpha": round(bleed_alpha, 3),
        "blur_sigma": round(blur_sigma, 3),
        "noise_sigma": round(noise_sigma, 3),
        "jpeg_quality": jpeg_quality,
    }


def render_synthetic_scan_pdf(
    source_pdf: Path,
    output_pdf: Path,
    *,
    seed: int,
    dpi: int = SYNTHETIC_SCAN_DPI,
) -> dict[str, Any]:
    """PDF를 이미지 전용 스캔본으로 만든다.

    ``source_pdf``와 ``output_pdf``가 같아도 임시 파일을 거쳐 원자적으로
    교체한다. 호출자는 반드시 원문·민감정보 검증을 먼저 끝내야 한다.
    """

    source_pdf = source_pdf.resolve()
    output_pdf = output_pdf.resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    page_parameters: list[dict[str, float | int]] = []

    with fitz.open(source_pdf) as source:
        page_count = source.page_count
        if page_count < 1:
            raise ValueError("Synthetic scan source PDF has no pages")
        output = fitz.open()
        for pair_start in range(0, page_count, 2):
            pair_indices = [pair_start]
            if pair_start + 1 < page_count:
                pair_indices.append(pair_start + 1)
            pair_images = {
                index: _render_page(source[index], dpi=dpi)
                for index in pair_indices
            }
            for index in pair_indices:
                reverse_index = (
                    pair_indices[1]
                    if index == pair_indices[0] and len(pair_indices) == 2
                    else pair_indices[0]
                )
                jpeg, parameters = _scan_page(
                    pair_images[index],
                    pair_images[reverse_index],
                    seed=_page_seed(seed, index),
                )
                source_page = source[index]
                page = output.new_page(
                    width=source_page.rect.width,
                    height=source_page.rect.height,
                )
                page.insert_image(page.rect, stream=jpeg)
                page_parameters.append({"page": index + 1, **parameters})

        output.set_metadata(
            {
                "title": str(source.metadata.get("title") or ""),
                "subject": "Synthetic image-only scan derivative",
                "creator": "RD2 PyMuPDF + OpenCV synthetic scan",
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
                raise RuntimeError("Synthetic scan page count changed")
            text_characters = sum(len(page.get_text()) for page in rendered)
            if text_characters:
                raise RuntimeError(
                    "Synthetic scan PDF unexpectedly retained text"
                )
            if any(not page.get_images(full=True) for page in rendered):
                raise RuntimeError(
                    "Synthetic scan PDF contains a non-image page"
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
        "page_parameters": page_parameters,
    }
