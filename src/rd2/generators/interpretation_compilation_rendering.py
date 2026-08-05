"""질의회시집 전용 Jinja2 + WeasyPrint 렌더러."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from typing import Any, Collection, Mapping, Sequence

import fitz
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from rd2.generators.weasyprint_runtime import HTML


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
INTERPRETATION_COMPILATION_MAX_PAGES = 10
_DEFAULT_VARIATION_SEED = 20260730
_MAX_VARIATIONS_PER_TEMPLATE = 10
_MAX_TEXT_CHARACTERS_PER_PAGE = 4_000
_MAX_BLOCKS = 240
_MAX_COLLECTION_ITEMS = 1_200
_MAX_TABLE_CELLS = 4_000
_MAX_MULTICOLUMN_ITEM_CHARACTERS = 240
INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "slug": "interpretation_01_sequence",
        "template": "interpretation_compilation/base.html",
        "variant_class": "interpretation-sequence",
    },
    {
        "slug": "interpretation_02_index",
        "template": "interpretation_compilation/base.html",
        "variant_class": "interpretation-index",
    },
    {
        "slug": "interpretation_03_cards",
        "template": "interpretation_compilation/base.html",
        "variant_class": "interpretation-cards",
    },
    {
        "slug": "interpretation_04_margin",
        "template": "interpretation_compilation/base.html",
        "variant_class": "interpretation-margin",
    },
)
_VARIANTS_BY_SLUG = {
    variant["slug"]: variant
    for variant in INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS
}
_DENSITIES = ("balanced", "compact", "airy")
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(("html", "xml")),
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class InterpretationVariationSpec:
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


def build_interpretation_variation_specs(
    template_slug: str,
    *,
    count: int = 1,
    base_seed: int = _DEFAULT_VARIATION_SEED,
    start_offset: int = 0,
) -> list[InterpretationVariationSpec]:
    """같은 입력에서 재현 가능한 밀도 변주를 만든다."""

    if template_slug not in _VARIANTS_BY_SLUG:
        raise ValueError(f"Unknown interpretation template: {template_slug}")
    if not 1 <= count <= _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "count must be between 1 and "
            f"{_MAX_VARIATIONS_PER_TEMPLATE}"
        )
    if start_offset < 0:
        raise ValueError("start_offset must be at least 0")
    if start_offset + count > _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "start_offset + count must be at most "
            f"{_MAX_VARIATIONS_PER_TEMPLATE}"
        )

    template_number = int(template_slug.split("_", 2)[1])
    ranges = {
        "compact": ((0.92, 0.96), (1.55, 1.64), (17.0, 19.0), (3.0, 4.2)),
        "balanced": ((0.98, 1.01), (1.68, 1.76), (19.0, 21.5), (4.5, 5.8)),
        "airy": ((1.01, 1.05), (1.8, 1.9), (21.0, 23.0), (5.8, 7.0)),
    }
    specs: list[InterpretationVariationSpec] = []
    for offset in range(start_offset, start_offset + count):
        density = _DENSITIES[offset % len(_DENSITIES)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
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
            InterpretationVariationSpec(
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
        return INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS
    requested = frozenset(template_slugs)
    unknown = sorted(requested - _VARIANTS_BY_SLUG.keys())
    if unknown:
        raise ValueError(
            "Unknown interpretation template(s): " + ", ".join(unknown)
        )
    return tuple(
        variant
        for variant in INTERPRETATION_COMPILATION_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


def _variation_css(spec: InterpretationVariationSpec) -> str:
    return f"""
