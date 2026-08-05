"""입찰·공고 계열 Jinja2 + WeasyPrint 전용 렌더러."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any, Collection, Mapping, Sequence
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import fitz
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from rd2.generators.weasyprint_runtime import HTML, URLFetcher


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
NOTICE_MAX_PAGES = 10
NOTICE_TEMPLATE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "slug": "notice_01_classic_gazette",
        "template": "notice/base.html",
        "variant_class": "notice-classic",
    },
    {
        "slug": "notice_02_structured",
        "template": "notice/base.html",
        "variant_class": "notice-structured",
    },
    {
        "slug": "notice_03_record_rail",
        "template": "notice/base.html",
        "variant_class": "notice-rail",
    },
)
_LAYOUT_CYCLES = {
    "notice_01_classic_gazette": ((2, 1), (3, 2), (1, 1)),
    "notice_02_structured": ((2, 1), (3, 2), (1, 1)),
    "notice_03_record_rail": ((2, 1), (3, 2), (1, 1)),
}
_DENSITIES = ("balanced", "compact", "airy")
_MAX_VARIATIONS_PER_TEMPLATE = 10
_MAX_TITLE_CHARACTERS = 300
_MAX_BLOCKS = 240
_MAX_CHARACTERS = 40_000
_MAX_LIST_ITEMS = 1_200
_MAX_TABLE_ROWS = 800
_MAX_TABLE_CELLS = 4_000
_MAX_TABLE_COLUMNS = 20
_MAX_MULTICOLUMN_ITEM_CHARACTERS = 240
_MAX_INPUT_METADATA_BYTES = 32_000
_ALLOWED_ASSET_ROOTS = (
    TEMPLATE_DIR.resolve(),
    (TEMPLATE_DIR.parent / "assets").resolve(),
)
_VARIANTS_BY_SLUG = {
    variant["slug"]: variant for variant in NOTICE_TEMPLATE_VARIANTS
}
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(("html", "xml")),
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class NoticeVariationSpec:
    """공고 서식 한 변주를 재현하는 레이아웃 설정."""

    template_slug: str
    index: int
    seed: int
    density: str
    key_value_columns: int
    list_columns: int
    font_scale: float
    line_height: float
    horizontal_margin_mm: float
    block_gap_mm: float
    table_padding_mm: float

    @property
    def slug(self) -> str:
        return f"{self.index:02d}_{self.density}"

    @property
    def css_class(self) -> str:
        return (
            f"density-{self.density} "
            f"kv-columns-{self.key_value_columns} "
            f"list-columns-{self.list_columns}"
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_notice_variation_specs(
    template_slug: str,
    *,
    count: int = 1,
    base_seed: int = 20260730,
    start_offset: int = 0,
) -> list[NoticeVariationSpec]:
    """기본 시안마다 재현 가능한 구조 변주를 만든다."""

    if template_slug not in _LAYOUT_CYCLES:
        raise ValueError(f"Unknown notice template: {template_slug}")
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
    layouts = _LAYOUT_CYCLES[template_slug]
    ranges = {
        "balanced": (
            (0.98, 1.01),
            (1.62, 1.72),
            (19.0, 21.0),
            (5.0, 6.0),
            (2.2, 2.6),
        ),
        "compact": (
            (0.91, 0.96),
            (1.44, 1.54),
            (14.0, 17.0),
            (3.0, 4.0),
            (1.5, 1.9),
        ),
        "airy": (
            (1.01, 1.05),
            (1.72, 1.84),
            (22.0, 25.0),
            (6.5, 8.0),
            (2.6, 3.0),
        ),
    }
    specs: list[NoticeVariationSpec] = []
    for offset in range(start_offset, start_offset + count):
        density = _DENSITIES[offset % len(_DENSITIES)]
        key_value_columns, list_columns = layouts[offset % len(layouts)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
        font_range, line_range, margin_range, gap_range, table_range = ranges[
            density
        ]
        specs.append(
            NoticeVariationSpec(
                template_slug=template_slug,
                index=offset + 1,
                seed=seed,
                density=density,
                key_value_columns=key_value_columns,
                list_columns=list_columns,
                font_scale=round(rng.uniform(*font_range), 3),
                line_height=round(rng.uniform(*line_range), 3),
                horizontal_margin_mm=round(rng.uniform(*margin_range), 2),
                block_gap_mm=round(rng.uniform(*gap_range), 2),
                table_padding_mm=round(rng.uniform(*table_range), 2),
            )
        )
    return specs


def _adapt_columns_to_content(
    spec: NoticeVariationSpec,
    base_context: Mapping[str, Any],
) -> NoticeVariationSpec:
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


def _variation_css(spec: NoticeVariationSpec) -> str:
    return f"""
