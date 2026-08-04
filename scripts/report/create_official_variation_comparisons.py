"""공문 템플릿 변주 PNG를 3열 비교판으로 묶는다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

import rd2.generators

# 폰트는 저장소 배치가 아니라 패키지에 속한다. 저장소 루트에서 거슬러 찾으면
# 스크립트가 몇 층 깊이에 있는지를 스크립트가 알아야 해서, 폴더를 옮길 때마다 틀린다.
# 이 스크립트는 PDF를 렌더링하지 않으므로 렌더러 모듈(=WeasyPrint) 대신
# 패키지 위치만 가져온다.
_FONT_DIR = Path(rd2.generators.__file__).parent / "assets" / "fonts"

_TEMPLATE_LABELS = {
    "01_classic_municipal": "01 전통 공문형",
    "02_fire_station": "02 상단 배너형",
    "03_internal_approval": "03 내부 결재형",
    "04_personnel_notice": "04 포고문형",
    "05_field_report": "05 사이드바형",
    "06_modern_public": "06 모듈 카드형",
    "07_monochrome_hwp": "07 흑백 실무형",
    "08_table_first_report": "08 표 중심 보고형",
    "09_long_form": "09 장문 2페이지형",
    "10_checklist_form": "10 체크리스트형",
}

_PROFILES = (
    ("01_stacked", "STACKED · 수직"),
    ("02_split", "SPLIT · 분할"),
    ("03_reflow", "REFLOW · 재배치"),
)

_IDENTITY_LABELS = {
    "none": "무로고",
    "symbol": "가상 심볼",
    "wordmark": "문자 마크",
}


def create_comparison_sheets(input_dir: Path) -> list[Path]:
    preview_dir = input_dir / "previews"
    manifest = json.loads(
        (input_dir / "manifest.json").read_text(encoding="utf-8")
    )
    identity_by_variation = {
        (item["template_slug"], item["variation_slug"]): item["identity"]["profile"]
        for item in manifest
    }
    bold_font = _FONT_DIR / "NotoSansKR-Bold.ttf"
    regular_font = _FONT_DIR / "NotoSansKR-Regular.ttf"
    fonts = {
        "title": ImageFont.truetype(str(bold_font), 30),
        "header": ImageFont.truetype(str(bold_font), 21),
        "row": ImageFont.truetype(str(bold_font), 23),
        "small": ImageFont.truetype(str(regular_font), 18),
    }
    template_slugs = list(_TEMPLATE_LABELS)
    outputs: list[Path] = []

    for start in (0, 5):
        group = template_slugs[start : start + 5]
        output_path = input_dir / f"comparison-{start + 1:02d}-{start + 5:02d}.png"
        _create_sheet(
            group,
            preview_dir=preview_dir,
            output_path=output_path,
            title=f"공문 레이아웃·기관 표식 변주 · {start + 1:02d}–{start + 5:02d}",
            fonts=fonts,
            identity_by_variation=identity_by_variation,
        )
        outputs.append(output_path)
    return outputs


def _create_sheet(
    template_slugs: list[str],
    *,
    preview_dir: Path,
    output_path: Path,
    title: str,
    fonts: dict[str, ImageFont.FreeTypeFont],
    identity_by_variation: dict[tuple[str, str], str],
) -> None:
    thumb_width, thumb_height = 280, 396
    left, gap, row_gap = 190, 24, 22
    top, label_height, margin = 104, 38, 30
    canvas_width = left + 3 * thumb_width + 2 * gap + margin
    row_height = thumb_height + label_height
    canvas_height = (
        top
        + len(template_slugs) * row_height
        + (len(template_slugs) - 1) * row_gap
        + margin
    )
    canvas = Image.new("RGB", (canvas_width, canvas_height), "#e8edef")
    draw = ImageDraw.Draw(canvas)
    draw.text((margin, 24), title, fill="#17252e", font=fonts["title"])

    for column, (_, profile_label) in enumerate(_PROFILES):
        x = left + column * (thumb_width + gap)
        draw.text((x, 69), profile_label, fill="#41515b", font=fonts["header"])

    for row, template_slug in enumerate(template_slugs):
        y = top + row * (row_height + row_gap)
        draw.text(
            (margin, y + 12),
            _TEMPLATE_LABELS[template_slug],
            fill="#253742",
            font=fonts["row"],
        )
        draw.text(
            (margin, y + 49),
            "동일 내용 / seed 기록",
            fill="#6b7a82",
            font=fonts["small"],
        )

        for column, (profile_slug, profile_label) in enumerate(_PROFILES):
            x = left + column * (thumb_width + gap)
            image_path = preview_dir / template_slug / f"{profile_slug}.png"
            if not image_path.exists():
                pdf_path = (
                    preview_dir.parent
                    / template_slug
                    / f"{profile_slug}.pdf"
                )
                _render_first_page(pdf_path, image_path)
            image = Image.open(image_path).convert("RGB")
            image.thumbnail(
                (thumb_width, thumb_height),
                Image.Resampling.LANCZOS,
            )
            frame = Image.new("RGB", (thumb_width, thumb_height), "white")
            frame.paste(
                image,
                (
                    (thumb_width - image.width) // 2,
                    (thumb_height - image.height) // 2,
                ),
            )
            canvas.paste(frame, (x, y))
            draw.rectangle(
                (x, y, x + thumb_width - 1, y + thumb_height - 1),
                outline="#aab5bb",
                width=2,
            )
            draw.text(
                (x + 3, y + thumb_height + 8),
                (
                    f"{profile_label.split(' · ')[1]} · "
                    f"{_IDENTITY_LABELS[identity_by_variation[(template_slug, profile_slug)]]}"
                ),
                fill="#51616a",
                font=fonts["small"],
            )

    canvas.save(output_path, quality=95)


def _render_first_page(pdf_path: Path, image_path: Path) -> None:
    """비교판에 필요한 첫 페이지만 필요 시 생성한다."""

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing variation PDF: {pdf_path}")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        page = document.load_page(0)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        pixmap.save(image_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help=(
            "generate_official_template_variations.py가 만든 변주 디렉터리 "
            "(manifest.json과 previews/가 있는 곳)"
        ),
    )
    args = parser.parse_args()
    for output_path in create_comparison_sheets(args.input_dir):
        print(f"[ok] {output_path}")


if __name__ == "__main__":
    main()
