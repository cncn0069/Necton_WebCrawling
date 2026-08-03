"""Jinja2 + WeasyPrint 보도자료 전용 렌더러.

입력 block의 상대 순서와 텍스트를 바꾸지 않고, 정부 보도자료에서 반복되는
헤더·제목·핵심요약·본문·담당정보 구조를 세 가지 골격과 재현 가능한 변주로
출력한다. 기관명, 시점, 담당자 정보는 입력에 있을 때만 표시한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from typing import Any, Collection, Mapping, Sequence
from urllib.parse import unquote, urlparse

import fitz
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from weasyprint import HTML
from weasyprint.urls import URLFetcher

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

PRESS_RELEASE_MAX_PAGES = 10
PRESS_RELEASE_WIDE_TABLE_MIN_COLUMNS = 8
PRESS_RELEASE_TEMPLATE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "slug": "press_01_government_standard",
        "template": "press_release/base.html",
        "variant_class": "press-classic",
        "structures": (
            (2, 1, 1, 2),
            (1, 2, 1, 1),
            (2, 1, 1, 2),
        ),
    },
    {
        "slug": "press_02_briefing_focus",
        "template": "press_release/base.html",
        "variant_class": "press-brief",
        "structures": (
            (1, 1, 1, 2),
            (2, 2, 1, 1),
            (1, 1, 1, 2),
        ),
    },
    {
        "slug": "press_03_joint_modular",
        "template": "press_release/base.html",
        "variant_class": "press-joint",
        "structures": (
            (2, 1, 2, 2),
            (2, 2, 2, 1),
            (1, 1, 1, 2),
        ),
    },
)
_VARIANTS_BY_SLUG = {
    variant["slug"]: variant for variant in PRESS_RELEASE_TEMPLATE_VARIANTS
}
_DENSITIES = ("balanced", "compact", "airy")
_MAX_VARIATIONS_PER_TEMPLATE = 10
_MAX_TITLE_CHARACTERS = 300
_MAX_BLOCKS = 160
_MAX_CHARACTERS = 40_000
_MAX_LIST_ITEMS = 600
_MAX_TABLE_ROWS = 500
_MAX_TABLE_CELLS = 2_500
_MAX_KEY_VALUE_ENTRIES_PER_BLOCK = 200
_MAX_ADMINISTRATIVE_EVENTS = 100
_ESTIMATED_CHARACTERS_PER_PAGE = 2_800
_ESTIMATED_KEY_VALUE_ENTRIES_PER_PAGE = 28
_ESTIMATED_ADMINISTRATIVE_EVENTS_PER_PAGE = 24
_ESTIMATED_TABLE_ROWS_PER_PAGE = 28
_ESTIMATED_LIST_ITEMS_PER_PAGE = 45
_ESTIMATED_BLOCKS_PER_PAGE = 40
_MAX_PAGE_ESTIMATE_MULTIPLIER = 1.25
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
class PressReleaseVariationSpec:
    """보도자료 한 변주를 재현하는 구조·밀도 설정."""

    template_slug: str
    index: int
    seed: int
    density: str
    meta_columns: int
    summary_columns: int
    body_columns: int
    contact_columns: int
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
        return " ".join(
            (
                f"density-{self.density}",
                f"meta-columns-{self.meta_columns}",
                f"summary-columns-{self.summary_columns}",
                f"body-columns-{self.body_columns}",
                f"contact-columns-{self.contact_columns}",
            )
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_press_release_variation_specs(
    template_slug: str,
    *,
    count: int = 1,
    base_seed: int = 20260730,
    start_offset: int = 0,
) -> list[PressReleaseVariationSpec]:
    """템플릿별 구조 변주를 seed 기반으로 결정한다."""

    if template_slug not in _VARIANTS_BY_SLUG:
        raise ValueError(f"Unknown press release template slug: {template_slug}")
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
    structures = _VARIANTS_BY_SLUG[template_slug]["structures"]
    specs: list[PressReleaseVariationSpec] = []
    for offset in range(start_offset, start_offset + count):
        density = _DENSITIES[offset % len(_DENSITIES)]
        (
            meta_columns,
            summary_columns,
            body_columns,
            contact_columns,
        ) = structures[offset % len(structures)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
        if density == "compact":
            font_range = (0.92, 0.96)
            line_range = (1.5, 1.6)
            margin_range = (16.5, 18.5)
            gap_range = (3.5, 4.7)
            table_range = (1.5, 1.9)
        elif density == "airy":
            font_range = (1.01, 1.05)
            line_range = (1.72, 1.82)
            margin_range = (20.5, 22.5)
            gap_range = (6.3, 7.5)
            table_range = (2.5, 2.9)
        else:
            font_range = (0.98, 1.01)
            line_range = (1.62, 1.7)
            margin_range = (18.5, 20.5)
            gap_range = (5.0, 6.0)
            table_range = (2.0, 2.4)
        specs.append(
            PressReleaseVariationSpec(
                template_slug=template_slug,
                index=offset + 1,
                seed=seed,
                density=density,
                meta_columns=meta_columns,
                summary_columns=summary_columns,
                body_columns=body_columns,
                contact_columns=contact_columns,
                font_scale=round(rng.uniform(*font_range), 3),
                line_height=round(rng.uniform(*line_range), 3),
                horizontal_margin_mm=round(rng.uniform(*margin_range), 2),
                block_gap_mm=round(rng.uniform(*gap_range), 2),
                table_padding_mm=round(rng.uniform(*table_range), 2),
            )
        )
    return specs


def _variation_css(spec: PressReleaseVariationSpec) -> str:
    return f"""
