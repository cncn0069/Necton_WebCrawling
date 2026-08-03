"""훈령·예규·고시 공용 Jinja2 + WeasyPrint 렌더러."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import random
from typing import Any, Collection, Mapping, Sequence

import fitz
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from weasyprint import HTML


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
ADMINISTRATIVE_RULE_MAX_PAGES = 10
ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "slug": "rule_01_promulgation",
        "template": "administrative_rule/base.html",
        "variant_class": "rule-promulgation",
    },
    {
        "slug": "rule_02_article_rail",
        "template": "administrative_rule/base.html",
        "variant_class": "rule-rail",
    },
    {
        "slug": "rule_03_gazette_columns",
        "template": "administrative_rule/base.html",
        "variant_class": "rule-columns",
    },
    {
        "slug": "rule_04_notice_frame",
        "template": "administrative_rule/base.html",
        "variant_class": "rule-frame",
    },
)
_VARIANTS_BY_SLUG = {
    variant["slug"]: variant
    for variant in ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS
}
_DENSITIES = ("balanced", "compact", "airy")
_MAX_MULTICOLUMN_ITEM_CHARACTERS = 240
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(("html", "xml")),
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class AdministrativeRuleVariationSpec:
    template_slug: str
    index: int
    seed: int
    density: str
    font_scale: float
    line_height: float
    horizontal_margin_mm: float
    block_gap_mm: float
    key_value_columns: int
    list_columns: int

    @property
    def slug(self) -> str:
        return f"{self.index:02d}_{self.density}"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_administrative_rule_variation_specs(
    template_slug: str,
    *,
    count: int = 1,
    base_seed: int = 20260730,
) -> list[AdministrativeRuleVariationSpec]:
    if template_slug not in _VARIANTS_BY_SLUG:
        raise ValueError(f"Unknown administrative rule template: {template_slug}")
    if not 1 <= count <= 10:
        raise ValueError("count must be between 1 and 10")

    template_number = int(template_slug.split("_", 2)[1])
    specs: list[AdministrativeRuleVariationSpec] = []
    for offset in range(count):
        density = _DENSITIES[offset % len(_DENSITIES)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
        ranges = {
            "compact": ((0.91, 0.96), (1.52, 1.62), (16.5, 19.0), (2.8, 4.0)),
            "balanced": ((0.97, 1.01), (1.68, 1.78), (19.0, 22.0), (4.5, 5.8)),
            "airy": ((1.01, 1.05), (1.8, 1.9), (21.5, 24.0), (5.8, 7.0)),
        }
        font_range, line_range, margin_range, gap_range = ranges[density]
        key_value_columns = {
            "balanced": 2,
            "compact": 3,
            "airy": 1,
        }[density]
        list_columns = {
            "balanced": 1,
            "compact": 2,
            "airy": 1,
        }[density]
        specs.append(
            AdministrativeRuleVariationSpec(
                template_slug=template_slug,
                index=offset + 1,
                seed=seed,
                density=density,
                font_scale=round(rng.uniform(*font_range), 3),
                line_height=round(rng.uniform(*line_range), 3),
                horizontal_margin_mm=round(rng.uniform(*margin_range), 2),
                block_gap_mm=round(rng.uniform(*gap_range), 2),
                key_value_columns=key_value_columns,
                list_columns=list_columns,
            )
        )
    return specs


def _selected_variants(
    template_slugs: Collection[str] | None,
) -> tuple[dict[str, str], ...]:
    if template_slugs is None:
        return ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS
    requested = frozenset(template_slugs)
    unknown = sorted(requested - _VARIANTS_BY_SLUG.keys())
    if unknown:
        raise ValueError(
            "Unknown administrative rule template(s): " + ", ".join(unknown)
        )
    return tuple(
        variant
        for variant in ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


def _variation_css(spec: AdministrativeRuleVariationSpec) -> str:
    return f"""
