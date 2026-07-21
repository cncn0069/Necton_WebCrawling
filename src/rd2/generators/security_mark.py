"""보안마크(대외비/군사기밀 워터마크 + 분류 스탬프) 생성 — Pillow + opencv.

2026-07-14 사용자 피드백 반영 이력:
1. 초기 버전(우측 하단 작은 사각 스탬프)이 실제 한국 관공서 대외비 문서
   관례와 너무 달라서, 참고 이미지의 **관례 형식**(워터마크+분류 박스
   스탬프)만 참고해 다시 만듦 — 참고 이미지 자체(실제 문서로 보이는
   여러 장)의 구체적 내용·구조는 쓰지 않는다. 워터마크·분류 박스라는
   "형식"은 특정 기관 고유 디자인이 아니라 한국 공문서 전반의 법정
   분류표시 관례("CONFIDENTIAL" 도장과 같은 수준의 범용 관례)라 이
   프로젝트의 법무 경계(실제 기관 고유 시각 정체성 복제 금지)와
   충돌하지 않는다.
2. 재수정: 페이지 전체를 덮는 "대외비" 타일 반복 워터마크가 아니라,
   가/나/다/라... 중 하나를 골라 큰 단일 문자로 한 번 배경에 찍는
   워터마크로 변경. "대외비" 분류 박스 스탬프는 페이지당 1회로 별개 유지.
3. 2026-07-21: 손으로 그린 "대외비" 박스 대신 logo/대외비.png(사용자가
   준비한 실제 서식 참고 원본)를 그대로 쓰도록 교체. 또한 국방부/국가정보원
   문서는 군사기밀 보호법 시행령 [별표 2](제5조제1항)의 등급별(Ⅰ/Ⅱ/Ⅲ급)
   마크(logo/1급_비밀.png 등, 사용자가 [별표 2] 도안을 참고해 준비함)를
   대신 쓴다 — generate_military_secret_mark() 참고. 두 마크 모두 노이즈
   처리(흐림·회전·잉크번짐)는 기존과 동일하게 적용한다.

Genalog는 이 프로젝트 Python 버전(3.14.6)에서 설치가 안 되는 게 확인돼
(pip install genalog 재현 시 numpy 소스 빌드 실패) 대신 Pillow+opencv로
직접 구현한다.

C(기밀) 문서에만 이 마크를 적용한다 — S(민감) 문서는 마크 없음(사용자 결정,
2026-07-14). 설계 문서:
~/.gstack/projects/cncn0069-Necton_WebCrawling/
안정현-feat-open-go-kr-alternative-sources-design-20260714-144123.md
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from rd2.generators.agency_resolver import MILITARY_SECRET_MARK_FILENAMES

_WATERMARK_CHARS = ("가", "나", "다", "라", "마", "바", "사")
WATERMARK_VARIANT_COUNT = len(_WATERMARK_CHARS)
_FONT_DIR = Path(__file__).with_name("assets") / "fonts"
_KOREAN_FONT_PATH = str(_FONT_DIR / "NotoSansKR-Regular.ttf")
_KOREAN_FONT_BOLD_PATH = str(_FONT_DIR / "NotoSansKR-Bold.ttf")

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_LOGO_DIR = _REPO_ROOT / "logo"
_CONFIDENTIAL_MARK_ASSET = _LOGO_DIR / "대외비.png"

# A4 @ ~150dpi (reportlab A4는 pt 단위 595x842 — 150dpi로 래스터화)
_PAGE_SIZE_PX = (1240, 1754)
_STAMP_SIZE_PX = (260, 100)
_WATERMARK_FONT_SIZE = 620


def _load_font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _draw_single_character_watermark(seed: int) -> Image.Image:
    """가/나/다/라... 중 seed로 결정되는 문자 하나를 페이지 중앙에 크고
    옅게 한 번 찍는다 — 페이지 전체를 덮는 타일 반복이 아니다.
    """
    width, height = _PAGE_SIZE_PX
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    char = _WATERMARK_CHARS[seed % len(_WATERMARK_CHARS)]
    font = _load_font(_KOREAN_FONT_BOLD_PATH, _WATERMARK_FONT_SIZE)
    bbox = draw.textbbox((0, 0), char, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - text_w) / 2 - bbox[0]
    y = (height - text_h) / 2 - bbox[1]
    draw.text((x, y), char, font=font, fill=(140, 140, 140, 60))

    return img


def _load_mark_asset(asset_path: Path) -> Image.Image:
    """logo/ 폴더의 실제 마크 원본을 표준 스탬프 캔버스 크기로 맞춘다(비율 유지, 중앙 배치)."""
    source = Image.open(asset_path).convert("RGBA")
    canvas = Image.new("RGBA", _STAMP_SIZE_PX, (255, 255, 255, 0))
    scale = min(_STAMP_SIZE_PX[0] / source.width, _STAMP_SIZE_PX[1] / source.height)
    new_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    resized = source.resize(new_size, Image.LANCZOS)
    offset = ((_STAMP_SIZE_PX[0] - new_size[0]) // 2, (_STAMP_SIZE_PX[1] - new_size[1]) // 2)
    canvas.paste(resized, offset, resized)
    return canvas


def _apply_noise(img: Image.Image, *, seed: int, angle_range: float = 3.0) -> Image.Image:
    """흐림(가우시안 블러) + 미세 회전(어파인) + 잉크번짐(알파 채널 팽창)."""
    rng = np.random.default_rng(seed)
    rgba = np.array(img)
    bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

    blurred = cv2.GaussianBlur(bgra, (3, 3), sigmaX=0.8)

    angle = float(rng.uniform(-angle_range, angle_range))
    height, width = blurred.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        blurred, matrix, (width, height),
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255, 0),
    )

    alpha = rotated[:, :, 3]
    kernel = np.ones((2, 2), np.uint8)
    rotated[:, :, 3] = cv2.dilate(alpha, kernel, iterations=1)

    noisy_rgba = cv2.cvtColor(rotated, cv2.COLOR_BGRA2RGBA)
    return Image.fromarray(noisy_rgba, mode="RGBA")


def generate_page_watermark(output_path: Path, *, seed: int = 0) -> Path:
    """가/나/다/라... 중 seed로 고른 문자 하나를 큰 배경 워터마크로 만든다(C 전용).

    문서마다 seed가 다르면(예: PRISM 문서 id 기반) 다른 문자가 나온다.
    """
    base = _draw_single_character_watermark(seed)
    noisy = _apply_noise(base, seed=seed, angle_range=2.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_classification_stamp(output_path: Path, *, seed: int = 0) -> Path:
    """"대외비" 분류 마크 PNG를 만든다(C 전용, 군사기밀 대상 기관 제외).

    agency_resolver.is_military_secret_agency()가 True인 문서(국방부·국가정보원)는
    이 마크 대신 generate_military_secret_mark()를 써야 한다.
    """
    base = _load_mark_asset(_CONFIDENTIAL_MARK_ASSET)
    noisy = _apply_noise(base, seed=seed, angle_range=8.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_military_secret_mark(output_path: Path, grade: str, *, seed: int = 0) -> Path:
    """군사기밀 보호법 시행령 [별표 2](제5조제1항) 등급별("1급"/"2급"/"3급") 마크 PNG를 만든다.

    국방부/국가정보원 문서에만 쓴다(agency_resolver.is_military_secret_agency) — 나머지
    C(기밀) 문서는 generate_classification_stamp()의 "대외비" 마크를 그대로 쓴다.
    [별표 2] "가"목: 기밀문서는 앞뒷면 표지와 기밀이 포함된 면마다 상단·하단 중앙에
    이 마크를 표시한다 — 이 함수는 마크 이미지 한 장만 만들고, 상단·하단 배치는
    pdf_render.py(템플릿의 stamp/stamp-top 슬롯)가 담당한다.
    """
    if grade not in MILITARY_SECRET_MARK_FILENAMES:
        raise ValueError(f"알 수 없는 군사기밀 등급: {grade!r}")
    asset_path = _LOGO_DIR / MILITARY_SECRET_MARK_FILENAMES[grade]
    base = _load_mark_asset(asset_path)
    noisy = _apply_noise(base, seed=seed, angle_range=8.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_security_mark(output_path: Path, *, seed: int = 0) -> Path:
    """하위호환용 별칭 — 분류 박스 스탬프만 생성한다."""
    return generate_classification_stamp(output_path, seed=seed)