@page press-content {{
  margin-left: {spec.horizontal_margin_mm}mm;
  margin-right: {spec.horizontal_margin_mm}mm;
}}
.press-document {{
  --press-font-scale: {spec.font_scale};
  --press-line-height: {spec.line_height};
  --press-block-gap: {spec.block_gap_mm}mm;
  --press-table-padding: {spec.table_padding_mm}mm;
}}
"""


def _selected_variants(
    template_slugs: Collection[str] | None,
) -> tuple[dict[str, Any], ...]:
    if template_slugs is None:
        return PRESS_RELEASE_TEMPLATE_VARIANTS

    requested = frozenset(template_slugs)
    known = {
        variant["slug"] for variant in PRESS_RELEASE_TEMPLATE_VARIANTS
    }
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(
            "Unknown press release template slug(s): " + ", ".join(unknown)
        )
    return tuple(
        variant
        for variant in PRESS_RELEASE_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


def _normalized_required_text(value: object) -> str:
    return "".join(str(value).split())


def _inspect_pdf(pdf_path: Path) -> tuple[int, str, str]:
    with fitz.open(pdf_path) as document:
        page_texts: list[str] = []
        first_page_text = ""
        for page_index, page in enumerate(document):
            footer_start = page.rect.height * 0.94
            # WeasyPrint의 다단 레이아웃은 좌표순 정렬 시 서로 다른 열의
            # 문장이 섞인다. PDF 객체가 기록한 DOM 순서를 사용해야 원문
            # 단위 검증이 실제 렌더 순서와 일치한다.
            blocks = page.get_text("blocks", sort=False)
            page_text = " ".join(
                str(block[4])
                for block in blocks
                if float(block[1]) < footer_start
            )
            page_texts.append(page_text)
            if page_index == 0:
                first_page_text = page_text
        page_count = document.page_count
    return (
        page_count,
        "".join(" ".join(page_texts).split()),
        "".join(first_page_text.split()),
    )


class _RestrictedURLFetcher(URLFetcher):
    """번들 템플릿 자산과 data URI만 허용한다."""

    def fetch(self, url: str, headers: Mapping[str, str] | None = None) -> Any:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            resource_path = Path(unquote(parsed.path)).resolve()
            if not any(
                resource_path.is_relative_to(root)
                for root in _ALLOWED_ASSET_ROOTS
            ):
                raise ValueError(
                    "Resource path is outside the renderer asset roots"
                )
        return super().fetch(url, headers=headers)


PRESS_RELEASE_URL_FETCHER = _RestrictedURLFetcher(
    allowed_protocols=("data", "file"),
    allow_redirects=False,
    fail_on_errors=True,
)


def _flatten_context_blocks(
    base_context: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    blocks: list[Mapping[str, Any]] = []
    header_meta = base_context.get("header_meta")
    if isinstance(header_meta, Mapping):
        blocks.append(header_meta)
    for item in base_context.get("render_items") or ():
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") == "paragraph_group":
            blocks.extend(
                block
                for block in item.get("blocks") or ()
                if isinstance(block, Mapping)
            )
        else:
            blocks.append(item)
    return blocks


def _validate_render_budget(
    base_context: Mapping[str, Any],
    *,
    max_pages: int,
) -> None:
    """WeasyPrint 실행 전에 보도자료 입력 복잡도를 제한한다."""

    title = str(base_context.get("title") or "")
    if len(title) > _MAX_TITLE_CHARACTERS:
        raise ValueError(
            f"press release title exceeds {_MAX_TITLE_CHARACTERS} characters"
        )

    blocks = _flatten_context_blocks(base_context)
    if len(blocks) > _MAX_BLOCKS:
        raise ValueError(f"press release supports at most {_MAX_BLOCKS} blocks")

    character_count = len(title) + len(
        str(base_context.get("agency_name") or "")
    )
    list_item_count = 0
    key_value_entry_count = 0
    administrative_event_count = 0
    table_row_count = 0
    table_cell_count = 0
    for block in blocks:
        character_count += len(str(block.get("block_id") or ""))
        kind = block.get("kind")
        if kind == "paragraph":
            character_count += len(str(block.get("text") or ""))
        elif kind == "key_value":
            entries = block.get("entries") or ()
            if len(entries) > _MAX_KEY_VALUE_ENTRIES_PER_BLOCK:
                raise ValueError(
                    "press release key_value block supports at most "
                    f"{_MAX_KEY_VALUE_ENTRIES_PER_BLOCK} entries"
                )
            key_value_entry_count += len(entries)
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
    administrative_events = base_context.get("administrative_events") or ()
    administrative_event_count = len(administrative_events)
    if administrative_event_count > _MAX_ADMINISTRATIVE_EVENTS:
        raise ValueError(
            "press release supports at most "
            f"{_MAX_ADMINISTRATIVE_EVENTS} administrative events"
        )
    for event in administrative_events:
        if isinstance(event, Mapping):
            character_count += len(str(event.get("date") or ""))
            character_count += len(str(event.get("text") or ""))
    for signer in base_context.get("signers") or ():
        if isinstance(signer, Mapping):
            character_count += sum(
                len(str(signer.get(key) or ""))
                for key in ("role", "name", "date", "status", "stamp_alt")
            )

    limits = (
        (list_item_count, _MAX_LIST_ITEMS, "list items"),
        (table_row_count, _MAX_TABLE_ROWS, "table rows"),
        (table_cell_count, _MAX_TABLE_CELLS, "table cells"),
        (character_count, _MAX_CHARACTERS, "characters"),
    )
    for actual, maximum, label in limits:
        if actual > maximum:
            raise ValueError(
                "press release exceeds render budget: "
                f"{actual} {label}, maximum {maximum}"
            )

    estimated_pages = (
        character_count / _ESTIMATED_CHARACTERS_PER_PAGE
        + key_value_entry_count / _ESTIMATED_KEY_VALUE_ENTRIES_PER_PAGE
        + (
            administrative_event_count
            / _ESTIMATED_ADMINISTRATIVE_EVENTS_PER_PAGE
        )
        + table_row_count / _ESTIMATED_TABLE_ROWS_PER_PAGE
        + list_item_count / _ESTIMATED_LIST_ITEMS_PER_PAGE
        + len(blocks) / _ESTIMATED_BLOCKS_PER_PAGE
    )
    if estimated_pages > max_pages * _MAX_PAGE_ESTIMATE_MULTIPLIER:
        raise ValueError(
            "press release exceeds pre-render page-cost estimate: "
            f"{estimated_pages:.1f} estimated pages for maximum {max_pages}"
        )


def _missing_texts(
    required_texts: Sequence[str],
    normalized_pdf_text: str,
) -> list[str]:
    """중복 개수와 부분문자열 겹침까지 고려해 누락 원문을 찾는다."""

    missing: list[str] = []
    occupied = bytearray(len(normalized_pdf_text))
    normalized_values = [
        (index, value, _normalized_required_text(value))
        for index, value in enumerate(required_texts)
    ]
    normalized_values.sort(key=lambda item: (-len(item[2]), item[0]))
    for _, value, normalized in normalized_values:
        if not normalized:
            continue
        start = 0
        matched = False
        while True:
            position = normalized_pdf_text.find(normalized, start)
            if position < 0:
                break
            end = position + len(normalized)
            if not any(occupied[position:end]):
                occupied[position:end] = b"\x01" * len(normalized)
                matched = True
                break
            start = position + 1
        if not matched:
            missing.append(value)
    return missing


def _missing_summary(label: str, values: Sequence[str]) -> str:
    samples = ", ".join(
        f"sha256={sha256(value.encode('utf-8')).hexdigest()[:12]}"
        f"/length={len(value)}"
        for value in values[:3]
    )
    return f"missing {label}: count={len(values)}; {samples}"


def _publish_output_directory(
    candidate_dir: Path,
    output_dir: Path,
    transaction_dir: Path,
) -> None:
    """완성된 한 세대만 출력 경로에 보이도록 디렉터리를 교체한다."""

    previous_dir = transaction_dir / "previous"
    if output_dir.exists() or output_dir.is_symlink():
        output_dir.replace(previous_dir)
    try:
        candidate_dir.replace(output_dir)
    except Exception:
        if previous_dir.exists() or previous_dir.is_symlink():
            previous_dir.replace(output_dir)
        raise


def _rejected_manifest_entry(
    entry: Mapping[str, object],
    input_metadata: Mapping[str, Any] | None,
) -> dict[str, object]:
    """거부 결과에서는 원문 기관·결재·임의 메타데이터를 제거한다."""

    safe_input_keys = (
        "contract_version",
        "document_type",
        "renderer_family",
        "generation_route",
        "content_sha256",
        "rendered_from_failed_input",
    )
    safe_input = (
        {
            key: input_metadata.get(key)
            for key in safe_input_keys
            if key in input_metadata
        }
        if input_metadata is not None
        else None
    )
    return {
        key: entry[key]
        for key in (
            "renderer_family",
            "template_slug",
            "template",
            "variation_slug",
            "max_pages",
            "actual_pages",
            "required_text_present",
            "source_text_present",
            "parameters",
        )
    } | {
        "status": "rejected",
        "validation_status": entry["status"],
        "artifact_published": False,
        "input": safe_input,
        "html": None,
        "pdf": None,
    }


def render_press_release_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 1,
    base_seed: int = 20260730,
    variation_offset: int = 0,
    template_slugs: Collection[str] | None = None,
    required_source_texts: Sequence[str] = (),
    max_pages: int = PRESS_RELEASE_MAX_PAGES,
    input_metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, object]]:
    """보도자료 변주를 렌더링하고 원문 보존 및 페이지 수를 검증한다."""

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
        raise ValueError("At least one press release template must be selected")

    source_texts = tuple(
        text
        for text in required_source_texts
        if isinstance(text, str) and text.strip()
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir.parent / f".{output_dir.name}.press.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            return _render_and_publish_press_release(
                base_context,
                output_dir,
                variants=variants,
                per_template=per_template,
                base_seed=base_seed,
                variation_offset=variation_offset,
                source_texts=source_texts,
                max_pages=max_pages,
                input_metadata=input_metadata,
            )
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _render_and_publish_press_release(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    variants: Sequence[Mapping[str, Any]],
    per_template: int,
    base_seed: int,
    variation_offset: int,
    source_texts: Sequence[str],
    max_pages: int,
    input_metadata: Mapping[str, Any] | None,
) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    failures: list[str] = []
    publish_pairs: list[tuple[Path, Path]] = []

    with TemporaryDirectory(
        prefix=f".{output_dir.name}-press-",
        dir=output_dir.parent,
    ) as transaction_name:
        transaction_dir = Path(transaction_name)
        work_dir = transaction_dir / "work"
        candidate_dir = transaction_dir / "candidate"
        candidate_dir.mkdir()
        for variant in variants:
            if failures:
                break
            template_slug = str(variant["slug"])
            template = _ENVIRONMENT.get_template(str(variant["template"]))
            staged_template_dir = work_dir / template_slug
            staged_template_dir.mkdir(parents=True, exist_ok=True)
            candidate_template_dir = candidate_dir / template_slug
            for spec in build_press_release_variation_specs(
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
                staged_html_path = staged_template_dir / f"{spec.slug}.html"
                staged_pdf_path = staged_template_dir / f"{spec.slug}.pdf"
                final_html_path = output_dir / template_slug / f"{spec.slug}.html"
                final_pdf_path = output_dir / template_slug / f"{spec.slug}.pdf"
                candidate_html_path = (
                    candidate_template_dir / f"{spec.slug}.html"
                )
                candidate_pdf_path = (
                    candidate_template_dir / f"{spec.slug}.pdf"
                )
                staged_html_path.write_text(html, encoding="utf-8")
                HTML(
                    string=html,
                    base_url=str(TEMPLATE_DIR),
                    url_fetcher=PRESS_RELEASE_URL_FETCHER,
                ).write_pdf(staged_pdf_path)

                actual_pages, pdf_text, first_page_text = _inspect_pdf(
                    staged_pdf_path
                )
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
                    failure_reasons.append("first-page title is incomplete")
                if missing_template_texts:
                    failure_reasons.append(
                        _missing_summary(
                            "template text",
                            missing_template_texts,
                        ),
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
                        "renderer_family": "press_release",
                        "template_slug": template_slug,
                        "template": variant["template"],
                        "variation_slug": spec.slug,
                        "status": status,
                        "artifact_published": False,
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
                        "approval": base_context.get(
                            "approval_manifest",
                            [],
                        ),
                        "input": (
                            dict(input_metadata)
                            if input_metadata is not None
                            else None
                        ),
                        "html": None,
                        "pdf": None,
                    }
                )
                publish_pairs.extend(
                    (
                        (staged_html_path, candidate_html_path),
                        (staged_pdf_path, candidate_pdf_path),
                    )
                )
                if failure_reasons:
                    break

        if failures:
            manifest = [
                _rejected_manifest_entry(entry, input_metadata)
                for entry in manifest
            ]
        else:
            for staged_path, candidate_path in publish_pairs:
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.replace(candidate_path)
            for entry in manifest:
                variation_slug = str(entry["variation_slug"])
                template_dir = output_dir / str(entry["template_slug"])
                entry["artifact_published"] = True
                entry["html"] = str(template_dir / f"{variation_slug}.html")
                entry["pdf"] = str(template_dir / f"{variation_slug}.pdf")

        (candidate_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _publish_output_directory(candidate_dir, output_dir, transaction_dir)

        if failures:
            details = "\n".join(f"- {failure}" for failure in failures)
            raise RuntimeError(
                f"Press release render validation failed:\n{details}"
            )
        return manifest
