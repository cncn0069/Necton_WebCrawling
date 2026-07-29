"""재현 가능한 학습용 합성 결재 도장을 생성한다.

실제 기관 직인 이미지를 복제하지 않는다. 입력 계약의 ``stamp_text``만 사용해
새 도장을 그린 뒤, seed 기반으로 인주 농도·압력·파손·번짐을 변형한다.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import asdict, dataclass
from io import BytesIO
import math
from pathlib import Path
import random
import re
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

StampProfile = Literal[
    "normal",
    "light_ink",
    "uneven_pressure",
    "damaged",
    "dry_ink",
    "wet_blur",
]
StampShape = Literal["round", "square", "oval"]
StampPlacementMode = Literal["standard", "boundary", "lower_overlap"]

STAMP_PROFILES: tuple[StampProfile, ...] = (
    "normal",
    "light_ink",
    "uneven_pressure",
    "damaged",
    "dry_ink",
    "wet_blur",
)
STAMP_SHAPES: tuple[StampShape, ...] = ("round", "square", "oval")

_PROFILE_WEIGHTS: tuple[StampProfile, ...] = (
    "normal",
    "normal",
    "normal",
    "normal",
    "light_ink",
    "light_ink",
    "uneven_pressure",
    "uneven_pressure",
    "damaged",
    "dry_ink",
    "wet_blur",
)
_FONT_PATH = Path(__file__).resolve().parent / "assets/fonts/NotoSansKR-Bold.ttf"
_CANVAS_SIZE = 420


@dataclass(frozen=True)
class StampParameters:
    """manifest에 기록하는 실제 도장 변형값."""

    profile: StampProfile
    shape: StampShape
    seed: int
    opacity: float
    ink_coverage: float
    pressure_imbalance: float
    edge_damage: float
    blur_px: float
    rotation_deg: float
    color: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SyntheticApprovalStamp:
    """렌더링용 PNG와 재현 파라미터."""

    data_uri: str
    png_bytes: bytes
    parameters: StampParameters


@dataclass(frozen=True)
class StampPlacement:
    """결재칸 안에서 도장을 놓을 재현 가능한 위치."""

    mode: StampPlacementMode
    seed: int
    left_percent: float
    top_percent: float
    crosses_boundary: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_PROFILE_BASES: dict[StampProfile, dict[str, float]] = {
    "normal": {
        "opacity": 0.90,
        "ink_coverage": 0.985,
        "pressure_imbalance": 0.08,
        "edge_damage": 0.025,
        "blur_px": 0.20,
    },
    "light_ink": {
        "opacity": 0.62,
        "ink_coverage": 0.88,
        "pressure_imbalance": 0.18,
        "edge_damage": 0.07,
        "blur_px": 0.28,
    },
    "uneven_pressure": {
        "opacity": 0.84,
        "ink_coverage": 0.94,
        "pressure_imbalance": 0.52,
        "edge_damage": 0.08,
        "blur_px": 0.26,
    },
    "damaged": {
        "opacity": 0.83,
        "ink_coverage": 0.91,
        "pressure_imbalance": 0.20,
        "edge_damage": 0.38,
        "blur_px": 0.24,
    },
    "dry_ink": {
        "opacity": 0.75,
        "ink_coverage": 0.72,
        "pressure_imbalance": 0.25,
        "edge_damage": 0.17,
        "blur_px": 0.10,
    },
    "wet_blur": {
        "opacity": 0.80,
        "ink_coverage": 0.97,
        "pressure_imbalance": 0.14,
        "edge_damage": 0.05,
        "blur_px": 1.45,
    },
}


def _resolve_profile(seed: int, profile: StampProfile | None) -> StampProfile:
    if profile is not None:
        return profile
    return random.Random(seed ^ 0x5EA1).choice(_PROFILE_WEIGHTS)


def _resolve_shape(
    seed: int,
    stamp_text: str,
    shape: StampShape | None,
) -> StampShape:
    if shape is not None:
        return shape
    text_length = len(_compact_stamp_text(stamp_text))
    rng = random.Random(seed ^ 0x5A9E)
    if text_length <= 5:
        return rng.choice(("square", "square", "round"))
    if text_length >= 9:
        return rng.choice(("round", "round", "round", "oval", "oval"))
    return rng.choice(("round", "round", "oval", "square"))


def _jitter(
    rng: random.Random,
    base: float,
    *,
    spread: float,
    lower: float,
    upper: float,
) -> float:
    return round(min(upper, max(lower, base + rng.uniform(-spread, spread))), 3)


def _build_parameters(
    seed: int,
    profile: StampProfile,
    shape: StampShape,
) -> StampParameters:
    rng = random.Random(seed)
    base = _PROFILE_BASES[profile]
    red = rng.randint(155, 181)
    green = rng.randint(39, 57)
    blue = rng.randint(35, 50)
    return StampParameters(
        profile=profile,
        shape=shape,
        seed=seed,
        opacity=_jitter(
            rng,
            base["opacity"],
            spread=0.045,
            lower=0.40,
            upper=0.96,
        ),
        ink_coverage=_jitter(
            rng,
            base["ink_coverage"],
            spread=0.025,
            lower=0.58,
            upper=0.998,
        ),
        pressure_imbalance=_jitter(
            rng,
            base["pressure_imbalance"],
            spread=0.035,
            lower=0.02,
            upper=0.62,
        ),
        edge_damage=_jitter(
            rng,
            base["edge_damage"],
            spread=0.025,
            lower=0.0,
            upper=0.48,
        ),
        blur_px=_jitter(
            rng,
            base["blur_px"],
            spread=0.08,
            lower=0.0,
            upper=1.8,
        ),
        rotation_deg=round(
            rng.uniform(-8.0, 8.0)
            if rng.random() < 0.10
            else rng.uniform(-5.0, 5.0),
            2,
        ),
        color=f"#{red:02x}{green:02x}{blue:02x}",
    )


def _compact_stamp_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _draw_text_grid(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    box: tuple[float, float, float, float],
    columns: int,
) -> None:
    rows = math.ceil(len(text) / columns)
    cell_width = (box[2] - box[0]) / columns
    cell_height = (box[3] - box[1]) / rows
    font_size = max(42, min(112, int(min(cell_width, cell_height) * 0.88)))
    text_font = ImageFont.truetype(str(_FONT_PATH), font_size)

    for index, character in enumerate(text):
        row = index // columns
        column = index % columns
        cell_left = box[0] + column * cell_width
        cell_top = box[1] + row * cell_height
        bounds = draw.textbbox((0, 0), character, font=text_font)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        x = cell_left + (cell_width - width) / 2 - bounds[0]
        y = cell_top + (cell_height - height) / 2 - bounds[1]
        draw.text((x, y), character, fill=255, font=text_font)


def _draw_oval_text(draw: ImageDraw.ImageDraw, text: str) -> None:
    split_at = math.ceil(len(text) / 2)
    lines = (text,) if len(text) <= 7 else (
        text[:split_at],
        text[split_at:],
    )
    max_characters = max(len(line) for line in lines)
    font_size = max(34, min(64, int(285 / max_characters)))
    text_font = ImageFont.truetype(str(_FONT_PATH), font_size)
    line_height = font_size * 1.12
    total_height = line_height * len(lines)
    for index, line in enumerate(lines):
        bounds = draw.textbbox((0, 0), line, font=text_font)
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        x = (_CANVAS_SIZE - width) / 2 - bounds[0]
        y = (
            (_CANVAS_SIZE - total_height) / 2
            + index * line_height
            + (line_height - height) / 2
            - bounds[1]
        )
        draw.text((x, y), line, fill=255, font=text_font)


def _draw_stamp_master(
    stamp_text: str,
    shape: StampShape,
) -> Image.Image:
    """원형·사각·타원 테두리와 문자를 고해상도 알파 마스크에 그린다."""

    size = _CANVAS_SIZE
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    text = _compact_stamp_text(stamp_text)
    if not text:
        raise ValueError("stamp_text must not be blank")
    if len(text) > 16:
        raise ValueError("stamp_text must contain at most 16 non-space characters")

    if shape == "square":
        draw.rounded_rectangle(
            (34, 34, size - 34, size - 34),
            radius=18,
            outline=255,
            width=17,
        )
        draw.rounded_rectangle(
            (59, 59, size - 59, size - 59),
            radius=10,
            outline=225,
            width=5,
        )
        columns = 1 if len(text) <= 3 else 2 if len(text) <= 8 else 3
        _draw_text_grid(
            draw,
            text,
            box=(92, 92, size - 92, size - 92),
            columns=columns,
        )
    elif shape == "oval":
        draw.ellipse(
            (22, 90, size - 22, size - 90),
            outline=255,
            width=16,
        )
        draw.ellipse(
            (47, 111, size - 47, size - 111),
            outline=225,
            width=5,
        )
        _draw_oval_text(draw, text)
    else:
        outer = 24
        draw.ellipse(
            (outer, outer, size - outer, size - outer),
            outline=255,
            width=15,
        )
        draw.ellipse(
            (outer + 23, outer + 23, size - outer - 23, size - outer - 23),
            outline=230,
            width=4,
        )
        columns = 1 if len(text) <= 3 else 2 if len(text) <= 8 else 3
        _draw_text_grid(
            draw,
            text,
            box=(85, 85, size - 85, size - 85),
            columns=columns,
        )
    return mask


def _apply_edge_damage(
    mask: Image.Image,
    amount: float,
    rng: random.Random,
    shape: StampShape,
) -> None:
    if amount <= 0:
        return
    damage = Image.new("L", mask.size, 0)
    draw = ImageDraw.Draw(damage)
    center = _CANVAS_SIZE / 2
    count = max(1, round(3 + amount * 28))
    for _ in range(count):
        if shape == "square":
            side = rng.randrange(4)
            progress = rng.uniform(42, _CANVAS_SIZE - 42)
            coordinates = (
                (progress, 38),
                (_CANVAS_SIZE - 38, progress),
                (progress, _CANVAS_SIZE - 38),
                (38, progress),
            )
            x, y = coordinates[side]
        else:
            angle = rng.uniform(0, math.tau)
            radius_x = _CANVAS_SIZE / 2 - 31
            radius_y = 114 if shape == "oval" else radius_x
            x = center + math.cos(angle) * radius_x
            y = center + math.sin(angle) * radius_y
        width = rng.uniform(5, 15 + amount * 34)
        height = rng.uniform(4, 11 + amount * 26)
        draw.ellipse(
            (x - width, y - height, x + width, y + height),
            fill=255,
        )
    alpha = np.asarray(mask, dtype=np.uint8).copy()
    alpha[np.asarray(damage, dtype=np.uint8) > 0] = 0
    mask.paste(Image.fromarray(alpha, mode="L"))


def _distress_mask(
    master: Image.Image,
    parameters: StampParameters,
    rng: random.Random,
) -> Image.Image:
    mask = master.copy()
    _apply_edge_damage(
        mask,
        parameters.edge_damage,
        rng,
        parameters.shape,
    )
    alpha = np.asarray(mask, dtype=np.float32)
    np_rng = np.random.default_rng(parameters.seed ^ 0xD157)

    angle = rng.uniform(0, math.tau)
    axis_x = math.cos(angle)
    axis_y = math.sin(angle)
    coordinates = np.linspace(-1.0, 1.0, _CANVAS_SIZE, dtype=np.float32)
    xx, yy = np.meshgrid(coordinates, coordinates)
    pressure = 1.0 - parameters.pressure_imbalance * (
        0.5 + 0.5 * (axis_x * xx + axis_y * yy)
    )
    pressure = np.clip(pressure, 0.32, 1.0)
    alpha *= pressure

    dropout = 1.0 - parameters.ink_coverage
    if dropout > 0.002:
        fine_noise = np_rng.random(alpha.shape)
        alpha[fine_noise < dropout * 0.48] = 0

        coarse = np_rng.random((32, 32), dtype=np.float32)
        coarse_image = Image.fromarray(
            np.uint8(coarse * 255),
            mode="L",
        ).resize(alpha.shape[::-1], Image.Resampling.BILINEAR)
        coarse_noise = np.asarray(coarse_image, dtype=np.float32) / 255
        threshold = min(0.34, dropout * 1.08)
        alpha[coarse_noise < threshold] *= 0.16

    alpha *= parameters.opacity
    distressed = Image.fromarray(
        np.uint8(np.clip(alpha, 0, 255)),
        mode="L",
    )
    if parameters.blur_px:
        distressed = distressed.filter(
            ImageFilter.GaussianBlur(parameters.blur_px)
        )
    return distressed


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(
        int(value[index : index + 2], 16)
        for index in (1, 3, 5)
    )


def _compose_stamp(
    mask: Image.Image,
    parameters: StampParameters,
    rng: random.Random,
) -> Image.Image:
    red, green, blue = _hex_to_rgb(parameters.color)
    image = Image.new("RGBA", mask.size, (red, green, blue, 0))
    image.putalpha(mask)

    if parameters.profile == "wet_blur":
        ghost = image.filter(ImageFilter.GaussianBlur(1.0))
        ghost_alpha = ghost.getchannel("A").point(lambda value: int(value * 0.28))
        ghost.putalpha(ghost_alpha)
        offset = (rng.choice((-3, -2, 2, 3)), rng.choice((-2, -1, 1, 2)))
        composite = Image.new("RGBA", image.size, (0, 0, 0, 0))
        composite.alpha_composite(ghost, dest=offset)
        composite.alpha_composite(image)
        image = composite

    return image.rotate(
        parameters.rotation_deg,
        resample=Image.Resampling.BICUBIC,
        expand=False,
    )


def build_stamp_placement(seed: int) -> StampPlacement:
    """승인된 위치 비율(일반 55%, 경계 25%, 하단 겹침 20%)을 적용한다."""

    rng = random.Random(seed ^ 0x91ACE)
    selection = rng.random()
    if selection < 0.55:
        mode: StampPlacementMode = "standard"
        left_percent = rng.uniform(38.0, 62.0)
        top_percent = rng.uniform(38.0, 62.0)
        crosses_boundary = False
    elif selection < 0.80:
        mode = "boundary"
        left_percent = (
            rng.uniform(4.0, 10.0)
            if rng.random() < 0.5
            else rng.uniform(90.0, 96.0)
        )
        top_percent = rng.uniform(40.0, 66.0)
        crosses_boundary = True
    else:
        mode = "lower_overlap"
        left_percent = rng.uniform(36.0, 64.0)
        top_percent = rng.uniform(82.0, 94.0)
        crosses_boundary = True

    return StampPlacement(
        mode=mode,
        seed=seed,
        left_percent=round(left_percent, 2),
        top_percent=round(top_percent, 2),
        crosses_boundary=crosses_boundary,
    )


def generate_synthetic_approval_stamp(
    stamp_text: str,
    *,
    seed: int,
    profile: StampProfile | None = None,
    shape: StampShape | None = None,
) -> SyntheticApprovalStamp:
    """입력 문자열로 실제 기관과 무관한 합성 결재 도장 PNG를 만든다."""

    resolved_profile = _resolve_profile(seed, profile)
    resolved_shape = _resolve_shape(seed, stamp_text, shape)
    parameters = _build_parameters(seed, resolved_profile, resolved_shape)
    rng = random.Random(seed ^ 0xA991)
    master = _draw_stamp_master(stamp_text, resolved_shape)
    mask = _distress_mask(master, parameters, rng)
    image = _compose_stamp(mask, parameters, rng)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    png_bytes = buffer.getvalue()
    data_uri = "data:image/png;base64," + b64encode(png_bytes).decode("ascii")
    return SyntheticApprovalStamp(
        data_uri=data_uri,
        png_bytes=png_bytes,
        parameters=parameters,
    )
