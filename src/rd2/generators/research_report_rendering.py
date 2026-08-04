"""Jinja2 + WeasyPrint 연구보고서 전용 렌더러.

연구보고서는 공문과 달리 표지 뒤에 여러 페이지의 본문이 이어질 수 있다.
입력 block 순서를 바꾸거나 의미를 추측한 제목을 추가하지 않고, 같은 내용을
세 가지 보고서 골격과 재현 가능한 레이아웃 변주로 출력한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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

RESEARCH_REPORT_TEMPLATE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "slug": "research_01_classic_flow",
        "template": "research_report/base.html",
        "variant_class": "research-classic",
    },
    {
        "slug": "research_02_modular_policy",
        "template": "research_report/base.html",
        "variant_class": "research-modular",
    },
    {
        "slug": "research_03_academic_flow",
        "template": "research_report/base.html",
        "variant_class": "research-academic",
    },
)

_LAYOUT_CYCLES = {
    "research_01_classic_flow": ((2, 1), (1, 2), (3, 1)),
    "research_02_modular_policy": ((3, 2), (2, 1), (1, 2)),
    "research_03_academic_flow": ((1, 1), (2, 1), (1, 2)),
}
_DENSITIES = ("balanced", "compact", "airy")
_MAX_VARIATIONS_PER_TEMPLATE = 10
_MAX_RESEARCH_TITLE_CHARACTERS = 300
_MAX_RESEARCH_BLOCKS = 160
_MAX_RESEARCH_TEXT_CHARACTERS_PER_PAGE = 4_000
_MAX_RESEARCH_LIST_ITEMS = 600
_MAX_RESEARCH_TABLE_ROWS = 500
_MAX_RESEARCH_TABLE_CELLS = 2_500
_ALLOWED_ASSET_ROOTS = (
    TEMPLATE_DIR.resolve(),
    (TEMPLATE_DIR.parent / "assets").resolve(),
)
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(("html", "xml")),
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class ResearchVariationSpec:
    """연구보고서 한 변주를 재현하는 레이아웃 설정."""

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


def build_research_variation_specs(
    template_slug: str,
    *,
    count: int = 1,
    base_seed: int = 20260729,
    start_offset: int = 0,
) -> list[ResearchVariationSpec]:
    """템플릿별 구조 변주를 seed 기반으로 결정한다."""

    if template_slug not in _LAYOUT_CYCLES:
        raise ValueError(f"Unknown research report template slug: {template_slug}")
    if count < 1:
        raise ValueError("count must be at least 1")
    if count > _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "count must be at most "
            f"{_MAX_VARIATIONS_PER_TEMPLATE} per template"
        )
    if start_offset < 0:
        raise ValueError("start_offset must be at least 0")
    if start_offset + count > _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "start_offset + count must be at most "
            f"{_MAX_VARIATIONS_PER_TEMPLATE} per template"
        )

    template_number = int(template_slug.split("_", 2)[1])
    layouts = _LAYOUT_CYCLES[template_slug]
    specs: list[ResearchVariationSpec] = []
    for offset in range(start_offset, start_offset + count):
        density = _DENSITIES[offset % len(_DENSITIES)]
        key_value_columns, list_columns = layouts[offset % len(layouts)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
        if density == "compact":
            font_range = (0.92, 0.96)
            line_range = (1.55, 1.64)
            margin_range = (17.5, 19.0)
            gap_range = (4.5, 5.5)
            table_range = (1.6, 2.0)
        elif density == "airy":
            font_range = (1.01, 1.05)
            line_range = (1.78, 1.88)
            margin_range = (21.0, 23.0)
            gap_range = (7.5, 8.5)
            table_range = (2.6, 3.0)
        else:
            font_range = (0.98, 1.01)
            line_range = (1.68, 1.76)
            margin_range = (20.0, 21.5)
            gap_range = (6.5, 7.2)
            table_range = (2.2, 2.6)
        specs.append(
            ResearchVariationSpec(
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


def _variation_css(spec: ResearchVariationSpec) -> str:
    page_background = (
        "#fbfaf4"
        if spec.template_slug == "research_02_modular_policy"
        else "#ffffff"
    )
    return f"""