@page notice-content {{
  margin-left: {spec.horizontal_margin_mm}mm;
  margin-right: {spec.horizontal_margin_mm}mm;
}}
.notice-document {{
  --notice-font-scale: {spec.font_scale};
  --notice-line-height: {spec.line_height};
  --notice-block-gap: {spec.block_gap_mm}mm;
  --notice-table-padding: {spec.table_padding_mm}mm;
}}
"""


def _selected_variants(
    template_slugs: Collection[str] | None,
) -> tuple[dict[str, str], ...]:
    if template_slugs is None:
        return NOTICE_TEMPLATE_VARIANTS
    requested = frozenset(template_slugs)
    unknown = sorted(requested - _VARIANTS_BY_SLUG.keys())
    if unknown:
        raise ValueError("Unknown notice template(s): " + ", ".join(unknown))
    return tuple(
        variant
        for variant in NOTICE_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


class _RestrictedURLFetcher(URLFetcher):
    """번들 자산과 data URI 외의 외부 자원 접근을 막는다."""

    def fetch(self, url: str, headers: Mapping[str, str] | None = None) -> Any:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            # ``unquote``만 쓰면 윈도우에서 file:///C:/... 의 앞 슬래시가
            # 남아 경로가 ``C:CODE\...``로 뭉개진다 - 자산이 검색 루트
            # 밖으로 보여 번들 CSS까지 차단된다. url2pathname이 플랫폼별
            # 변환을 담당한다(POSIX에서는 결과가 같다).
            resource_path = Path(url2pathname(parsed.path)).resolve()
            if not any(
                resource_path.is_relative_to(root)
                for root in _ALLOWED_ASSET_ROOTS
            ):
                raise ValueError(
                    "Resource path is outside the renderer asset roots"
                )
        return super().fetch(url, headers=headers)


_RESTRICTED_URL_FETCHER = _RestrictedURLFetcher(
    allowed_protocols=("data", "file"),
    allow_redirects=False,
    fail_on_errors=True,
)


def _validate_render_budget(base_context: Mapping[str, Any]) -> None:
    title = str(base_context.get("title") or "")
    if len(title) > _MAX_TITLE_CHARACTERS:
        raise ValueError(
            f"notice title exceeds {_MAX_TITLE_CHARACTERS} characters"
        )

    raw_blocks = base_context.get("blocks")
    blocks = (
        list(raw_blocks)
        if isinstance(raw_blocks, Sequence)
        and not isinstance(raw_blocks, (str, bytes))
        else []
    )
    if len(blocks) > _MAX_BLOCKS:
        raise ValueError(f"notice supports at most {_MAX_BLOCKS} blocks")

    character_count = len(title) + len(str(base_context.get("agency_name") or ""))
    list_item_count = 0
    table_row_count = 0
    table_cell_count = 0
    max_table_columns = 0
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("kind")
        character_count += len(str(block.get("block_id") or ""))
        if kind == "paragraph":
            character_count += len(str(block.get("text") or ""))
        elif kind == "key_value":
            entries = block.get("entries") or ()
            list_item_count += len(entries)
            for entry in entries:
                if isinstance(entry, Mapping):
                    character_count += len(str(entry.get("key") or ""))
                    character_count += len(str(entry.get("value") or ""))
        elif kind == "bullet_list":
            items = block.get("items") or ()
            list_item_count += len(items)
            character_count += sum(len(str(item)) for item in items)
        elif kind == "table":
            columns = block.get("columns") or ()
            rows = block.get("rows") or ()
            max_table_columns = max(max_table_columns, len(columns))
            table_row_count += len(rows)
            table_cell_count += len(columns) + sum(len(row) for row in rows)
            character_count += sum(len(str(column)) for column in columns)
            character_count += sum(
                len(str(cell)) for row in rows for cell in row
            )
        elif kind == "attachment_reference":
            character_count += sum(
                len(str(block.get(key) or ""))
                for key in ("attachment_id", "label", "description")
            )

    for event in base_context.get("administrative_events") or ():
        if isinstance(event, Mapping):
            character_count += len(str(event.get("date") or ""))
            character_count += len(str(event.get("text") or ""))
    for signer in base_context.get("signers") or ():
        if isinstance(signer, Mapping):
            character_count += sum(
                len(str(signer.get(key) or ""))
                for key in ("role", "name", "date")
            )

    limits = (
        (character_count, _MAX_CHARACTERS, "characters"),
        (list_item_count, _MAX_LIST_ITEMS, "list items"),
        (table_row_count, _MAX_TABLE_ROWS, "table rows"),
        (table_cell_count, _MAX_TABLE_CELLS, "table cells"),
        (max_table_columns, _MAX_TABLE_COLUMNS, "table columns"),
    )
    for actual, maximum, label in limits:
        if actual > maximum:
            raise ValueError(
                f"notice exceeds render budget: "
                f"{actual} {label}, maximum {maximum}"
            )


def _inspect_pdf(
    pdf_path: Path,
    *,
    exclude_left_rail: bool = False,
) -> tuple[int, str]:
    with fitz.open(pdf_path) as document:
        page_texts: list[str] = []
        for page in document:
            footer_start = page.rect.height * 0.94
            rail_end = page.rect.width * 0.10
            blocks = page.get_text("blocks", sort=True)
            page_texts.append(
                " ".join(
                    str(block[4])
                    for block in blocks
                    if float(block[1]) < footer_start
                    and (
                        not exclude_left_rail
                        or float(block[2]) > rail_end
                    )
                )
            )
        return document.page_count, "".join(" ".join(page_texts).split())


def _normalized(value: object) -> str:
    return "".join(str(value).split())


def _missing_texts(
    required_texts: Sequence[str],
    normalized_pdf_text: str,
) -> list[str]:
    required: list[tuple[int, str, str]] = []
    for index, value in enumerate(required_texts):
        normalized = _normalized(value)
        if normalized:
            required.append((index, normalized, value))

    occupied: list[tuple[int, int]] = []
    missing_indexes: set[int] = set()
    for index, normalized, _ in sorted(
        required,
        key=lambda item: (-len(item[1]), item[0]),
    ):
        search_from = 0
        while True:
            start = normalized_pdf_text.find(normalized, search_from)
            if start < 0:
                missing_indexes.add(index)
                break
            end = start + len(normalized)
            insertion_index = bisect_left(occupied, (start, end))
            overlaps_previous = (
                insertion_index > 0
                and occupied[insertion_index - 1][1] > start
            )
            overlaps_next = (
                insertion_index < len(occupied)
                and occupied[insertion_index][0] < end
            )
            if not overlaps_previous and not overlaps_next:
                occupied.insert(insertion_index, (start, end))
                break
            search_from = start + 1

    return [
        value
        for index, _, value in required
        if index in missing_indexes
    ]


def _missing_summary(values: Sequence[str]) -> str:
    samples = ", ".join(
        f"sha256={sha256(value.encode('utf-8')).hexdigest()[:12]}"
        f"/length={len(value)}"
        for value in values[:3]
    )
    return f"missing source text: count={len(values)}; {samples}"


def render_notice_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 1,
    base_seed: int = 20260730,
    variation_offset: int = 0,
    template_slugs: Collection[str] | None = None,
    required_source_texts: Sequence[str] = (),
    max_pages: int = NOTICE_MAX_PAGES,
    input_metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, object]]:
    """공고 변주를 렌더링하고 원문 보존과 페이지 수를 검증한다."""

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
    metadata_size = len(
        json.dumps(
            dict(input_metadata) if input_metadata else {},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if metadata_size > _MAX_INPUT_METADATA_BYTES:
        raise ValueError(
            "notice input metadata exceeds "
            f"{_MAX_INPUT_METADATA_BYTES} bytes"
        )
    _validate_render_budget(base_context)
    variants = _selected_variants(template_slugs)
    if not variants:
        raise ValueError("At least one notice template is required")

    output_dir.mkdir(parents=True, exist_ok=True)
    required = (
        str(base_context.get("title") or ""),
        *required_source_texts,
    )
    required = tuple(text for text in required if text.strip())
    manifest: list[dict[str, object]] = []
    failures: list[str] = []

    for variant in variants:
        if failures:
            break
        template_slug = variant["slug"]
        template = _ENVIRONMENT.get_template(variant["template"])
        template_dir = output_dir / template_slug
        template_dir.mkdir(parents=True, exist_ok=True)
        for spec in build_notice_variation_specs(
            template_slug,
            count=per_template,
            base_seed=base_seed,
            start_offset=variation_offset,
        ):
            effective_spec = _adapt_columns_to_content(spec, base_context)
            html = template.render(
                **base_context,
                variant_class=(
                    f"{variant['variant_class']} {effective_spec.css_class}"
                ),
                variation_css=_variation_css(effective_spec),
            )
            html_path = template_dir / f"{spec.slug}.html"
            pdf_path = template_dir / f"{spec.slug}.pdf"
            html_path.write_text(html, encoding="utf-8")
            HTML(
                string=html,
                base_url=str(TEMPLATE_DIR),
                url_fetcher=_RESTRICTED_URL_FETCHER,
            ).write_pdf(pdf_path)

            page_count, pdf_text = _inspect_pdf(
                pdf_path,
                exclude_left_rail=(
                    variant["variant_class"] == "notice-rail"
                ),
            )
            missing = _missing_texts(required, pdf_text)
            status = "ok" if page_count <= max_pages and not missing else "rejected"
            if status == "rejected":
                reasons = []
                if page_count > max_pages:
                    reasons.append(
                        f"maximum {max_pages} pages, got {page_count}"
                    )
                if missing:
                    reasons.append(_missing_summary(missing))
                failures.append(
                    f"{template_slug}/{spec.slug}: " + "; ".join(reasons)
                )

            agency_name = str(base_context.get("agency_name") or "")
            manifest.append(
                {
                    "renderer_family": "notice",
                    "template_slug": template_slug,
                    "template": variant["template"],
                    "variation_slug": spec.slug,
                    "status": status,
                    "max_pages": max_pages,
                    "actual_pages": page_count,
                    "source_text_present": not missing,
                    "parameters": effective_spec.to_dict(),
                    "identity": {
                        "identity_source": "input" if agency_name else "none",
                        "agency_name": agency_name or None,
                    },
                    "approval": base_context.get("approval_manifest", []),
                    "input": dict(input_metadata) if input_metadata else None,
                    "html": str(html_path),
                    "pdf": str(pdf_path),
                }
            )
            if status == "rejected":
                break

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        raise RuntimeError(
            "Notice render validation failed:\n"
            + "\n".join(f"- {failure}" for failure in failures)
        )
    return manifest
