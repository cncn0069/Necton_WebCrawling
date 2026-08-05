"""Seed 기반 공문 템플릿 레이아웃 변주 규칙."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import random
from typing import Any, Collection, Mapping

from rd2.generators.synthetic_official_agencies import SYNTHETIC_AGENCIES

PROFILES = ("stacked", "split", "reflow")
IDENTITY_PROFILES = ("none", "symbol", "wordmark")

EXPECTED_PAGE_COUNTS = {
    "01_classic_municipal": 1,
    "02_fire_station": 1,
    "03_internal_approval": 1,
    "04_personnel_notice": 1,
    "05_field_report": 1,
    "06_modern_public": 1,
    "07_monochrome_hwp": 1,
    "08_table_first_report": 1,
    "09_long_form": 2,
    "10_checklist_form": 1,
}

_PROFILE_RANGES = {
    "stacked": {
        "font_scale": (0.98, 1.00),
        "line_height": (1.50, 1.54),
        "margin_scale": (0.96, 0.99),
        "table_padding_mm": (1.35, 1.50),
        "section_gap_mm": (1.9, 2.2),
        "heading_font": "Noto Sans KR",
        "title_align": "center",
    },
    "split": {
        "font_scale": (0.96, 0.99),
        "line_height": (1.46, 1.51),
        "margin_scale": (0.91, 0.95),
        "table_padding_mm": (1.20, 1.40),
        "section_gap_mm": (1.6, 2.0),
        "heading_font": "Noto Sans KR",
        "title_align": "left",
    },
    "reflow": {
        "font_scale": (0.97, 1.01),
        "line_height": (1.49, 1.55),
        "margin_scale": (0.94, 0.99),
        "table_padding_mm": (1.30, 1.50),
        "section_gap_mm": (1.8, 2.2),
        "heading_font": "Noto Sans KR",
        "title_align": "left",
    },
}

# 실제 공문 예시처럼 색상보다 선, 여백, 표 배치로 레이아웃 차이를 만든다.
# 템플릿별로 아주 미세한 온도 차이만 남기되 모든 강조색은 저채도 회색이다.
_ACCENTS = {
    "01_classic_municipal": ("#34383a", "#424749", "#555a5c"),
    "02_fire_station": ("#393636", "#494545", "#5a5656"),
    "03_internal_approval": ("#353a3c", "#444a4c", "#565c5e"),
    "04_personnel_notice": ("#3b3935", "#4b4944", "#5c5a54"),
    "05_field_report": ("#34383b", "#42484c", "#555b5f"),
    "06_modern_public": ("#353a38", "#454a47", "#575c59"),
    "07_monochrome_hwp": ("#111111", "#2c2c2c", "#4a4a4a"),
    "08_table_first_report": ("#303438", "#40454a", "#53585d"),
    "09_long_form": ("#34393c", "#444a4e", "#565c60"),
    "10_checklist_form": ("#353a38", "#454a47", "#575c59"),
}

_IDENTITY_SEQUENCE = (
    "none",
    "symbol",
    "wordmark",
    "none",
    "symbol",
    "none",
    "symbol",
    "wordmark",
    "none",
    "symbol",
)

_IDENTITY_LAYOUT_SHIFTS = {
    1: 0,
    2: 3,
    3: 6,
}

_IDENTITY_SHAPES = ("orbit", "block", "dual")

_ROADS = ("나래로", "모아길", "새빛로", "이음대로")
_DISTRICTS = ("하늘", "솔길", "누리", "새결")


@dataclass(frozen=True)
class VariationSpec:
    """한 PDF 변주를 완전히 재현할 수 있는 설정."""

    template_slug: str
    index: int
    profile: str
    seed: int
    font_scale: float
    line_height: float
    margin_scale: float
    table_padding_mm: float
    section_gap_mm: float
    heading_font: str
    title_align: str
    accent: str
    light_accent: str

    @property
    def slug(self) -> str:
        return f"{self.index:02d}_{self.profile}"

    @property
    def css_class(self) -> str:
        return f"variation-{self.profile}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IdentitySpec:
    """실제 기관 표식을 대체하는 재현 가능한 가상 아이덴티티."""

    template_slug: str
    variation_index: int
    profile: str
    seed: int
    shape: str
    agency_seed: int
    agency_pool_index: int
    agency_stem: str
    organization_category: str
    organization_type: str
    issuer_role: str
    romanized_name: str
    slogan: str
    brand_note: str
    copy_recipients: str
    road_name: str
    district_name: str

    @property
    def agency_name(self) -> str:
        return f"{self.agency_stem}{self.organization_type}"

    @property
    def display_agency_name(self) -> str:
        # 짧은 지자체명은 전통 공문처럼 자간을 벌리고, 긴 공공기관명은
        # 글자마다 공백을 넣으면 줄바꿈이 과해지므로 원문 표기를 유지한다.
        return (
            " ".join(self.agency_name)
            if len(self.agency_name) <= 6
            else self.agency_name
        )

    @property
    def issuer_title(self) -> str:
        title = f"{self.agency_name}{self.issuer_role}"
        return " ".join(title) if len(title) <= 7 else title

    @property
    def administration_type(self) -> str:
        """기존 manifest 소비자를 위한 조직 유형 호환 필드."""

        return self.organization_type

    @property
    def mark_text(self) -> str:
        return self.agency_stem if self.profile == "wordmark" else ""

    @property
    def css_class(self) -> str:
        classes = [f"identity-{self.profile}"]
        if self.profile == "symbol":
            classes.append(f"identity-shape-{self.shape}")
        return " ".join(classes)

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "agency_name": self.agency_name,
            "display_agency_name": self.display_agency_name,
            "issuer_title": self.issuer_title,
            "administration_type": self.administration_type,
            "mark_text": self.mark_text,
            "css_class": self.css_class,
            "agency_pool_size": len(SYNTHETIC_AGENCIES),
        }


def build_variation_specs(
    template_slug: str,
    *,
    count: int = 3,
    base_seed: int = 20260728,
    start_offset: int = 0,
) -> list[VariationSpec]:
    """템플릿 하나에 대해 재현 가능한 변주 설정을 만든다."""

    if template_slug not in EXPECTED_PAGE_COUNTS:
        raise ValueError(f"Unknown template slug: {template_slug}")
    if count < 1:
        raise ValueError("count must be at least 1")
    if start_offset < 0:
        raise ValueError("start_offset must be at least 0")

    template_number = int(template_slug.split("_", 1)[0])
    specs: list[VariationSpec] = []

    for offset in range(start_offset, start_offset + count):
        profile = PROFILES[offset % len(PROFILES)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
        ranges = _PROFILE_RANGES[profile]
        accent = _ACCENTS[template_slug][1]
        specs.append(
            VariationSpec(
                template_slug=template_slug,
                index=offset + 1,
                profile=profile,
                seed=seed,
                font_scale=round(rng.uniform(*ranges["font_scale"]), 3),
                line_height=round(rng.uniform(*ranges["line_height"]), 3),
                margin_scale=round(rng.uniform(*ranges["margin_scale"]), 3),
                table_padding_mm=round(
                    rng.uniform(*ranges["table_padding_mm"]),
                    2,
                ),
                section_gap_mm=round(rng.uniform(*ranges["section_gap_mm"]), 2),
                heading_font=str(ranges["heading_font"]),
                title_align=str(ranges["title_align"]),
                accent=accent,
                light_accent=_lighten(accent, 0.90),
            )
        )
    return specs


def build_identity_spec(
    template_slug: str,
    *,
    variation_index: int,
    base_seed: int = 20260728,
    identity_seed: int | None = None,
    organization_category: str | None = None,
) -> IdentitySpec:
    """문서 단위 기관과 레이아웃 단위 표식 변주를 독립적으로 선택한다.

    ``identity_seed``가 같은 문서는 어떤 템플릿과 레이아웃을 사용해도 같은
    기관명을 갖는다. 템플릿/레이아웃은 로고 유무와 형태만 바꾼다.
    """

    if template_slug not in EXPECTED_PAGE_COUNTS:
        raise ValueError(f"Unknown template slug: {template_slug}")
    if variation_index < 1:
        raise ValueError("variation_index must be at least 1")

    template_number = int(template_slug.split("_", 1)[0])
    shift = _IDENTITY_LAYOUT_SHIFTS.get(
        variation_index,
        ((variation_index - 1) * 3) % len(_IDENTITY_SEQUENCE),
    )
    profile = _IDENTITY_SEQUENCE[
        (template_number - 1 + shift) % len(_IDENTITY_SEQUENCE)
    ]
    seed = base_seed + 500_000 + template_number * 1000 + variation_index
    rng = random.Random(seed)
    resolved_agency_seed = base_seed if identity_seed is None else identity_seed
    agency_candidates = (
        tuple(
            agency
            for agency in SYNTHETIC_AGENCIES
            if agency.organization_category == organization_category
        )
        if organization_category
        else SYNTHETIC_AGENCIES
    )
    if not agency_candidates:
        raise ValueError(
            f"Unknown synthetic agency category: {organization_category}"
        )
    agency = agency_candidates[
        resolved_agency_seed % len(agency_candidates)
    ]
    agency_pool_index = agency.pool_index
    shape = rng.choice(_IDENTITY_SHAPES)
    if profile == "symbol":
        shape = _balanced_symbol_shape(template_number, variation_index)
    return IdentitySpec(
        template_slug=template_slug,
        variation_index=variation_index,
        profile=profile,
        seed=seed,
        shape=shape,
        agency_seed=resolved_agency_seed,
        agency_pool_index=agency_pool_index,
        agency_stem=agency.stem,
        organization_category=agency.organization_category,
        organization_type=agency.organization_type,
        issuer_role=agency.issuer_role,
        romanized_name=agency.romanized_name,
        slogan=agency.slogan,
        brand_note=agency.brand_note,
        copy_recipients=agency.copy_recipients,
        road_name=rng.choice(_ROADS),
        district_name=rng.choice(_DISTRICTS),
    )


def _balanced_symbol_shape(
    template_number: int,
    variation_index: int,
) -> str:
    """레이아웃별 순회 안에서 심볼 형태를 고르게 순환시킨다."""

    symbol_rank = 0
    for current_variation in range(1, variation_index + 1):
        final_template = (
            template_number
            if current_variation == variation_index
            else len(EXPECTED_PAGE_COUNTS)
        )
        for current_template in range(1, final_template + 1):
            shift = _IDENTITY_LAYOUT_SHIFTS.get(
                current_variation,
                ((current_variation - 1) * 3) % len(_IDENTITY_SEQUENCE),
            )
            profile = _IDENTITY_SEQUENCE[
                (current_template - 1 + shift)
                % len(_IDENTITY_SEQUENCE)
            ]
            if profile != "symbol":
                continue
            if (
                current_variation == variation_index
                and current_template == template_number
            ):
                return _IDENTITY_SHAPES[
                    symbol_rank % len(_IDENTITY_SHAPES)
                ]
            symbol_rank += 1
    raise ValueError("Current identity profile is not symbol")


def apply_identity_context(
    base_context: Mapping[str, Any],
    identity: IdentitySpec | None,
    *,
    protected_keys: Collection[str] = (),
) -> dict[str, Any]:
    """문서 데이터의 기존 기관 토큰을 가상 기관 값으로 교체한다.

    ``protected_keys``는 생성 모델이 만든 원문 필드에 사용한다. 예를 들어
    정책명에 실제로 "한빛"이라는 단어가 들어 있어도 레이아웃 아이덴티티
    변주 때문에 본문이 바뀌면 안 된다.
    """

    if identity is None:
        context = {
            key: deepcopy(value) if key in protected_keys else value
            for key, value in dict(base_context).items()
        }
        context.update(
            {
                "emblem": "",
                "agency_name": str(
                    base_context.get("source_agency_name") or ""
                ).strip(),
            }
        )
        return context

    replacements = (
        ("한빛시설관리공단", f"{identity.agency_stem}시설관리공단"),
        ("한빛문화광장", f"{identity.agency_stem}문화광장"),
        ("한빛시민", "관내 주민"),
        ("한 빛 시", identity.display_agency_name),
        ("한빛시", identity.agency_name),
        ("한빛", identity.agency_stem),
        ("hanbit", identity.romanized_name),
    )

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            for old, new in replacements:
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, tuple):
            return tuple(replace(item) for item in value)
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    protected = frozenset(protected_keys)
    context = {
        key: deepcopy(value) if key in protected else replace(value)
        for key, value in dict(base_context).items()
    }
    source_agency_name = str(
        base_context.get("source_agency_name") or ""
    ).strip()
    context.update(
        {
            "emblem": "" if source_agency_name else identity.mark_text,
            "agency_name": (
                source_agency_name or identity.display_agency_name
            ),
        }
    )
    return context


def render_variation_css(spec: VariationSpec) -> str:
    """변주 설정을 원본 템플릿 뒤에 적용할 CSS로 변환한다."""

    renderer = _CSS_RENDERERS.get(spec.template_slug)
    if renderer is None:
        raise ValueError(f"No CSS renderer for {spec.template_slug}")
    return _common_css(spec) + renderer(spec) + _print_sobriety_css(spec)


def _print_sobriety_css(spec: VariationSpec) -> str:
    """변주 CSS가 넓은 색상 면을 다시 만들지 않도록 인쇄 톤을 고정한다."""

    return f"""