@page {{
  margin-left: {spec.horizontal_margin_mm}mm;
  margin-right: {spec.horizontal_margin_mm}mm;
}}
.rule-document {{
  --font-scale: {spec.font_scale};
  --line-height: {spec.line_height};
  --block-gap: {spec.block_gap_mm}mm;
}}
.rule-document .key-values {{
  grid-template-columns: repeat(
    {spec.key_value_columns},
    minmax(0, 1fr)
  );
  break-inside: auto;
}}
.rule-document .key-values > div {{
  break-inside: auto;
}}
.rule-document .bullet-list {{
  columns: {spec.list_columns};
  column-gap: 8mm;
  break-inside: auto;
}}
.rule-document .bullet-list li {{
  break-inside: auto;
  orphans: 2;
  widows: 2;
}}
"""


def _adapt_columns_to_content(
    spec: AdministrativeRuleVariationSpec,
    base_context: Mapping[str, Any],
) -> AdministrativeRuleVariationSpec:
    key_value_columns = spec.key_value_columns
    list_columns = spec.list_columns
    for block in base_context.get("blocks") or ():
        if not isinstance(block, Mapping):
            continue
        if block.get("kind") == "key_value":
            entries = block.get("entries") or ()
            if any(
                len(str(entry.get("key") or ""))
                + len(str(entry.get("value") or ""))
                > _MAX_MULTICOLUMN_ITEM_CHARACTERS
                for entry in entries
                if isinstance(entry, Mapping)
            ):
                key_value_columns = 1
        elif block.get("kind") == "bullet_list":
            if any(
                len(str(item)) > _MAX_MULTICOLUMN_ITEM_CHARACTERS
                for item in block.get("items") or ()
            ):
                list_columns = 1
    return replace(
        spec,
        key_value_columns=key_value_columns,
        list_columns=list_columns,
    )


def _normalized(value: object) -> str:
    return "".join(str(value).split())


def _text_is_present(
    normalized_value: str,
    required_tokens: Sequence[str],
    normalized_pdf_text: str,
    pdf_tokens: Sequence[str],
) -> bool:
    if normalized_value in normalized_pdf_text:
        return True
    if len(required_tokens) < 2:
        return False

    token_index = 0
    for token in pdf_tokens:
        if token == required_tokens[token_index]:
            token_index += 1
            if token_index == len(required_tokens):
                return True
    return False


def _inspect_pdf(pdf_path: Path) -> tuple[int, str]:
    with fitz.open(pdf_path) as document:
        text = "\n".join(page.get_text() for page in document)
        return document.page_count, text


def render_administrative_rule_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 1,
    base_seed: int = 20260730,
    template_slugs: Collection[str] | None = None,
    required_source_texts: Sequence[str] = (),
    max_pages: int = ADMINISTRATIVE_RULE_MAX_PAGES,
) -> list[dict[str, object]]:
    """행정규칙 변주를 렌더링하고 페이지 수와 원문 포함 여부를 검증한다."""

    if not 1 <= per_template <= 10:
        raise ValueError("per_template must be between 1 and 10")
    variants = _selected_variants(template_slugs)
    if not variants:
        raise ValueError("At least one administrative rule template is required")

    output_dir.mkdir(parents=True, exist_ok=True)
    required = tuple(
        dict.fromkeys(
            (
                str(base_context.get("title") or ""),
                *required_source_texts,
            )
        )
    )
    required = tuple(text for text in required if text.strip())
    required_checks = tuple(
        (
            text,
            _normalized(text),
            tuple(
                normalized_token
                for token in text.split()
                if (normalized_token := _normalized(token))
            ),
        )
        for text in required
    )
    manifest: list[dict[str, object]] = []
    failures: list[str] = []

    for variant in variants:
        template = _ENVIRONMENT.get_template(variant["template"])
        template_dir = output_dir / variant["slug"]
        template_dir.mkdir(parents=True, exist_ok=True)
        for spec in build_administrative_rule_variation_specs(
            variant["slug"],
            count=per_template,
            base_seed=base_seed,
        ):
            effective_spec = _adapt_columns_to_content(spec, base_context)
            html = template.render(
                **base_context,
                variant_class=variant["variant_class"],
                density_class=f"density-{spec.density}",
                variation_css=_variation_css(effective_spec),
            )
            html_path = template_dir / f"{spec.slug}.html"
            pdf_path = template_dir / f"{spec.slug}.pdf"
            html_path.write_text(html, encoding="utf-8")
            HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(pdf_path)

            page_count, pdf_text = _inspect_pdf(pdf_path)
            normalized_pdf_text = _normalized(pdf_text)
            pdf_tokens = tuple(
                _normalized(token)
                for token in pdf_text.split()
                if _normalized(token)
            )
            missing = [
                value
                for value, normalized_value, required_tokens
                in required_checks
                if not _text_is_present(
                    normalized_value,
                    required_tokens,
                    normalized_pdf_text,
                    pdf_tokens,
                )
            ]
            status = "ok" if page_count <= max_pages and not missing else "rejected"
            if status == "rejected":
                failures.append(
                    f"{variant['slug']}/{spec.slug}: "
                    f"pages={page_count}, missing={len(missing)}"
                )
            manifest.append(
                {
                    "renderer_family": "administrative_rule",
                    "template_slug": variant["slug"],
                    "template": variant["template"],
                    "variation_slug": spec.slug,
                    "status": status,
                    "max_pages": max_pages,
                    "actual_pages": page_count,
                    "source_text_present": not missing,
                    "parameters": effective_spec.to_dict(),
                    "html": str(html_path),
                    "pdf": str(pdf_path),
                }
            )

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        raise RuntimeError(
            "Administrative rule render validation failed:\n"
            + "\n".join(f"- {failure}" for failure in failures)
        )
    return manifest
