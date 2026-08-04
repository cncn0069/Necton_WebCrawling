"""보안마크(대외비/군사기밀 워터마크 + 분류 스탬프) 생성 — Pillow + opencv.

최신 ``doc_type``별 PDF 생성기는 렌더 후
``document_security_marking.py``에서 기관 워터마크와 분류표지를 합성한다.
기관별 자산 매핑, 투명도, 크기, 레이어 순서는 ``logo/README.md``를 기준으로
한다. 이 파일의 생성 함수는 기존 파일럿/레거시 이미지 합성 경로를 유지한다.

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

SYNTHETIC_SECURITY_STAMP_LABELS: tuple[str, ...] = (
    "CONFIDENTIAL",
    "TOP SECRET",
    "RESTRICTED",
    "NEED TO KNOW",
)
_SYNTHETIC_STAMP_SIZE_PX = (720, 220)
_SYNTHETIC_STAMP_INK = (34, 39, 44, 255)

# A4 @ ~150dpi (reportlab A4는 pt 단위 595x842 — 150dpi로 래스터화)
_PAGE_SIZE_PX = (1240, 1754)
_STAMP_SIZE_PX = (260, 100)
_WATERMARK_FONT_SIZE = 620
_LETTERHEAD_MARK_SIZE_PX = (170, 170)  # 좌상단 기관 마크(레터헤드) 캔버스
_AGENCY_WATERMARK_BOX_PX = (900, 900)  # 배경 워터마크용 기관 마크 캔버스
_AGENCY_WATERMARK_ALPHA = 60  # 글자 워터마크 fill=(140,140,140,60)과 같은 톤

# 비밀표시 규정 제9항 붉은 문구 + [별표 2] 7호 재분류 근거 박스 공용 폰트 크기/색.
_NOTICE_FONT_SIZE = 26
_NOTICE_TEXT_COLOR = (200, 0, 0, 255)
_MILITARY_SECRET_CONTENT_NOTICE_TEXT = "이 비밀에는 군사기밀 사항이 포함되어 있습니다"

# security_mark.py의 다른 마크와 같은 150dpi 래스터화 기준 cm→px 환산
# (_PAGE_SIZE_PX 주석 참고, 1cm = dpi/2.54 px) — 비밀표시 규정 제9항 박스와
# [별표 2] 7호 재분류 근거 박스가 공유한다.
_PX_PER_CM = 150 / 2.54


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


def _load_mark_asset(asset_path: Path, canvas_size: tuple[int, int] = _STAMP_SIZE_PX) -> Image.Image:
    """logo/ 폴더의 실제 마크 원본을 지정 캔버스 크기로 맞춘다(비율 유지, 중앙 배치)."""
    source = Image.open(asset_path).convert("RGBA")
    canvas = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    scale = min(canvas_size[0] / source.width, canvas_size[1] / source.height)
    new_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    resized = source.resize(new_size, Image.LANCZOS)
    offset = ((canvas_size[0] - new_size[0]) // 2, (canvas_size[1] - new_size[1]) // 2)
    canvas.paste(resized, offset)  # mask 인자를 또 주면 resized의 알파가 제곱으로 감쇠된다
    return canvas


def _fade_to_watermark_tone(img: Image.Image) -> Image.Image:
    """기관 마크를 흑백 처리 후 알파를 낮춰, 기존 글자 워터마크와 같은 옅은 회색 톤으로 만든다."""
    gray = img.convert("L")
    alpha = img.split()[3]
    faded_alpha = alpha.point(lambda v: v * _AGENCY_WATERMARK_ALPHA // 255)
    return Image.merge("RGBA", (gray, gray, gray, faded_alpha))


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
    noisy = _apply_noise(base, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_synthetic_security_stamp(output_path: Path, label: str) -> Path:
    """어두운 단색 영문 보안 스탬프 PNG를 만든다.

    실제 기관 자산이나 법정 등급 도안을 복제하지 않는 가상 표지다. 지원하는
    문구를 고정해 오타와 임의의 공식 표지 생성을 막고, 하단에 ``VIRTUAL
    SAMPLE``을 함께 넣어 합성 자산임을 이미지 자체에서도 식별할 수 있게 한다.
    """

    normalized = " ".join(str(label).strip().upper().split())
    if normalized not in SYNTHETIC_SECURITY_STAMP_LABELS:
        raise ValueError(f"알 수 없는 가상 보안 스탬프 문구: {label!r}")

    width, height = _SYNTHETIC_STAMP_SIZE_PX
    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    outer = 10
    inner = 22
    draw.rounded_rectangle(
        [outer, outer, width - outer - 1, height - outer - 1],
        radius=8,
        outline=_SYNTHETIC_STAMP_INK,
        width=8,
    )
    draw.rectangle(
        [inner, inner, width - inner - 1, height - inner - 1],
        outline=_SYNTHETIC_STAMP_INK,
        width=3,
    )

    header_font = _load_font(_KOREAN_FONT_BOLD_PATH, 24)
    footer_font = _load_font(_KOREAN_FONT_PATH, 21)
    label_size = 72
    while label_size > 34:
        label_font = _load_font(_KOREAN_FONT_BOLD_PATH, label_size)
        label_box = draw.textbbox((0, 0), normalized, font=label_font)
        if label_box[2] - label_box[0] <= width - 90:
            break
        label_size -= 2

    def centered_x(text: str, font: ImageFont.ImageFont) -> float:
        bbox = draw.textbbox((0, 0), text, font=font)
        return (width - (bbox[2] - bbox[0])) / 2 - bbox[0]

    draw.text(
        (centered_x("SECURITY CLASSIFICATION", header_font), 37),
        "SECURITY CLASSIFICATION",
        font=header_font,
        fill=_SYNTHETIC_STAMP_INK,
    )
    label_box = draw.textbbox((0, 0), normalized, font=label_font)
    label_y = (height - (label_box[3] - label_box[1])) / 2 - label_box[1] + 3
    draw.text(
        (centered_x(normalized, label_font), label_y),
        normalized,
        font=label_font,
        fill=_SYNTHETIC_STAMP_INK,
    )
    draw.text(
        (centered_x("VIRTUAL SAMPLE", footer_font), height - 55),
        "VIRTUAL SAMPLE",
        font=footer_font,
        fill=_SYNTHETIC_STAMP_INK,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
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
    noisy = _apply_noise(base, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_agency_letterhead_mark(output_path: Path, logo_filename: str, *, seed: int = 0) -> Path:
    """문서 좌상단에 한 번 표시할 기관 마크 PNG를 만든다(C 문서 전용).

    logo_filename은 agency_resolver.AGENCY_LOGO_FILENAMES의 값(logo/ 폴더 실제
    파일명)이다. 대외비/군사기밀 마크와 마찬가지로 회전은 적용하지 않는다
    (2026-07-21 사용자 결정 — 마크는 항상 수평 유지).
    """
    asset_path = _LOGO_DIR / logo_filename
    base = _load_mark_asset(asset_path, canvas_size=_LETTERHEAD_MARK_SIZE_PX)
    noisy = _apply_noise(base, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_agency_watermark(output_path: Path, logo_filename: str, *, seed: int = 0) -> Path:
    """가/나/다/라... 글자 워터마크 대신, 기관 마크를 크게 옅게 키워 배경에 한 번 찍는다.

    _draw_single_character_watermark와 같은 톤(연한 회색, 알파 60)으로 맞춰
    문서 배경에서 자연스럽게 보이도록 흑백+저알파 처리한다. 최신 PDF
    후처리의 승인된 작은·선명한 합성 규칙은 ``logo/README.md``와
    ``document_security_marking.py``를 따른다.
    """
    asset_path = _LOGO_DIR / logo_filename
    boxed = _load_mark_asset(asset_path, canvas_size=_AGENCY_WATERMARK_BOX_PX)
    faded = _fade_to_watermark_tone(boxed)

    width, height = _PAGE_SIZE_PX
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    offset = (
        (width - _AGENCY_WATERMARK_BOX_PX[0]) // 2,
        (height - _AGENCY_WATERMARK_BOX_PX[1]) // 2,
    )
    canvas.paste(faded, offset)  # mask 인자를 또 주면 faded의 알파(이미 60)가 제곱으로 감쇠된다

    noisy = _apply_noise(canvas, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_security_mark(output_path: Path, *, seed: int = 0) -> Path:
    """하위호환용 별칭 — 분류 박스 스탬프만 생성한다."""
    return generate_classification_stamp(output_path, seed=seed)


# 비밀표시 규정 제9항 도안 실측 치수 — 박스 너비 9cm × 높이 2cm(2026-07-27 사용자
# 지적 — 원 도안은 1.5cm 높이지만, [별표 2] 7호 재분류 근거 박스와 시각적으로
# 맞추기 위해 2cm로 통일하라는 사용자 결정).
_NOTICE_BOX_WIDTH_PX = round(9 * _PX_PER_CM)
_NOTICE_BOX_HEIGHT_PX = round(2 * _PX_PER_CM)
_NOTICE_BOX_PAD_PX = 10
# 2026-07-27: 이 박스는 최종 PDF 하단 여백에 6mm 높이로 작게 표시되는데, 2px
# 테두리는 _apply_noise()의 가우시안 블러 + 그 축소 배율을 거치면 사실상 사라진다
# (원본 PNG를 확대해서 보면 테두리가 있지만, 실제 인쇄/미리보기 크기에서는 안 보임
# — 사용자 지적). 축소돼도 살아남도록 두껍게 그린다.
_NOTICE_BOX_BORDER_WIDTH_PX = 6


def generate_military_secret_content_notice(output_path: Path, *, seed: int = 0) -> Path:
    """국방부·국가정보원이 아닌 일반 기관의 비밀문서에 군사기밀 사항이 섞여 있을 때,
    기존 "대외비" 마크 아래 여백에 붙이는 붉은색 문구 PNG를 만든다(비밀표시 규정
    제9항 — 각급 행정기관 장이 생산·재생산하는 비밀에 군사기밀 사항이 포함된 경우의
    표시, 2026-07-27 사용자 제공 이미지).

    agency_resolver.scenario_contains_military_secret()이 True인 문서에만 쓴다 —
    국방부/국가정보원 문서 자체는 이미 generate_military_secret_mark()의 등급 마크를
    쓰므로 이 함수 대상이 아니다. 박스 치수는 9cm×2cm로 고정하고([별표 2] 7호 재분류
    근거 박스와 같은 방식, 2026-07-27 사용자 지적 — 텍스트 길이에 맞춰 캔버스가
    늘어나던 이전 버전은 박스 테두리 자체가 없었다), 글자 크기를 그 안에 맞춰
    자동으로 줄인다.
    """
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    available_w = _NOTICE_BOX_WIDTH_PX - _NOTICE_BOX_PAD_PX * 2
    available_h = _NOTICE_BOX_HEIGHT_PX - _NOTICE_BOX_PAD_PX * 2

    font_size = _NOTICE_FONT_SIZE
    while font_size > 8:
        font = _load_font(_KOREAN_FONT_PATH, font_size)
        bbox = measure.textbbox((0, 0), _MILITARY_SECRET_CONTENT_NOTICE_TEXT, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if text_w <= available_w and text_h <= available_h:
            break
        font_size -= 1
    else:
        font = _load_font(_KOREAN_FONT_PATH, font_size)
        bbox = measure.textbbox((0, 0), _MILITARY_SECRET_CONTENT_NOTICE_TEXT, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    img = Image.new("RGBA", (_NOTICE_BOX_WIDTH_PX, _NOTICE_BOX_HEIGHT_PX), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle(
        [0, 0, _NOTICE_BOX_WIDTH_PX - 1, _NOTICE_BOX_HEIGHT_PX - 1],
        outline=_NOTICE_TEXT_COLOR, width=_NOTICE_BOX_BORDER_WIDTH_PX,
    )
    text_x = (_NOTICE_BOX_WIDTH_PX - text_w) / 2 - bbox[0]
    text_y = (_NOTICE_BOX_HEIGHT_PX - text_h) / 2 - bbox[1]
    draw.text((text_x, text_y), _MILITARY_SECRET_CONTENT_NOTICE_TEXT, font=font, fill=_NOTICE_TEXT_COLOR)
    noisy = _apply_noise(img, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


def generate_reclassification_old_mark(output_path: Path, old_grade: str, *, seed: int = 0) -> Path:
    """재분류된 군사기밀 문서의 "예전 등급" 마크 — 등급 마크 위에 붉은색 대각선(X자)을
    그어 삭제 표시한다([별표 2] 7호 "예전 분류 표시를 붉은색으로 대각선을 그어 삭제").

    상단 마진에 배치한다 — 새(현재) 등급은 기존 generate_military_secret_mark()를
    하단에 그대로 쓴다(측면 배치 대신 상단/하단만 쓰는 설계, 2026-07-27 결정).
    """
    if old_grade not in MILITARY_SECRET_MARK_FILENAMES:
        raise ValueError(f"알 수 없는 군사기밀 등급: {old_grade!r}")
    asset_path = _LOGO_DIR / MILITARY_SECRET_MARK_FILENAMES[old_grade]
    base = _load_mark_asset(asset_path)
    draw = ImageDraw.Draw(base)
    width, height = base.size
    line_width = max(2, width // 60)
    draw.line([(0, 0), (width, height)], fill=(220, 0, 0, 255), width=line_width)
    draw.line([(0, height), (width, 0)], fill=(220, 0, 0, 255), width=line_width)
    noisy = _apply_noise(base, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path


# [별표 2] 7호 재분류 근거 표시 도안 실측 치수 — 근거·직책·계급·성명 박스 8cm ×
# 서명란 1.5cm, 둘 다 높이 2cm.
_RECLASS_TEXT_BOX_WIDTH_PX = round(8 * _PX_PER_CM)
_RECLASS_SIGNATURE_BOX_WIDTH_PX = round(1.5 * _PX_PER_CM)
_RECLASS_BOX_HEIGHT_PX = round(2 * _PX_PER_CM)
_RECLASS_BOX_PAD_PX = 10
_RECLASS_LINE_GAP_PX = 6
_RECLASS_SIGNATURE_LABEL_FONT_SIZE = 18
# _NOTICE_BOX_BORDER_WIDTH_PX와 같은 이유 — 최종 PDF 하단 여백에서 작게 표시될 때도
# 테두리가 사라지지 않도록 두껍게 그린다(2026-07-27 사용자 지적).
_RECLASS_BOX_BORDER_WIDTH_PX = 6


def generate_reclassification_notice(
    output_path: Path,
    *,
    basis_text: str,
    reclass_date: str,
    position: str,
    rank: str,
    name: str,
    seed: int = 0,
) -> Path:
    """[별표 2] 7호 재분류 근거 표시 PNG를 만든다 — "{근거}에 따른 재분류({날짜})" +
    "직책: {} 계급: {} 성명: {}" 두 줄이 든 박스(8cm×2cm)와 그 옆 빈 서명란
    (1.5cm×2cm, 실제 서명 이미지는 합성하지 않는다)으로 구성한다. 박스 치수는
    [별표 2] 도안 실측값 그대로 고정하고(2026-07-27 사용자 지적 — 텍스트 길이에
    맞춰 캔버스가 늘어나던 이전 버전은 규격과 다르다), 글자 크기를 그 안에 맞춰
    자동으로 줄인다. 하단 마진에서 새 등급 마크(generate_military_secret_mark)
    아래에 쌓아 표시한다.
    """
    line1 = f"{basis_text}에 따른 재분류({reclass_date})"
    line2 = f"직책: {position}  계급: {rank}  성명: {name}"
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    available_w = _RECLASS_TEXT_BOX_WIDTH_PX - _RECLASS_BOX_PAD_PX * 2
    available_line_h = (
        _RECLASS_BOX_HEIGHT_PX - _RECLASS_BOX_PAD_PX * 2 - _RECLASS_LINE_GAP_PX
    ) / 2

    font_size = _NOTICE_FONT_SIZE
    while font_size > 8:
        font = _load_font(_KOREAN_FONT_PATH, font_size)
        bbox1 = measure.textbbox((0, 0), line1, font=font)
        bbox2 = measure.textbbox((0, 0), line2, font=font)
        fits_width = max(bbox1[2] - bbox1[0], bbox2[2] - bbox2[0]) <= available_w
        fits_height = max(bbox1[3] - bbox1[1], bbox2[3] - bbox2[1]) <= available_line_h
        if fits_width and fits_height:
            break
        font_size -= 1
    else:
        font = _load_font(_KOREAN_FONT_PATH, font_size)
        bbox1 = measure.textbbox((0, 0), line1, font=font)
        bbox2 = measure.textbbox((0, 0), line2, font=font)

    total_w = _RECLASS_TEXT_BOX_WIDTH_PX + _RECLASS_SIGNATURE_BOX_WIDTH_PX
    img = Image.new("RGBA", (total_w, _RECLASS_BOX_HEIGHT_PX), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    draw.rectangle(
        [0, 0, _RECLASS_TEXT_BOX_WIDTH_PX, _RECLASS_BOX_HEIGHT_PX - 1],
        outline=(0, 0, 0, 255), width=_RECLASS_BOX_BORDER_WIDTH_PX,
    )
    draw.rectangle(
        [_RECLASS_TEXT_BOX_WIDTH_PX, 0, total_w - 1, _RECLASS_BOX_HEIGHT_PX - 1],
        outline=(0, 0, 0, 255), width=_RECLASS_BOX_BORDER_WIDTH_PX,
    )

    line1_y = _RECLASS_BOX_PAD_PX
    line2_y = _RECLASS_BOX_PAD_PX + available_line_h + _RECLASS_LINE_GAP_PX
    draw.text((_RECLASS_BOX_PAD_PX - bbox1[0], line1_y - bbox1[1]), line1, font=font, fill=(0, 0, 0, 255))
    draw.text((_RECLASS_BOX_PAD_PX - bbox2[0], line2_y - bbox2[1]), line2, font=font, fill=(0, 0, 0, 255))

    sig_font = _load_font(_KOREAN_FONT_PATH, _RECLASS_SIGNATURE_LABEL_FONT_SIZE)
    sig_bbox = measure.textbbox((0, 0), "서명", font=sig_font)
    sig_w, sig_h = sig_bbox[2] - sig_bbox[0], sig_bbox[3] - sig_bbox[1]
    sig_x = _RECLASS_TEXT_BOX_WIDTH_PX + (_RECLASS_SIGNATURE_BOX_WIDTH_PX - sig_w) / 2 - sig_bbox[0]
    sig_y = (_RECLASS_BOX_HEIGHT_PX - sig_h) / 2 - sig_bbox[1]
    draw.text((sig_x, sig_y), "서명", font=sig_font, fill=(0, 0, 0, 255))

    noisy = _apply_noise(img, seed=seed, angle_range=0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    noisy.save(output_path)
    return output_path