/* 실제 지자체 공문에 가까운 저채도 인쇄 톤 */
body * {{
  border-color: #777777 !important;
}}
.banner-hero,
.sidebar-rail,
.modular-lead {{
  background: {spec.light_accent} !important;
  color: #171717 !important;
}}
.banner-hero {{
  border-bottom: .65mm solid {spec.accent} !important;
}}
.sidebar-rail {{
  border-right: .45mm solid {spec.accent} !important;
}}
.modular-lead {{
  border-left: 1.2mm solid {spec.accent} !important;
  border-radius: 0 !important;
}}
.banner-hero *,
.sidebar-rail *,
.modular-lead * {{
  color: #171717 !important;
}}
.banner-table th,
.banner-signoff > div:first-child,
.sidebar-table th,
.table-report-primary th,
.long-program th,
.checklist-table th {{
  background: #e8e8e8 !important;
  color: #171717 !important;
}}
.modular-title > span {{
  background: transparent !important;
  color: {spec.accent} !important;
}}
.approval-body article > span,
.long-section > div:first-child span {{
  color: #ffffff !important;
}}
.modular-sections,
.modular-facts,
.modular-table-card,
.modular-attachments,
.modular-issuer,
.checklist-summary > div,
.checklist-decision {{
  border-radius: 0 !important;
  box-shadow: none !important;
}}
"""


def render_identity_css(
    spec: IdentitySpec | None,
    *,
    accent: str,
    light_accent: str,
) -> str:
    """가상 아이덴티티를 모든 템플릿의 기관 표시 지점에 적용한다."""

    if spec is None or spec.profile == "none":
        return _identity_none_css()
    if spec.profile == "wordmark":
        return _identity_wordmark_css(spec, accent)
    return _identity_symbol_css(spec, accent, light_accent)


_IDENTITY_MARK_SELECTORS = (
    ".classic-emblem",
    ".banner-emblem",
    ".approval-brand > span",
    ".proclamation-brand strong",
    ".sidebar-emblem",
    ".modular-mark",
    ".table-report-brand > span",
    ".long-brand > span",
    ".checklist-brand > span",
)

_IDENTITY_DARK_MARK_SELECTORS = (
    ".banner-emblem",
    ".sidebar-emblem",
)


def _identity_selectors(
    identity_selector: str,
    selectors: tuple[str, ...] = _IDENTITY_MARK_SELECTORS,
    *,
    pseudo: str = "",
) -> str:
    return ",\n".join(
        f"{identity_selector} {selector}{pseudo}" for selector in selectors
    )


def _css_content(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _identity_none_css() -> str:
    marks = _identity_selectors("body.identity-none")
    return f"""
{marks} {{
  display: none;
}}
body.identity-none .classic-masthead {{
  grid-template-columns: 1fr 32mm;
}}
body.identity-none .modular-header {{
  grid-template-columns: 1fr 34mm;
}}
body.identity-none .proclamation-brand {{
  gap: 0;
}}
body.identity-none .proclamation-brand span {{
  width: 28mm;
}}
"""


def _identity_wordmark_css(spec: IdentitySpec, accent: str) -> str:
    marks = _identity_selectors("body.identity-wordmark")
    mark_text = _css_content(spec.mark_text)
    dark_marks = _identity_selectors(
        "body.identity-wordmark",
        _IDENTITY_DARK_MARK_SELECTORS,
    )
    return f"""
{marks} {{
  align-items: center;
  background: transparent;
  border: 0;
  border-bottom: .55mm solid {accent};
  border-radius: 0;
  color: {accent};
  display: flex;
  flex: 0 0 auto;
  font-size: 8pt;
  font-weight: 700;
  height: 11mm;
  justify-content: center;
  min-width: 18mm;
  padding: 1mm 2mm;
  transform: none;
  width: auto;
}}
{dark_marks} {{
  border-color: #ffffff;
  color: #ffffff;
}}
body.identity-wordmark .modular-mark > span {{
  display: none;
}}
body.identity-wordmark .modular-mark::before {{
  content: "{mark_text}";
}}
body.identity-wordmark .approval-brand > div,
body.identity-wordmark .table-report-brand > div,
body.identity-wordmark .checklist-brand > div,
body.identity-wordmark .long-header:not(.long-header-compact) .long-brand > div {{
  min-width: 34mm;
}}
body.identity-wordmark .approval-brand h1,
body.identity-wordmark .table-report-brand h1,
body.identity-wordmark .checklist-brand h1,
body.identity-wordmark .long-header:not(.long-header-compact) .long-brand h1 {{
  white-space: nowrap;
}}
body.identity-wordmark .monochrome-header > div {{
  min-height: 15mm;
  padding-left: 22mm;
  position: relative;
}}
body.identity-wordmark .monochrome-header > div::before {{
  border-bottom: .55mm solid {accent};
  color: {accent};
  content: "{mark_text}";
  font-size: 8pt;
  font-weight: 700;
  left: 0;
  padding: 2mm 1mm;
  position: absolute;
  text-align: center;
  top: 0;
  width: 17mm;
}}
"""


def _identity_symbol_css(
    spec: IdentitySpec,
    accent: str,
    light_accent: str,
) -> str:
    identity_selector = f"body.identity-symbol.identity-shape-{spec.shape}"
    marks = _identity_selectors(identity_selector)
    before = _identity_selectors(identity_selector, pseudo="::before")
    after = _identity_selectors(identity_selector, pseudo="::after")
    dark_marks = _identity_selectors(
        identity_selector,
        _IDENTITY_DARK_MARK_SELECTORS,
    )
    dark_before = _identity_selectors(
        identity_selector,
        _IDENTITY_DARK_MARK_SELECTORS,
        pseudo="::before",
    )
    dark_after = _identity_selectors(
        identity_selector,
        _IDENTITY_DARK_MARK_SELECTORS,
        pseudo="::after",
    )
    base = f"""
{marks} {{
  background: transparent;
  border: 0;
  border-radius: 0;
  color: transparent;
  display: block;
  flex: 0 0 auto;
  font-size: 0;
  height: 15mm;
  min-width: 15mm;
  overflow: hidden;
  padding: 0;
  position: relative;
  transform: none;
  width: 15mm;
}}
body.identity-symbol .modular-mark > span {{
  display: none;
}}
body.identity-symbol .monochrome-header > div {{
  min-height: 15mm;
  padding-left: 27mm;
  position: relative;
}}
body.identity-symbol .monochrome-header > div::before {{
  content: "";
  height: 14mm;
  left: 0;
  position: absolute;
  top: 0;
  width: 14mm;
}}
"""
    if spec.shape == "orbit":
        return (
            base
            + f"""
{marks} {{
  border: .8mm solid {accent};
  border-radius: 50%;
}}
{before} {{
  background: {accent};
  border-radius: 50%;
  content: "";
  height: 5mm;
  left: 4.2mm;
  position: absolute;
  top: 4.2mm;
  width: 5mm;
}}
{after} {{
  background: {light_accent};
  border-radius: 50%;
  content: "";
  height: 2mm;
  left: 5.7mm;
  position: absolute;
  top: 5.7mm;
  width: 2mm;
}}
{dark_marks} {{
  border-color: #ffffff;
}}
{dark_before} {{
  background: #ffffff;
}}
{dark_after} {{
  background: {accent};
}}
{identity_selector} .monochrome-header > div::before {{
  border: .8mm solid {accent};
  border-radius: 50%;
}}
"""
        )
    if spec.shape == "block":
        return (
            base
            + f"""
{marks} {{
  background: {accent};
  border-radius: 2mm;
}}
{before} {{
  background: {light_accent};
  content: "";
  height: 5mm;
  left: 5mm;
  position: absolute;
  top: 5mm;
  width: 5mm;
}}
{dark_marks} {{
  background: #ffffff;
}}
{dark_before} {{
  background: {accent};
}}
{identity_selector} .monochrome-header > div::before {{
  background: {accent};
  border-radius: 2mm;
}}
"""
        )
    return (
        base
        + f"""
{before},
{after} {{
  background: {accent};
  border-radius: 2mm;
  content: "";
  height: 13mm;
  position: absolute;
  top: 1mm;
  width: 5mm;
}}
{before} {{
  left: 1.5mm;
}}
{after} {{
  left: 8.5mm;
  top: 3mm;
}}
{dark_before},
{dark_after} {{
  background: #ffffff;
}}
{identity_selector} .monochrome-header > div::before {{
  background: transparent;
  border-left: 4mm solid {accent};
  border-right: 4mm solid {accent};
  height: 14mm;
  width: 13mm;
}}
"""
    )


def _lighten(hex_color: str, ratio: float) -> str:
    value = hex_color.removeprefix("#")
    channels = [int(value[index : index + 2], 16) for index in (0, 2, 4)]
    mixed = [round(channel + (255 - channel) * ratio) for channel in channels]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _mm(base: float, spec: VariationSpec) -> float:
    return round(base * spec.margin_scale, 2)


def _pt(base: float, spec: VariationSpec) -> float:
    return round(base * spec.font_scale, 2)


def _columns(spec: VariationSpec, stacked: str, split: str, reflow: str) -> str:
    return {
        "stacked": stacked,
        "split": split,
        "reflow": reflow,
    }[spec.profile]


def _common_css(spec: VariationSpec) -> str:
    return f"""