@page interpretation-content {{
  margin-left: {spec.horizontal_margin_mm}mm;
  margin-right: {spec.horizontal_margin_mm}mm;
}}
.interpretation-document {{
  --font-scale: {spec.font_scale};
  --line-height: {spec.line_height};
  --block-gap: {spec.block_gap_mm}mm;
}}
.interpretation-document .block-key-value {{
  grid-template-columns: repeat(
    {spec.key_value_columns},
    minmax(0, 1fr)
  );
  break-inside: auto;
}}
.interpretation-document .block-key-value > div {{
  break-inside: auto;
}}
.interpretation-document .block-bullet-list {{
  columns: {spec.list_columns};
  column-gap: 8mm;
  break-inside: auto;
}}
.interpretation-document .block-bullet-list li {{
  break-inside: auto;
  orphans: 2;
  widows: 2;
}}
"""


def _adapt_columns_to_content(
    spec: InterpretationVariationSpec,
    base_context: Mapping[str, Any],
) -> InterpretationVariationSpec:
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


def _inspect_pdf(pdf_path: Path) -> tuple[int, str]:
    with fitz.open(pdf_path) as document:
        text = "".join(
            "".join(page.get_text().split())
            for page in document
        )
        return document.page_count, text


def _validate_render_budget(
    base_context: Mapping[str, Any],
    *,
    max_pages: int,
) -> None:
    """명백히 과대한 입력은 WeasyPrint 실행 전에 거부한다."""

    blocks = base_context.get("blocks")
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        raise ValueError("blocks must be a sequence")

    text_characters = sum(
        len(str(base_context.get(key) or ""))
        for key in ("title", "agency_name")
    )
    collection_items = 0
    table_cells = 0

    for block in blocks:
        if not isinstance(block, Mapping):
            raise ValueError("each block must be a mapping")
        kind = block.get("kind")
        if kind == "paragraph":
            text_characters += len(str(block.get("text") or ""))
        elif kind == "key_value":
            entries = block.get("entries") or ()
            collection_items += len(entries)
            for entry in entries:
                text_characters += len(str(entry.get("key") or ""))
                text_characters += len(str(entry.get("value") or ""))
        elif kind == "bullet_list":
            items = block.get("items") or ()
            collection_items += len(items)
            text_characters += sum(len(str(item)) for item in items)
        elif kind == "table":
            columns = block.get("columns") or ()
            rows = block.get("rows") or ()
            collection_items += len(rows)
            table_cells += len(columns) + sum(len(row) for row in rows)
            text_characters += sum(len(str(value)) for value in columns)
            text_characters += sum(
                len(str(value))
                for row in rows
                for value in row
            )
        elif kind == "attachment_reference":
            collection_items += 1
            text_characters += sum(
                len(str(block.get(key) or ""))
                for key in ("attachment_id", "label", "description")
            )

    for signer in base_context.get("signers") or ():
        text_characters += sum(
            len(str(signer.get(key) or ""))
            for key in ("role", "name", "date")
        )
    for event in base_context.get("administrative_events") or ():
        text_characters += sum(
            len(str(event.get(key) or ""))
            for key in ("type", "date", "text")
        )

    violations: list[str] = []
    if len(blocks) > _MAX_BLOCKS:
        violations.append(f"blocks={len(blocks)}>{_MAX_BLOCKS}")
    character_limit = max_pages * _MAX_TEXT_CHARACTERS_PER_PAGE
    if text_characters > character_limit:
        violations.append(
            f"text_characters={text_characters}>{character_limit}"
        )
    if collection_items > _MAX_COLLECTION_ITEMS:
        violations.append(
            f"collection_items={collection_items}>{_MAX_COLLECTION_ITEMS}"
        )
    if table_cells > _MAX_TABLE_CELLS:
        violations.append(
            f"table_cells={table_cells}>{_MAX_TABLE_CELLS}"
        )
    if violations:
        raise ValueError(
            "Interpretation compilation input exceeds render budget: "
            + ", ".join(violations)
        )


def _publish_output_directory(
    candidate_dir: Path,
    output_dir: Path,
    transaction_dir: Path,
) -> None:
    """검증이 끝난 한 세대만 출력 경로에 보이도록 교체한다."""

    previous_dir = transaction_dir / "previous"
    if output_dir.exists() or output_dir.is_symlink():
        output_dir.replace(previous_dir)
    try:
        candidate_dir.replace(output_dir)
    except Exception:
        if previous_dir.exists() or previous_dir.is_symlink():
            previous_dir.replace(output_dir)
        raise


def render_interpretation_compilation_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 1,
    base_seed: int = _DEFAULT_VARIATION_SEED,
    variation_offset: int = 0,
    template_slugs: Collection[str] | None = None,
    required_source_texts: Sequence[str] = (),
    max_pages: int = INTERPRETATION_COMPILATION_MAX_PAGES,
) -> list[dict[str, object]]:
    """일반 GeneratedDocumentIR 블록을 순서대로 렌더링하고 검증한다."""

    if not 1 <= per_template <= _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "per_template must be between 1 and "
            f"{_MAX_VARIATIONS_PER_TEMPLATE}"
        )
    if variation_offset < 0:
        raise ValueError("variation_offset must be at least 0")
    if variation_offset + per_template > _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "variation_offset + per_template must be at most "
            f"{_MAX_VARIATIONS_PER_TEMPLATE}"
        )
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    _validate_render_budget(base_context, max_pages=max_pages)
    variants = _selected_variants(template_slugs)
    if not variants:
        raise ValueError("At least one interpretation template is required")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    required = tuple(
        dict.fromkeys(
            (
                str(base_context.get("title") or ""),
                *required_source_texts,
            )
        )
    )
    required = tuple(text for text in required if text.strip())
    manifest: list[dict[str, object]] = []
    failures: list[str] = []
    publish_pairs: list[tuple[Path, Path]] = []

    with TemporaryDirectory(
        prefix=f".{output_dir.name}-interpretation-",
        dir=output_dir.parent,
    ) as transaction_name:
        transaction_dir = Path(transaction_name)
        work_dir = transaction_dir / "work"
        candidate_dir = transaction_dir / "candidate"
        candidate_dir.mkdir()

        for variant in variants:
            if failures:
                break
            template = _ENVIRONMENT.get_template(variant["template"])
            staged_template_dir = work_dir / variant["slug"]
            staged_template_dir.mkdir(parents=True, exist_ok=True)
            candidate_template_dir = candidate_dir / variant["slug"]
            for spec in build_interpretation_variation_specs(
                variant["slug"],
                count=per_template,
                base_seed=base_seed,
                start_offset=variation_offset,
            ):
                effective_spec = _adapt_columns_to_content(
                    spec,
                    base_context,
                )
                html = template.render(
                    **base_context,
                    variant_class=variant["variant_class"],
                    density_class=f"density-{spec.density}",
                    variation_css=_variation_css(effective_spec),
                )
                staged_html_path = staged_template_dir / f"{spec.slug}.html"
                staged_pdf_path = staged_template_dir / f"{spec.slug}.pdf"
                staged_html_path.write_text(html, encoding="utf-8")
                HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(
                    staged_pdf_path
                )

                page_count, pdf_text = _inspect_pdf(staged_pdf_path)
                missing = [
                    value
                    for value in required
                    if _normalized(value) not in pdf_text
                ]
                status = (
                    "ok"
                    if page_count <= max_pages and not missing
                    else "rejected"
                )
                if status == "rejected":
                    failures.append(
                        f"{variant['slug']}/{spec.slug}: "
                        f"pages={page_count}, missing={len(missing)}"
                    )
                manifest.append(
                    {
                        "renderer_family": "interpretation_compilation",
                        "template_slug": variant["slug"],
                        "template": variant["template"],
                        "variation_slug": spec.slug,
                        "status": status,
                        "artifact_published": False,
                        "max_pages": max_pages,
                        "actual_pages": page_count,
                        "source_text_present": not missing,
                        "parameters": effective_spec.to_dict(),
                        "html": None,
                        "pdf": None,
                    }
                )
                publish_pairs.extend(
                    (
                        (
                            staged_html_path,
                            candidate_template_dir / f"{spec.slug}.html",
                        ),
                        (
                            staged_pdf_path,
                            candidate_template_dir / f"{spec.slug}.pdf",
                        ),
                    )
                )
                if status == "rejected":
                    break

        if not failures:
            for staged_path, candidate_path in publish_pairs:
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.replace(candidate_path)
            for entry in manifest:
                variation_slug = str(entry["variation_slug"])
                template_dir = output_dir / str(entry["template_slug"])
                entry["artifact_published"] = True
                entry["html"] = str(
                    template_dir / f"{variation_slug}.html"
                )
                entry["pdf"] = str(
                    template_dir / f"{variation_slug}.pdf"
                )

        (candidate_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _publish_output_directory(candidate_dir, output_dir, transaction_dir)

        if failures:
            raise RuntimeError(
                "Interpretation compilation render validation failed:\n"
                + "\n".join(f"- {failure}" for failure in failures)
            )
        return manifest