@page research-content {{
  background: {page_background};
  margin-left: {spec.horizontal_margin_mm}mm;
  margin-right: {spec.horizontal_margin_mm}mm;
}}
@page research-wide {{
  background: {page_background};
}}
.research-document {{
  --report-font-scale: {spec.font_scale};
  --report-line-height: {spec.line_height};
  --report-block-gap: {spec.block_gap_mm}mm;
  --report-table-padding: {spec.table_padding_mm}mm;
}}
"""


def _selected_variants(
    template_slugs: Collection[str] | None,
) -> tuple[dict[str, str], ...]:
    if template_slugs is None:
        return RESEARCH_REPORT_TEMPLATE_VARIANTS

    requested = frozenset(template_slugs)
    known = {
        variant["slug"] for variant in RESEARCH_REPORT_TEMPLATE_VARIANTS
    }
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(
            "Unknown research report template slug(s): " + ", ".join(unknown)
        )
    return tuple(
        variant
        for variant in RESEARCH_REPORT_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


def _inspect_pdf(pdf_path: Path) -> tuple[int, str, str]:
    with fitz.open(pdf_path) as document:
        page_texts: list[str] = []
        first_page_text = ""
        for page_index, page in enumerate(document):
            # @page 하단의 쪽 번호는 긴 문단이 페이지를 넘을 때 원문
            # 사이에 삽입된다. 인쇄 가능 영역 아래쪽의 블록을 제외한 뒤
            # 페이지 본문끼리 이어야 원문 보존 여부를 정확히 판정할 수 있다.
            footer_start = page.rect.height * 0.94
            blocks = page.get_text("blocks", sort=True)
            page_text = (
                " ".join(
                    str(block[4])
                    for block in blocks
                    if float(block[1]) < footer_start
                )
            )
            page_texts.append(page_text)
            if page_index == 0:
                first_page_text = page_text
        text = " ".join(page_texts)
        page_count = document.page_count
    return (
        page_count,
        "".join(text.split()),
        "".join(first_page_text.split()),
    )


def _normalized_required_text(value: object) -> str:
    return "".join(str(value).split())


class _RestrictedURLFetcher(URLFetcher):
    """번들 자산과 data URI만 허용하는 WeasyPrint fetcher."""

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


def _validate_render_budget(
    base_context: Mapping[str, Any],
    *,
    max_pages: int,
) -> None:
    """WeasyPrint 실행 전에 연구보고서 입력 복잡도를 제한한다."""

    title = str(base_context.get("title") or "")
    if len(title) > _MAX_RESEARCH_TITLE_CHARACTERS:
        raise ValueError(
            "research report title exceeds "
            f"{_MAX_RESEARCH_TITLE_CHARACTERS} characters"
        )

    raw_blocks = base_context.get("blocks")
    blocks = list(raw_blocks) if isinstance(raw_blocks, Sequence) else []
    if len(blocks) > _MAX_RESEARCH_BLOCKS:
        raise ValueError(
            f"research report supports at most {_MAX_RESEARCH_BLOCKS} blocks"
        )

    character_count = len(title) + len(
        str(base_context.get("agency_name") or "")
    )
    list_item_count = 0
    table_row_count = 0
    table_cell_count = 0

    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("kind")
        character_count += len(str(block.get("block_id") or ""))
        if kind == "paragraph":
            character_count += len(str(block.get("text") or ""))
        elif kind == "key_value":
            for entry in block.get("entries") or ():
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
            table_row_count += len(rows)
            table_cell_count += len(columns) + sum(len(row) for row in rows)
            character_count += sum(len(str(column)) for column in columns)
            character_count += sum(
                len(str(cell))
                for row in rows
                for cell in row
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

    character_limit = max_pages * _MAX_RESEARCH_TEXT_CHARACTERS_PER_PAGE
    limits = (
        (
            list_item_count,
            _MAX_RESEARCH_LIST_ITEMS,
            "list items",
        ),
        (
            table_row_count,
            _MAX_RESEARCH_TABLE_ROWS,
            "table rows",
        ),
        (
            table_cell_count,
            _MAX_RESEARCH_TABLE_CELLS,
            "table cells",
        ),
        (
            character_count,
            character_limit,
            "characters",
        ),
    )
    for actual, maximum, label in limits:
        if actual > maximum:
            raise ValueError(
                f"research report exceeds render budget: "
                f"{actual} {label}, maximum {maximum}"
            )


def _missing_texts(
    required_texts: Sequence[str],
    normalized_pdf_text: str,
) -> list[str]:
    required_counts: dict[str, int] = {}
    original_by_normalized: dict[str, str] = {}
    for value in required_texts:
        normalized = _normalized_required_text(value)
        if not normalized:
            continue
        required_counts[normalized] = required_counts.get(normalized, 0) + 1
        original_by_normalized.setdefault(normalized, value)
    return [
        original_by_normalized[normalized]
        for normalized, required_count in required_counts.items()
        if normalized_pdf_text.count(normalized) < required_count
    ]


def _missing_summary(label: str, values: Sequence[str]) -> str:
    samples = ", ".join(
        f"sha256={sha256(value.encode('utf-8')).hexdigest()[:12]}"
        f"/length={len(value)}"
        for value in values[:3]
    )
    return f"missing {label}: count={len(values)}; {samples}"


def render_research_report_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 1,
    base_seed: int = 20260729,
    variation_offset: int = 0,
    template_slugs: Collection[str] | None = None,
    required_source_texts: Sequence[str] = (),
    max_pages: int = 10,
    input_metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, object]]:
    """연구보고서 변주를 렌더링하고 원문 보존 및 페이지 수를 검증한다."""

    if per_template < 1:
        raise ValueError("per_template must be at least 1")
    if per_template > _MAX_VARIATIONS_PER_TEMPLATE:
        raise ValueError(
            "per_template must be at most "
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
        raise ValueError("At least one research report template must be selected")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    failures: list[str] = []
    source_texts = tuple(
        text
        for text in required_source_texts
        if isinstance(text, str) and text.strip()
    )

    for variant in variants:
        template_slug = variant["slug"]
        template = _ENVIRONMENT.get_template(variant["template"])
        template_dir = output_dir / template_slug
        template_dir.mkdir(parents=True, exist_ok=True)
        for spec in build_research_variation_specs(
            template_slug,
            count=per_template,
            base_seed=base_seed,
            start_offset=variation_offset,
        ):
            context = {
                **base_context,
                "variant_class": (
                    f"{variant['variant_class']} {spec.css_class}"
                ),
                "variation_css": _variation_css(spec),
            }
            html = template.render(**context)
            html_path = template_dir / f"{spec.slug}.html"
            pdf_path = template_dir / f"{spec.slug}.pdf"
            html_path.write_text(html, encoding="utf-8")
            HTML(
                string=html,
                base_url=str(TEMPLATE_DIR),
                url_fetcher=_RESTRICTED_URL_FETCHER,
            ).write_pdf(pdf_path)

            actual_pages, pdf_text, first_page_text = _inspect_pdf(pdf_path)
            required_template_texts = (
                str(base_context.get("title") or ""),
                str(base_context.get("agency_name") or ""),
            )
            missing_template_texts = _missing_texts(
                required_template_texts,
                pdf_text,
            )
            missing_source_texts = _missing_texts(source_texts, pdf_text)
            cover_title_present = (
                _normalized_required_text(required_template_texts[0])
                in first_page_text
            )
            page_count_valid = actual_pages <= max_pages
            status = (
                "ok"
                if (
                    page_count_valid
                    and cover_title_present
                    and not missing_template_texts
                    and not missing_source_texts
                )
                else "rejected"
            )

            failure_reasons: list[str] = []
            if not page_count_valid:
                failure_reasons.append(
                    f"maximum {max_pages} pages, got {actual_pages}"
                )
            if not cover_title_present:
                failure_reasons.append("cover title is incomplete")
            if missing_template_texts:
                failure_reasons.append(
                    _missing_summary(
                        "template text",
                        missing_template_texts,
                    )
                )
            if missing_source_texts:
                failure_reasons.append(
                    _missing_summary("source text", missing_source_texts)
                )
            if failure_reasons:
                failures.append(
                    f"{template_slug}/{spec.slug}: "
                    + "; ".join(failure_reasons)
                )

            agency_name = str(base_context.get("agency_name") or "")
            manifest.append(
                {
                    "renderer_family": "research_report",
                    "template_slug": template_slug,
                    "template": variant["template"],
                    "variation_slug": spec.slug,
                    "status": status,
                    "max_pages": max_pages,
                    "actual_pages": actual_pages,
                    "required_text_present": not missing_template_texts,
                    "source_text_present": not missing_source_texts,
                    "parameters": spec.to_dict(),
                    "identity": {
                        "identity_source": (
                            "input" if agency_name else "none"
                        ),
                        "agency_name": agency_name or None,
                    },
                    "approval": base_context.get("approval_manifest", []),
                    "input": (
                        dict(input_metadata)
                        if input_metadata is not None
                        else None
                    ),
                    "html": str(html_path),
                    "pdf": str(pdf_path),
                }
            )

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        details = "\n".join(f"- {failure}" for failure in failures)
        raise RuntimeError(f"Research report render validation failed:\n{details}")
    return manifest
