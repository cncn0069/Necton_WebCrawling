"""C 문서 PDF 바깥 여백에 적용하는 단색 보안 스킨 10종.

본문의 문서 유형과 레이아웃은 각 전용 렌더러가 결정한다. 이 모듈은 렌더가
끝난 A4 PDF의 예약 여백만 사용해 대외비 문서의 시각적 통제 표지를 더한다.
그래서 공문, 연구보고서, 회의록 등 서로 다른 본문 템플릿에 같은 보안 스킨을
재사용할 수 있고 생성 payload의 문서 구조도 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import fitz


_INK = (34 / 255, 39 / 255, 44 / 255)
_MID = (96 / 255, 104 / 255, 112 / 255)
_LIGHT = (232 / 255, 235 / 255, 237 / 255)
_WHITE = (1.0, 1.0, 1.0)

BODY_SAFE_TOP_BOTTOM_PT = 30.0
BODY_SAFE_LEFT_RIGHT_PT = 12.0

ConfidentialLayout = Literal[
    "classic_register",
    "report_band",
    "minimal_mark",
    "restricted_memo",
    "strategy_report",
    "controlled_sheet",
    "official_sensitive",
    "registry_control",
    "protected_technology",
    "need_to_know",
]


@dataclass(frozen=True)
class ConfidentialSecurityTemplate:
    slug: str
    name: str
    english_label: str
    layout: ConfidentialLayout
    stamp_asset: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


CONFIDENTIAL_SECURITY_TEMPLATES: tuple[ConfidentialSecurityTemplate, ...] = (
    ConfidentialSecurityTemplate(
        "01_classic_register",
        "공문 원본형",
        "CONFIDENTIAL",
        "classic_register",
        "대외비.png",
    ),
    ConfidentialSecurityTemplate(
        "02_report_band",
        "리포트 밴드형",
        "CONFIDENTIAL REPORT",
        "report_band",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "03_minimal_mark",
        "미니멀 마크형",
        "CONFIDENTIAL",
        "minimal_mark",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "04_restricted_memo",
        "통제 메모형",
        "CONFIDENTIAL / INTERNAL",
        "restricted_memo",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "05_strategy_report",
        "전략보고서형",
        "CONFIDENTIAL / STRATEGY",
        "strategy_report",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "06_controlled_sheet",
        "통제 커버시트형",
        "CONFIDENTIAL / CONTROLLED",
        "controlled_sheet",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "07_official_sensitive",
        "오피셜 센서티브형",
        "CONFIDENTIAL / OFFICIAL",
        "official_sensitive",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "08_registry_control",
        "레지스트리 통제형",
        "CONFIDENTIAL / REGISTRY",
        "registry_control",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "09_protected_technology",
        "산업기술 보호형",
        "CONFIDENTIAL / PROTECTED TECHNOLOGY",
        "protected_technology",
        "synthetic_confidential.png",
    ),
    ConfidentialSecurityTemplate(
        "10_need_to_know",
        "디지털 니드투노우형",
        "CONFIDENTIAL / NEED TO KNOW",
        "need_to_know",
        "synthetic_confidential.png",
    ),
)

# 군사기밀은 등급별 표지 자체가 분류 권위다. 대외비 템플릿의 영문 등급 문구를
# 섞지 않고, 분류 의미가 없는 테두리만 재사용한다.
MILITARY_NEUTRAL_SECURITY_FRAME = ConfidentialSecurityTemplate(
    "military_neutral_frame",
    "군사기밀 중립 프레임",
    "",
    "classic_register",
    "",
)

CONFIDENTIAL_SECURITY_TEMPLATE_SLUGS = tuple(
    template.slug for template in CONFIDENTIAL_SECURITY_TEMPLATES
)


def select_confidential_security_template(
    selection_seed: int,
    *,
    offset: int = 0,
) -> ConfidentialSecurityTemplate:
    """seed와 출력 순번으로 10종을 재현 가능하게 순환 선택한다."""

    index = (selection_seed + offset) % len(CONFIDENTIAL_SECURITY_TEMPLATES)
    return CONFIDENTIAL_SECURITY_TEMPLATES[index]


def body_safe_rect(page: fitz.Page) -> fitz.Rect:
    """보안 스킨과 본문이 겹치지 않는 공통 본문 사각형."""

    return fitz.Rect(
        BODY_SAFE_LEFT_RIGHT_PT,
        BODY_SAFE_TOP_BOTTOM_PT,
        page.rect.width - BODY_SAFE_LEFT_RIGHT_PT,
        page.rect.height - BODY_SAFE_TOP_BOTTOM_PT,
    )


def _text(
    page: fitz.Page,
    x: float,
    y: float,
    value: str,
    *,
    color: tuple[float, float, float] = _INK,
    size: float = 6.4,
    rotate: int = 0,
) -> None:
    page.insert_text(
        fitz.Point(x, y),
        value,
        fontsize=size,
        fontname="helv",
        color=color,
        rotate=rotate,
        overlay=True,
    )


def _mark_rect(
    page: fitz.Page,
    *,
    image_ratio: float,
    anchor: str,
    height: float = 17.0,
) -> fitz.Rect:
    width = height * image_ratio
    margin = 5.0
    if anchor == "top_left":
        x0, y0 = margin, margin
    elif anchor == "top_right":
        x0, y0 = page.rect.width - margin - width, margin
    elif anchor == "bottom_left":
        x0, y0 = margin, page.rect.height - margin - height
    elif anchor == "bottom_right":
        x0 = page.rect.width - margin - width
        y0 = page.rect.height - margin - height
    elif anchor == "bottom_center":
        x0 = (page.rect.width - width) / 2
        y0 = page.rect.height - margin - height
    else:
        x0 = (page.rect.width - width) / 2
        y0 = margin
    return fitz.Rect(x0, y0, x0 + width, y0 + height)


def _insert_mark(
    page: fitz.Page,
    *,
    mark_bytes: bytes,
    image_ratio: float,
    anchor: str,
) -> fitz.Rect:
    rect = _mark_rect(page, image_ratio=image_ratio, anchor=anchor)
    page.insert_image(
        rect,
        stream=mark_bytes,
        keep_proportion=True,
        overlay=True,
    )
    return rect


def _corner_brackets(page: fitz.Page) -> None:
    inset = 7.0
    length = 18.0
    width = 1.2
    x1 = page.rect.width - inset
    y1 = page.rect.height - inset
    for start, end in (
        ((inset, inset), (inset + length, inset)),
        ((inset, inset), (inset, inset + length)),
        ((x1, inset), (x1 - length, inset)),
        ((x1, inset), (x1, inset + length)),
        ((inset, y1), (inset + length, y1)),
        ((inset, y1), (inset, y1 - length)),
        ((x1, y1), (x1 - length, y1)),
        ((x1, y1), (x1, y1 - length)),
    ):
        page.draw_line(
            fitz.Point(*start),
            fitz.Point(*end),
            color=_INK,
            width=width,
            overlay=True,
        )


def draw_confidential_security_template(
    page: fitz.Page,
    *,
    template: ConfidentialSecurityTemplate,
    mark_bytes: bytes | None,
    mark_ratio: float | None,
) -> dict[str, object]:
    """한 본문 페이지의 예약 여백에 선택한 단색 스킨을 그린다.

    군사기밀은 별도의 등급 이미지가 같은 단계에서 들어가므로 ``mark_bytes``를
    넘기지 않고 프레임만 재사용한다.
    """

    width = page.rect.width
    height = page.rect.height
    layout = template.layout
    mark_rect: fitz.Rect | None = None

    if layout == "classic_register":
        page.draw_rect(
            fitz.Rect(4, 4, width - 4, height - 4),
            color=_INK,
            width=0.9,
            overlay=True,
        )
        page.draw_rect(
            fitz.Rect(7, 7, width - 7, height - 7),
            color=_MID,
            width=0.35,
            overlay=True,
        )
        _text(page, 10, height - 9, "ORIGINAL / CONTROLLED COPY", size=5.6)
        mark_anchor = "top_center"
    elif layout == "report_band":
        page.draw_rect(
            fitz.Rect(0, 0, width, 23),
            color=None,
            fill=_INK,
            overlay=True,
        )
        _text(page, 12, 15.5, template.english_label, color=_WHITE, size=7.0)
        page.draw_line(
            fitz.Point(0, height - 23),
            fitz.Point(width, height - 23),
            color=_INK,
            width=1.2,
            overlay=True,
        )
        mark_anchor = "bottom_center"
    elif layout == "minimal_mark":
        page.draw_line(
            fitz.Point(8, height - 9),
            fitz.Point(width - 8, height - 9),
            color=_MID,
            width=0.45,
            overlay=True,
        )
        _text(page, width - 94, height - 13, "DO NOT FORWARD", size=5.8)
        mark_anchor = "top_left"
    elif layout == "restricted_memo":
        page.draw_line(
            fitz.Point(4, 0),
            fitz.Point(4, height),
            color=_INK,
            width=7.0,
            overlay=True,
        )
        page.draw_line(
            fitz.Point(10, 24),
            fitz.Point(width - 10, 24),
            color=_MID,
            width=0.45,
            overlay=True,
        )
        _text(page, width - 122, 16, template.english_label, size=6.4)
        mark_anchor = "bottom_left"
    elif layout == "strategy_report":
        page.draw_line(
            fitz.Point(0, 4),
            fitz.Point(width, 4),
            color=_INK,
            width=3.4,
            overlay=True,
        )
        page.draw_line(
            fitz.Point(0, height - 5),
            fitz.Point(width, height - 5),
            color=_INK,
            width=1.0,
            overlay=True,
        )
        _text(page, 10, height - 10, "NEED TO KNOW", size=5.8)
        mark_anchor = "top_center"
    elif layout == "controlled_sheet":
        _corner_brackets(page)
        _text(page, width - 82, height - 10, "CONTROLLED", size=6.0)
        mark_anchor = "top_center"
    elif layout == "official_sensitive":
        page.draw_rect(
            fitz.Rect(0, 0, width, 20),
            color=None,
            fill=_LIGHT,
            overlay=True,
        )
        page.draw_rect(
            fitz.Rect(0, height - 20, width, height),
            color=None,
            fill=_LIGHT,
            overlay=True,
        )
        _text(page, 10, 13.5, template.english_label, size=6.5)
        mark_anchor = "bottom_right"
    elif layout == "registry_control":
        page.draw_line(
            fitz.Point(7, 24),
            fitz.Point(width - 7, 24),
            color=_INK,
            width=0.7,
            overlay=True,
        )
        page.draw_line(
            fitz.Point(7, 27),
            fitz.Point(width - 7, 27),
            color=_MID,
            width=0.3,
            overlay=True,
        )
        _text(page, 10, 17, "COPY 01 / CONTROLLED", size=5.8)
        _text(page, width - 78, 17, "REGISTRY CONTROL", size=5.4)
        page.draw_line(
            fitz.Point(7, height - 8),
            fitz.Point(width - 7, height - 8),
            color=_INK,
            width=0.7,
            overlay=True,
        )
        mark_anchor = "top_center"
    elif layout == "protected_technology":
        page.draw_rect(
            fitz.Rect(0, 0, 7, height),
            color=None,
            fill=_INK,
            overlay=True,
        )
        page.draw_line(
            fitz.Point(12, 7),
            fitz.Point(12, height - 7),
            color=_MID,
            width=0.7,
            overlay=True,
        )
        _text(
            page,
            width - 8,
            height - 100,
            template.english_label,
            size=5.4,
            rotate=90,
        )
        mark_anchor = "bottom_center"
    elif layout == "need_to_know":
        page.draw_rect(
            fitz.Rect(0, height - 23, width, height),
            color=None,
            fill=_INK,
            overlay=True,
        )
        _text(page, 12, height - 8, template.english_label, color=_WHITE, size=6.5)
        _text(page, width - 102, 15, "ACCESS LOGGED", size=5.7)
        mark_anchor = "top_right"
    else:
        raise ValueError(f"알 수 없는 보안 스킨 layout입니다: {layout!r}")

    if mark_bytes is not None and mark_ratio is not None:
        mark_rect = _insert_mark(
            page,
            mark_bytes=mark_bytes,
            image_ratio=mark_ratio,
            anchor=mark_anchor,
        )
        # 래스터 스탬프 안의 작은 표시는 후속 스캔/JPEG 처리에서 흐려질 수 있다.
        # 합성 provenance는 PDF 벡터 텍스트로도 최소 5.5pt 크기로 남긴다.
        provenance_rect = fitz.Rect(width - 91, 5, width - 7, 18)
        page.draw_rect(
            provenance_rect,
            color=_INK,
            fill=_WHITE,
            width=0.5,
            overlay=True,
        )
        _text(page, width - 84, 14, "VIRTUAL SAMPLE", size=5.5)

    return {
        "layout": layout,
        "mark_anchor": mark_anchor if mark_rect is not None else None,
        "mark_rect": (
            [mark_rect.x0, mark_rect.y0, mark_rect.x1, mark_rect.y1]
            if mark_rect is not None
            else None
        ),
        "synthetic_provenance": (
            {"label": "VIRTUAL SAMPLE", "font_size_pt": 5.5}
            if mark_rect is not None
            else None
        ),
    }