body.{spec.css_class} {{
  font-variant-numeric: tabular-nums;
}}
body.{spec.css_class} h1,
body.{spec.css_class} h2,
body.{spec.css_class} h3 {{
  font-family: "{spec.heading_font}", "Noto Sans KR", sans-serif;
}}
"""


def _classic_css(spec: VariationSpec) -> str:
    x = _mm(17, spec)
    top = _mm(17, spec)
    masthead = _columns(spec, "32mm 1fr 24mm", "32mm 1fr 32mm", "27mm 1fr 40mm")
    layout = {
        "stacked": """
.classic-body { display: block; }
""",
        "split": """
.classic-page { display: block; }
.classic-body { display: block; }
.classic-sections {
  float: left;
  margin-bottom: 0;
  width: calc(100% - 63mm);
}
.classic-facts {
  display: block;
  float: right;
  grid-template-columns: 1fr;
  margin: 0;
  width: 58mm;
}
.classic-facts div { grid-template-columns: 21mm 1fr; }
.classic-facts div:nth-child(odd) { border-right: 0; }
.classic-facts div:nth-child(n+2) { border-top: .2mm solid #aaa; }
.classic-table {
  float: right;
  font-size: 7.6pt;
  margin-top: 3mm;
  width: 58mm;
}
.classic-attachments {
  clear: right;
  display: block;
  float: right;
  margin-top: 3mm;
  width: 58mm;
}
.classic-attachments ol { padding-left: 5mm; }
.classic-body::after { clear: both; content: ""; display: block; }
""",
        "reflow": """
.classic-page { display: block; }
.classic-body {
  display: block;
  position: relative;
}
.classic-facts {
  display: block;
  grid-template-columns: 1fr;
  margin: 0;
  position: absolute;
  left: 0;
  top: 4mm;
  width: 50mm;
}
.classic-facts div { grid-template-columns: 19mm 1fr; }
.classic-facts div:nth-child(odd) { border-right: 0; }
.classic-facts div:nth-child(n+2) { border-top: .2mm solid #aaa; }
.classic-intro, .classic-sections { margin-left: 55mm; }
.classic-table { clear: both; }
.classic-attachments { margin-top: 2mm; }
""",
    }[spec.profile]
    return f"""
.classic-page {{ padding: {top}mm {x}mm {_mm(10, spec)}mm; }}
.classic-masthead {{
  grid-template-columns: {masthead};
  min-height: {_mm(38, spec)}mm;
}}
.classic-emblem, .classic-masthead > strong {{ color: {spec.accent}; border-color: {spec.accent}; }}
.classic-identity h1 {{ font-size: {_pt(25, spec)}pt; letter-spacing: {_mm(7, spec)}mm; }}
.classic-title dd {{ font-size: {_pt(12, spec)}pt; text-align: {spec.title_align}; }}
.classic-body {{ font-size: {_pt(10.3, spec)}pt; line-height: {spec.line_height}; }}
.classic-sections > li {{ margin-bottom: {spec.section_gap_mm}mm; }}
.classic-facts dt {{ background: {spec.light_accent}; }}
.classic-table th {{ background: {spec.light_accent}; }}
.classic-table th, .classic-table td {{ padding: {spec.table_padding_mm}mm; }}
{layout}
"""


def _banner_css(spec: VariationSpec) -> str:
    x = _mm(14, spec)
    columns = _columns(spec, "32mm 1fr 28mm", "40mm 1fr 34mm", "46mm 1fr 30mm")
    aside = _columns(spec, "46mm", "53mm", "60mm")
    layout = {
        "stacked": """
.banner-content { display: block; }
.banner-aside {
  display: grid;
  gap: 2mm 4mm;
  grid-template-columns: 1fr 1fr;
  margin-top: 3mm;
}
.banner-aside h2:first-child { grid-column: 1; grid-row: 1; }
.banner-aside dl { grid-column: 1; grid-row: 2; }
.banner-aside h2:nth-of-type(2) { grid-column: 2; grid-row: 1; }
.banner-aside ol { grid-column: 2; grid-row: 2; }
""",
        "split": """
.banner-content {
  display: grid;
  grid-template-columns: 1fr 53mm;
}
""",
        "reflow": """
.banner-content {
  display: flex;
  flex-direction: column;
}
.banner-aside {
  display: grid;
  gap: 2mm 4mm;
  grid-template-columns: 1fr 1fr;
  order: -1;
}
.banner-aside h2:first-child { grid-column: 1; grid-row: 1; }
.banner-aside dl { grid-column: 1; grid-row: 2; }
.banner-aside h2:nth-of-type(2) { grid-column: 2; grid-row: 1; }
.banner-aside ol { grid-column: 2; grid-row: 2; }
.banner-main {
  display: grid;
  gap: 3mm;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 3mm;
}
.banner-section {
  display: block;
  margin: 0;
}
.banner-number { display: block; margin-bottom: 1mm; }
""",
    }[spec.profile]
    return f"""
.banner-page {{
  display: block;
  padding-left: {x}mm;
  padding-right: {x}mm;
  position: relative;
}}
.banner-hero {{
  background: {spec.accent};
  grid-template-columns: {columns};
  margin-left: -{x}mm;
  margin-right: -{x}mm;
  min-height: {_mm(53, spec)}mm;
  padding-left: {x}mm;
  padding-right: {x}mm;
}}
.banner-routing {{
  margin-left: -{x}mm;
  margin-right: -{x}mm;
  padding-left: {x}mm;
  padding-right: {x}mm;
}}
.banner-heading h1 {{ font-size: {_pt(19, spec)}pt; }}
.banner-lead {{ font-size: {_pt(10.3, spec)}pt; line-height: {spec.line_height}; }}
.banner-content {{ grid-template-columns: 1fr {aside}; gap: {_mm(7, spec)}mm; }}
.banner-section {{ margin-bottom: {spec.section_gap_mm}mm; }}
.banner-table th, .banner-signoff > div:first-child {{ background: {spec.accent}; }}
.banner-table th, .banner-table td {{ padding: {spec.table_padding_mm}mm; }}
.banner-signoff {{
  border-color: {spec.accent};
  bottom: 18mm;
  left: {x}mm;
  margin-top: 0;
  position: absolute;
  right: {x}mm;
}}
.banner-footer {{
  bottom: 9mm;
  left: {x}mm;
  position: absolute;
  right: {x}mm;
}}
{layout}
"""


def _approval_css(spec: VariationSpec) -> str:
    approval_width = _columns(spec, "68mm", "78mm", "84mm")
    meta_columns = _columns(spec, "repeat(3, 1fr)", "repeat(3, 1fr)", "repeat(2, 1fr)")
    layout = {
        "stacked": """
.approval-header { display: block; }
.approval-matrix { margin: 3mm 0 0 auto; width: 100%; }
.approval-body { display: block; }
""",
        "split": """
.approval-body {
  display: grid;
  gap: 3mm;
  grid-template-columns: repeat(3, 1fr);
}
.approval-body article {
  display: block;
  margin: 0;
}
.approval-body article > span { margin-bottom: 1.5mm; }
""",
        "reflow": """
.approval-header { display: block; }
.approval-matrix { margin: 3mm 0 0 auto; width: 100%; }
.approval-meta dl { grid-template-columns: repeat(2, 1fr); }
.approval-body {
  display: grid;
  gap: 3mm;
  grid-template-columns: repeat(3, 1fr);
}
.approval-body article { display: block; margin: 0; }
.approval-body article > span { margin-bottom: 1.5mm; }
.approval-data { grid-template-columns: 1fr 55mm; }
.approval-data .approval-table { grid-column: 1; grid-row: 1; }
.approval-data .approval-facts { grid-column: 2; grid-row: 1; }
""",
    }[spec.profile]
    return f"""
.approval-page {{ padding: {_mm(8, spec)}mm {_mm(12, spec)}mm {_mm(7, spec)}mm; }}
.approval-header {{ grid-template-columns: 1fr {approval_width}; gap: {_mm(8, spec)}mm; }}
.approval-brand > span, .approval-body article > span {{ background: {spec.accent}; }}
.approval-meta dl {{ grid-template-columns: {meta_columns}; }}
.approval-title {{ border-color: {spec.accent}; }}
.approval-title small {{ color: {spec.accent}; }}
.approval-title h2 {{ font-size: {_pt(16, spec)}pt; text-align: {spec.title_align}; }}
.approval-title p {{ font-size: {_pt(9, spec)}pt; line-height: {spec.line_height}; }}
.approval-body article {{ margin-bottom: {spec.section_gap_mm}mm; }}
.approval-facts dt, .approval-table th {{ background: {spec.light_accent}; }}
.approval-table th, .approval-table td {{ padding: {spec.table_padding_mm}mm; }}
{layout}
"""


def _proclamation_css(spec: VariationSpec) -> str:
    outer = _mm(9, spec)
    frame_height = round(297 - outer * 2, 2)
    body_width = _columns(spec, "155mm", "150mm", "144mm")
    layout = {
        "stacked": """
.proclamation-body { display: block; }
""",
        "split": """
.proclamation-body {
  display: flex;
  flex-wrap: wrap;
  gap: 3mm 5mm;
}
.proclamation-body article {
  display: block;
  flex: 1 1 calc(50% - 2.5mm);
  margin: 0;
  min-width: 0;
}
.proclamation-body article > span { display: block; margin-bottom: 1mm; }
.proclamation-body article:last-child {
  flex-basis: 100%;
}
.proclamation-body h3,
.proclamation-body li { overflow-wrap: anywhere; }
""",
        "reflow": """
.proclamation-body {
  display: grid;
  gap: 3mm;
  grid-template-columns: repeat(3, 1fr);
  width: 100%;
}
.proclamation-body article { display: block; margin: 0; }
.proclamation-body article > span { display: block; margin-bottom: 1mm; }
""",
    }[spec.profile]
    return f"""
.proclamation-page {{ padding: {outer}mm; }}
.proclamation-frame {{
  border-color: {spec.accent};
  height: {frame_height}mm;
  padding: {_mm(10, spec)}mm {_mm(16, spec)}mm {_mm(8, spec)}mm;
}}
.proclamation-brand span, .proclamation-title div {{ background: {spec.accent}; }}
.proclamation-brand strong, .proclamation-title small,
.proclamation-body article > span {{ color: {spec.accent}; border-color: {spec.accent}; }}
.proclamation-title h2 {{
  font-size: {_pt(18, spec)}pt;
  text-align: {spec.title_align};
}}
.proclamation-intro {{ font-size: {_pt(9.2, spec)}pt; line-height: {spec.line_height}; }}
.proclamation-body {{ width: {body_width}; }}
.proclamation-body article {{ margin-bottom: {spec.section_gap_mm}mm; }}
.proclamation-table th, .proclamation-table td {{ padding: {spec.table_padding_mm}mm; }}
{layout}
"""


def _sidebar_css(spec: VariationSpec) -> str:
    rail = _columns(spec, "44mm", "50mm", "56mm")
    layout = {
        "stacked": """
.sidebar-page { display: block; }
.sidebar-rail {
  display: grid;
  gap: 1mm 5mm;
  grid-template-columns: 20mm 1fr 50mm 52mm;
  grid-template-rows: auto auto 1fr;
  height: 62mm;
  padding: 7mm 10mm;
}
.sidebar-emblem { grid-column: 1; grid-row: 1 / span 3; }
.sidebar-slogan { grid-column: 2; grid-row: 1; margin: 0; }
.sidebar-rail h1 { grid-column: 2; grid-row: 2; }
.sidebar-note { grid-column: 2; grid-row: 3; margin-top: 0; }
.sidebar-routing { grid-column: 3; grid-row: 1 / span 3; margin: 0; }
.sidebar-routing div { padding: .8mm 0; }
.sidebar-routing dd { margin-top: .3mm; }
.sidebar-issuer { grid-column: 4; grid-row: 1; margin: 0; }
.sidebar-rail footer {
  grid-column: 4;
  grid-row: 2 / span 2;
  margin: 1mm 0 0;
  padding-top: 1.5mm;
}
.sidebar-main {
  height: 215mm;
  padding: 8mm 14mm;
}
""",
        "split": """
.sidebar-page { display: grid; grid-template-columns: 50mm 1fr; }
.sidebar-rail { grid-column: 1; grid-row: 1; }
.sidebar-main { grid-column: 2; grid-row: 1; }
""",
        "reflow": """
.sidebar-page { display: grid; grid-template-columns: 1fr 52mm; }
.sidebar-main { grid-column: 1; grid-row: 1; }
.sidebar-rail { grid-column: 2; grid-row: 1; }
""",
    }[spec.profile]
    return f"""
.sidebar-page {{ grid-template-columns: {rail} 1fr; }}
.sidebar-rail {{ background: {spec.accent}; padding: {_mm(15, spec)}mm {_mm(8, spec)}mm {_mm(10, spec)}mm; }}
.sidebar-main {{ padding: {_mm(15, spec)}mm {_mm(13, spec)}mm {_mm(10, spec)}mm; }}
.sidebar-main > header {{ border-color: {spec.accent}; }}
.sidebar-main > header h2 {{ color: {spec.accent}; font-size: {_pt(20, spec)}pt; }}
.sidebar-main > header p {{ font-size: {_pt(9, spec)}pt; line-height: {spec.line_height}; }}
.sidebar-sections article {{ margin-bottom: {spec.section_gap_mm}mm; }}
.sidebar-facts div {{ background: {spec.light_accent}; }}
.sidebar-table th {{ background: {spec.accent}; }}
.sidebar-table th, .sidebar-table td {{ padding: {spec.table_padding_mm}mm; }}
{layout}
"""


def _modular_css(spec: VariationSpec) -> str:
    aside = _columns(spec, "45mm", "51mm", "58mm")
    radius = _columns(spec, ".8mm", "2mm", "4mm")
    layout = {
        "stacked": """
.modular-grid { display: block; }
.modular-facts { margin-top: 3mm; }
.modular-facts dl {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
}
.modular-facts dl div { border-left: .2mm solid #9fc5be; padding: 1.5mm 2mm; }
""",
        "split": """
.modular-grid {
  display: grid;
  grid-template-columns: 1fr 51mm;
}
""",
        "reflow": """
.modular-grid {
  display: flex;
  flex-direction: column;
}
.modular-facts { margin: 0 0 3mm; order: -1; }
.modular-facts dl {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
}
.modular-sections {
  display: flex;
  gap: 4mm;
}
.modular-sections article {
  display: block;
  flex: 1 1 0;
  margin: 0;
  min-width: 0;
}
.modular-sections article > b { display: block; margin-bottom: 1mm; }
.modular-sections h3,
.modular-sections li { overflow-wrap: anywhere; }
""",
    }[spec.profile]
    return f"""
.modular-page {{ padding: {_mm(12, spec)}mm {_mm(14, spec)}mm {_mm(9, spec)}mm; }}
.modular-mark span, .modular-title > span {{ background: {spec.accent}; color: {spec.accent}; }}
.modular-title h2 {{ font-size: {_pt(18, spec)}pt; text-align: {spec.title_align}; }}
.modular-lead {{ font-size: {_pt(9.2, spec)}pt; line-height: {spec.line_height}; }}
.modular-grid {{ grid-template-columns: 1fr {aside}; gap: {_mm(3, spec)}mm; }}
.modular-sections, .modular-facts, .modular-table-card,
.modular-attachments, .modular-issuer {{ border-radius: {radius}; }}
.modular-sections article {{ margin-bottom: {spec.section_gap_mm}mm; }}
.modular-facts {{ background: {spec.light_accent}; }}
.modular-table-card th, .modular-table-card td {{ padding: {spec.table_padding_mm}mm; }}
{layout}
"""


def _monochrome_css(spec: VariationSpec) -> str:
    meta_width = _columns(spec, "60mm", "67mm", "74mm")
    layout = {
        "stacked": """
.monochrome-body { display: block; }
""",
        "split": """
.monochrome-page { display: block; }
.monochrome-body { display: block; }
.monochrome-sections {
  float: left;
  margin-bottom: 0;
  width: calc(100% - 61mm);
}
.monochrome-facts {
  display: block;
  float: right;
  grid-template-columns: 1fr;
  margin: 0;
  width: 56mm;
}
.monochrome-facts div { grid-template-columns: 20mm 1fr; }
.monochrome-facts div:nth-child(odd) { border-right: 0; }
.monochrome-facts div:nth-child(n+2) { border-top: .2mm solid #999; }
.monochrome-table {
  float: right;
  font-size: 7pt;
  margin-top: 3mm;
  width: 56mm;
}
.monochrome-attachments {
  clear: right;
  display: block;
  float: right;
  margin-top: 3mm;
  width: 56mm;
}
.monochrome-attachments ol { padding-left: 5mm; }
.monochrome-body::after { clear: both; content: ""; display: block; }
""",
        "reflow": """
.monochrome-page { display: block; }
.monochrome-body {
  display: block;
  position: relative;
}
.monochrome-facts {
  display: block;
  grid-template-columns: 1fr;
  margin: 0;
  position: absolute;
  left: 0;
  top: 4mm;
  width: 48mm;
}
.monochrome-facts div { grid-template-columns: 18mm 1fr; }
.monochrome-facts div:nth-child(odd) { border-right: 0; }
.monochrome-facts div:nth-child(n+2) { border-top: .2mm solid #999; }
.monochrome-intro, .monochrome-sections { margin-left: 53mm; }
.monochrome-table { clear: both; }
.monochrome-attachments { margin-top: 2mm; }
""",
    }[spec.profile]
    return f"""
.monochrome-page {{ padding: {_mm(14, spec)}mm {_mm(16, spec)}mm {_mm(9, spec)}mm; }}
.monochrome-header dl {{ width: {meta_width}; }}
.monochrome-header h1 {{ font-size: {_pt(21, spec)}pt; letter-spacing: {_mm(5, spec)}mm; }}
.monochrome-title {{
  color: {spec.accent};
  font-size: {_pt(16, spec)}pt;
  text-align: {spec.title_align};
}}
.monochrome-body {{ font-size: {_pt(9.2, spec)}pt; line-height: {spec.line_height}; }}
.monochrome-sections > li {{ margin-bottom: {spec.section_gap_mm}mm; }}
.monochrome-facts dt, .monochrome-table th, .monochrome-approval th {{ background: {spec.light_accent}; }}
.monochrome-table th, .monochrome-table td {{ padding: {spec.table_padding_mm}mm; }}
{layout}
"""


def _table_report_css(spec: VariationSpec) -> str:
    summary_columns = _columns(spec, "repeat(4, 1fr)", "repeat(4, 1fr)", "repeat(2, 1fr)")
    layout = {
        "stacked": """
.table-report-page { display: block; }
""",
        "split": """
.table-report-page { display: block; }
.table-report-primary {
  float: left;
  width: calc(100% - 58mm);
}
.table-report-summary {
  float: right;
  grid-template-columns: 1fr;
  margin: 0;
  width: 54mm;
}
.table-report-notes { clear: both; padding-top: 3mm; }
""",
        "reflow": """
.table-report-page { display: block; }
.table-report-summary {
  float: left;
  grid-template-columns: 1fr;
  margin: 3mm 4mm 3mm 0;
  width: 46mm;
}
.table-report-notes {
  margin-left: 50mm;
  padding-top: 3mm;
}
.table-report-ending { clear: both; }
""",
    }[spec.profile]
    return f"""
.table-report-page {{ padding: {_mm(12, spec)}mm {_mm(14, spec)}mm {_mm(9, spec)}mm; }}
.table-report-brand > span, .table-report-primary th {{ background: {spec.accent}; }}
.table-report-title {{ border-color: {spec.accent}; }}
.table-report-title h2 {{ font-size: {_pt(19, spec)}pt; text-align: {spec.title_align}; }}
.table-report-intro {{ font-size: {_pt(9, spec)}pt; line-height: {spec.line_height}; }}
.table-report-primary th, .table-report-primary td {{ padding: {spec.table_padding_mm}mm; }}
.table-report-summary {{ grid-template-columns: {summary_columns}; gap: {_mm(2, spec)}mm; }}
.table-report-summary div {{ border-color: {spec.accent}; background: {spec.light_accent}; }}
.table-report-notes article {{ margin-bottom: {spec.section_gap_mm}mm; }}
.table-report-signers {{ margin-top: {_mm(7, spec)}mm; }}
{layout}
"""


def _long_form_css(spec: VariationSpec) -> str:
    x = _mm(18, spec)
    facts_columns = _columns(spec, "repeat(4, 1fr)", "repeat(4, 1fr)", "repeat(2, 1fr)")
    layout = {
        "stacked": """
.long-body { display: block; }
""",
        "split": """
.long-body {
  display: grid;
  gap: 3mm 5mm;
  grid-template-columns: 1fr 1fr;
}
.long-section { display: block; margin: 0; }
.long-section > div:first-child { margin-bottom: 1.5mm; }
""",
        "reflow": """
.long-body {
  display: grid;
  gap: 3mm;
  grid-template-columns: repeat(4, 1fr);
}
.long-section { display: block; margin: 0; }
.long-section > div:first-child { margin-bottom: 1.5mm; }
.long-section h3 { font-size: 9pt; }
.long-section ol { font-size: 7.2pt; padding-left: 4mm; }
""",
    }[spec.profile]
    return f"""
.long-page {{ padding: {_mm(14, spec)}mm {x}mm {_mm(12, spec)}mm; }}
.long-header {{ border-color: {spec.accent}; }}
.long-brand > span, .long-section > div:first-child span,
.long-program th {{ border-color: {spec.accent}; background: {spec.accent}; }}
.long-title h2 {{ color: {spec.accent}; font-size: {_pt(20, spec)}pt; text-align: {spec.title_align}; }}
.long-title p {{ font-size: {_pt(9, spec)}pt; line-height: {spec.line_height}; }}
.long-section {{ margin-bottom: {spec.section_gap_mm}mm; }}
.long-section h3, .long-program h3 {{ color: {spec.accent}; }}
.long-section ol {{ font-size: {_pt(8.7, spec)}pt; line-height: {spec.line_height}; }}
.long-facts {{ grid-template-columns: {facts_columns}; }}
.long-program th, .long-program td {{ padding: {spec.table_padding_mm}mm; }}
.long-page-footer {{ left: {x}mm; right: {x}mm; }}
{layout}
"""


def _checklist_css(spec: VariationSpec) -> str:
    meta_columns = _columns(spec, "repeat(3, 1fr)", "repeat(3, 1fr)", "repeat(2, 1fr)")
    summary_side = _columns(spec, "54mm", "58mm", "62mm")
    layout = {
        "stacked": """
.checklist-page { display: block; }
""",
        "split": """
.checklist-page { display: block; }
.checklist-table {
  float: left;
  width: calc(100% - 62mm);
}
.checklist-summary {
  display: block;
  float: right;
  margin-top: 3mm;
  width: 58mm;
}
.checklist-summary dl { margin-top: 3mm; }
.checklist-approval { clear: both; padding-top: 3mm; }
""",
        "reflow": """
.checklist-page { display: block; }
.checklist-meta {
  float: left;
  grid-template-columns: 1fr;
  margin: 4mm 4mm 3mm 0;
  width: 46mm;
}
.checklist-meta div { grid-template-columns: 1fr; }
.checklist-guide { margin-left: 50mm; }
.checklist-table { margin-left: 50mm; width: calc(100% - 50mm); }
.checklist-summary { clear: both; }
""",
    }[spec.profile]
    return f"""
.checklist-page {{ padding: {_mm(10, spec)}mm {_mm(13, spec)}mm {_mm(8, spec)}mm; }}
.checklist-brand > span, .checklist-table th {{ background: {spec.accent}; }}
.checklist-header > strong, .checklist-title, .checklist-footer {{ border-color: {spec.accent}; color: {spec.accent}; }}
.checklist-title h2 {{ color: {spec.accent}; font-size: {_pt(18, spec)}pt; text-align: {spec.title_align}; }}
.checklist-title p {{ font-size: {_pt(8.5, spec)}pt; line-height: {spec.line_height}; }}
.checklist-meta {{ grid-template-columns: {meta_columns}; }}
.checklist-meta dt, .checklist-guide, .checklist-summary dt,
.checklist-approval th {{ background: {spec.light_accent}; }}
.checklist-table th, .checklist-table td {{ padding: {spec.table_padding_mm}mm 1.3mm; }}
.checklist-summary {{ grid-template-columns: 1fr {summary_side}; }}
.checklist-ending > div:last-child strong {{ color: {spec.accent}; }}
{layout}
"""


_CSS_RENDERERS = {
    "01_classic_municipal": _classic_css,
    "02_fire_station": _banner_css,
    "03_internal_approval": _approval_css,
    "04_personnel_notice": _proclamation_css,
    "05_field_report": _sidebar_css,
    "06_modern_public": _modular_css,
    "07_monochrome_hwp": _monochrome_css,
    "08_table_first_report": _table_report_css,
    "09_long_form": _long_form_css,
    "10_checklist_form": _checklist_css,
}
