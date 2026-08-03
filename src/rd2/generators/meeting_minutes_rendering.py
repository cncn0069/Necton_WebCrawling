"""단색 공공기관형 회의록 Jinja2 + WeasyPrint 렌더러."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
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
MEETING_MINUTES_MAX_PAGES = 10
_DEFAULT_VARIATION_SEED = 20260731
_MAX_VARIATIONS_PER_TEMPLATE = 10
_MAX_TITLE_CHARACTERS = 300
_MAX_TEXT_CHARACTERS_PER_PAGE = 4_000
_MAX_BLOCKS_PER_PAGE = 24
_MAX_COLLECTION_ITEMS_PER_PAGE = 60
_MAX_TABLE_CELLS_PER_PAGE = 250
_MAX_TABLE_COLUMNS = 20

MEETING_MINUTES_TEMPLATE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "slug": "meeting_01_registry",
        "template": "meeting_minutes/base.html",
        "variant_class": "meeting-registry",
    },
    {
        "slug": "meeting_02_sequence",
        "template": "meeting_minutes/base.html",
        "variant_class": "meeting-sequence",
    },
    {
        "slug": "meeting_03_columns",
        "template": "meeting_minutes/base.html",
        "variant_class": "meeting-columns",
    },
    {
        "slug": "meeting_04_docket",
        "template": "meeting_minutes/base.html",
        "variant_class": "meeting-docket",
    },
)
_VARIANTS_BY_SLUG = {
    variant["slug"]: variant
    for variant in MEETING_MINUTES_TEMPLATE_VARIANTS
}
_DENSITIES = ("balanced", "compact", "airy")
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
class MeetingMinutesVariationSpec:
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


def build_meeting_minutes_variation_specs(
    template_slug: str,
    *,
    count: int = 1,
    base_seed: int = _DEFAULT_VARIATION_SEED,
    start_offset: int = 0,
) -> list[MeetingMinutesVariationSpec]:
    """같은 입력에서 재현 가능한 밀도·열 구조 변주를 만든다."""

    if template_slug not in _VARIANTS_BY_SLUG:
        raise ValueError(
            f"Unknown meeting minutes template: {template_slug}"
        )
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

    layout_cycles = {
        "meeting_01_registry": ((2, 1), (4, 2), (2, 1)),
        "meeting_02_sequence": ((2, 2), (4, 1), (2, 2)),
        "meeting_03_columns": ((1, 1), (2, 2), (1, 2)),
        "meeting_04_docket": ((4, 1), (2, 2), (4, 1)),
    }
    ranges = {
        "compact": (
            (0.92, 0.96),
            (1.48, 1.58),
            (16.5, 18.0),
            (3.2, 4.1),
            (1.5, 1.9),
        ),
        "balanced": (
            (0.98, 1.01),
            (1.60, 1.70),
            (18.0, 20.0),
            (4.5, 5.6),
            (2.0, 2.4),
        ),
        "airy": (
            (1.01, 1.05),
            (1.74, 1.84),
            (20.0, 22.0),
            (5.8, 6.8),
            (2.5, 2.9),
        ),
    }
    template_number = int(template_slug.split("_", 2)[1])
    layouts = layout_cycles[template_slug]
    specs: list[MeetingMinutesVariationSpec] = []
    for offset in range(start_offset, start_offset + count):
        density = _DENSITIES[offset % len(_DENSITIES)]
        seed = base_seed + template_number * 1000 + offset
        rng = random.Random(seed)
        (
            font_range,
            line_range,
            margin_range,
            gap_range,
            table_range,
        ) = ranges[density]
        key_value_columns, list_columns = layouts[offset % len(layouts)]
        specs.append(
            MeetingMinutesVariationSpec(
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


def _variation_css(spec: MeetingMinutesVariationSpec) -> str:
    page_background = (
        "#f4f5f5"
        if spec.template_slug == "meeting_03_columns"
        else "#ffffff"
    )
    return f"""
@page meeting-content {{
  background: {page_background};
  margin-left: {spec.horizontal_margin_mm}mm;
  margin-right: {spec.horizontal_margin_mm}mm;
}}
@page meeting-wide {{
  background: {page_background};
}}
.meeting-document {{
  --meeting-font-scale: {spec.font_scale};
  --meeting-line-height: {spec.line_height};
  --meeting-block-gap: {spec.block_gap_mm}mm;
  --meeting-table-padding: {spec.table_padding_mm}mm;
}}
"""


def _selected_variants(
    template_slugs: Collection[str] | None,
) -> tuple[dict[str, str], ...]:
    if template_slugs is None:
        return MEETING_MINUTES_TEMPLATE_VARIANTS
    requested = frozenset(template_slugs)
    unknown = sorted(requested - _VARIANTS_BY_SLUG.keys())
    if unknown:
        raise ValueError(
            "Unknown meeting minutes template(s): " + ", ".join(unknown)
        )
    return tuple(
        variant
        for variant in MEETING_MINUTES_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


class _RestrictedURLFetcher(URLFetcher):
    """번들 템플릿 자산과 data URI 외의 접근을 차단한다."""

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


_RESTRICTED_URL_FETCHER = _RestrictedURLFetcher(
    allowed_protocols=("data", "file"),
    allow_redirects=False,
    fail_on_errors=True,
)


def _normalized(value: object) -> str:
    return "".join(str(value).split())


def _inspect_pdf(pdf_path: Path) -> tuple[int, str, str]:
    """쪽 번호를 제외한 본문 텍스트와 첫 페이지 텍스트를 반환한다."""

    with fitz.open(pdf_path) as document:
        page_texts: list[str] = []
        first_page_text = ""
        for page_index, page in enumerate(document):
            footer_start = page.rect.height * 0.94
            page_text = " ".join(
                str(block[4])
                for block in page.get_text("blocks", sort=False)
                if float(block[1]) < footer_start
            )
            page_texts.append(page_text)
            if page_index == 0:
                first_page_text = page_text
        return (
            document.page_count,
            _normalized(" ".join(page_texts)),
            _normalized(first_page_text),
        )


@dataclass(frozen=True)
class _RequiredTexts:
    counts: dict[str, int]
    original_by_normalized: dict[str, str]


class _PatternMatcher:
    """여러 원문 조각의 출현 횟수를 PDF 전체 한 번의 순회로 센다."""

    def __init__(self, patterns: Collection[str]) -> None:
        self._transitions: list[dict[str, int]] = [{}]
        self._failures: list[int] = [0]
        self._terminal_states: dict[str, int] = {}
        for pattern in patterns:
            state = 0
            for character in pattern:
                next_state = self._transitions[state].get(character)
                if next_state is None:
                    next_state = len(self._transitions)
                    self._transitions[state][character] = next_state
                    self._transitions.append({})
                    self._failures.append(0)
                state = next_state
            self._terminal_states[pattern] = state

        queue: deque[int] = deque()
        for state in self._transitions[0].values():
            queue.append(state)
        self._propagation_order: list[int] = []
        while queue:
            state = queue.popleft()
            self._propagation_order.append(state)
            for character, next_state in self._transitions[state].items():
                queue.append(next_state)
                failure = self._failures[state]
                while (
                    failure
                    and character not in self._transitions[failure]
                ):
                    failure = self._failures[failure]
                self._failures[next_state] = self._transitions[
                    failure
                ].get(character, 0)

    def count(self, text: str) -> dict[str, int]:
        state_visits = [0] * len(self._transitions)
        state = 0
        for character in text:
            while state and character not in self._transitions[state]:
                state = self._failures[state]
            state = self._transitions[state].get(character, 0)
            state_visits[state] += 1

        for visited_state in reversed(self._propagation_order):
            state_visits[self._failures[visited_state]] += state_visits[
                visited_state
            ]
        return {
            pattern: state_visits[terminal_state]
            for pattern, terminal_state in self._terminal_states.items()
            if state_visits[terminal_state]
        }


def _prepare_required_texts(
    required_texts: Sequence[str],
) -> _RequiredTexts:
    """동일·포함 관계까지 실제 표시 횟수만큼 요구한다."""

    normalized_values: list[str] = []
    original_by_normalized: dict[str, str] = {}
    for value in required_texts:
        normalized = _normalized(value)
        if not normalized:
            continue
        normalized_values.append(normalized)
        original_by_normalized.setdefault(normalized, value)

    matcher = _PatternMatcher(original_by_normalized)
    required_counts = {
        pattern: 0
        for pattern in original_by_normalized
    }
    for normalized_value in normalized_values:
        for pattern, count in matcher.count(normalized_value).items():
            required_counts[pattern] += count
    return _RequiredTexts(required_counts, original_by_normalized)


def _missing_texts(
    required: _RequiredTexts,
    found_counts: Mapping[str, int],
) -> list[str]:
    return [
        required.original_by_normalized[normalized]
        for normalized, required_count in required.counts.items()
        if found_counts.get(normalized, 0) < required_count
    ]


def _missing_summary(label: str, values: Sequence[str]) -> str:
    samples = ", ".join(
        f"sha256={sha256(value.encode('utf-8')).hexdigest()[:12]}"
        f"/length={len(value)}"
        for value in values[:3]
    )
    return f"missing {label}: count={len(values)}; {samples}"


def _text_length(value: object) -> int:
    text = str(value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "meeting minutes contains text that cannot be encoded as UTF-8"
        ) from error
    return len(text)


def _validate_manifest_values(
    base_context: Mapping[str, Any],
    input_metadata: Mapping[str, Any] | None,
) -> None:
    """manifest에 도달하는 값을 JSON/UTF-8로 기록할 수 있는지 확인한다."""

    try:
        serialized = json.dumps(
            {
                "context": dict(base_context),
                "input": (
                    dict(input_metadata)
                    if input_metadata is not None
                    else None
                ),
            },
            ensure_ascii=False,
        )
        serialized.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "meeting minutes metadata cannot be encoded as UTF-8"
        ) from error
    except (TypeError, ValueError) as error:
        raise ValueError(
            "meeting minutes metadata must be JSON serializable"
        ) from error


def _validate_render_budget(
    base_context: Mapping[str, Any],
    *,
    max_pages: int,
) -> None:
    """명백히 과대한 입력은 WeasyPrint 실행 전에 거부한다."""

    title = str(base_context.get("title") or "")
    if len(title) > _MAX_TITLE_CHARACTERS:
        raise ValueError(
            f"meeting minutes title exceeds "
            f"{_MAX_TITLE_CHARACTERS} characters"
        )
    raw_blocks = base_context.get("blocks")
    if not isinstance(raw_blocks, Sequence) or isinstance(
        raw_blocks,
        (str, bytes),
    ):
        raise ValueError("blocks must be a sequence")

    blocks = list(raw_blocks)
    character_count = _text_length(title) + _text_length(
        base_context.get("agency_name") or ""
    )
    collection_items = 0
    table_cells = 0
    max_table_columns = 0

    for block in blocks:
        if not isinstance(block, Mapping):
            raise ValueError("each block must be a mapping")
        character_count += _text_length(block.get("block_id") or "")
        kind = block.get("kind")
        if kind == "paragraph":
            character_count += _text_length(block.get("text") or "")
        elif kind == "key_value":
            entries = block.get("entries") or ()
            collection_items += len(entries)
            for entry in entries:
                character_count += _text_length(entry.get("key") or "")
                character_count += _text_length(entry.get("value") or "")
        elif kind == "bullet_list":
            items = block.get("items") or ()
            collection_items += len(items)
            character_count += sum(_text_length(item) for item in items)
        elif kind == "table":
            columns = block.get("columns") or ()
            rows = block.get("rows") or ()
            max_table_columns = max(max_table_columns, len(columns))
            collection_items += len(rows)
            table_cells += len(columns) + sum(len(row) for row in rows)
            character_count += sum(_text_length(value) for value in columns)
            character_count += sum(
                _text_length(value)
                for row in rows
                for value in row
            )
        elif kind == "attachment_reference":
            collection_items += 1
            character_count += sum(
                _text_length(block.get(key) or "")
                for key in ("attachment_id", "label", "description")
            )

    signers = base_context.get("signers") or ()
    administrative_events = base_context.get("administrative_events") or ()
    collection_items += len(signers) + len(administrative_events)
    for signer in signers:
        if isinstance(signer, Mapping):
            character_count += sum(
                _text_length(signer.get(key) or "")
                for key in ("role", "name", "date")
            )
    for event in administrative_events:
        if isinstance(event, Mapping):
            character_count += sum(
                _text_length(event.get(key) or "")
                for key in ("type", "date", "text")
            )

    limits = (
        (len(blocks), max_pages * _MAX_BLOCKS_PER_PAGE, "blocks"),
        (
            character_count,
            max_pages * _MAX_TEXT_CHARACTERS_PER_PAGE,
            "characters",
        ),
        (
            collection_items,
            max_pages * _MAX_COLLECTION_ITEMS_PER_PAGE,
            "collection items",
        ),
        (
            table_cells,
            max_pages * _MAX_TABLE_CELLS_PER_PAGE,
            "table cells",
        ),
        (max_table_columns, _MAX_TABLE_COLUMNS, "table columns"),
    )
    violations = [
        f"{label}={actual}>{maximum}"
        for actual, maximum, label in limits
        if actual > maximum
    ]
    if violations:
        raise ValueError(
            "Meeting minutes input exceeds render budget: "
            + ", ".join(violations)
        )


def _publish_output_directory(
    candidate_dir: Path,
    output_dir: Path,
    transaction_dir: Path,
) -> None:
    previous_dir = transaction_dir / "previous"
    if output_dir.exists() or output_dir.is_symlink():
        output_dir.replace(previous_dir)
    try:
        candidate_dir.replace(output_dir)
    except Exception:
        if previous_dir.exists() or previous_dir.is_symlink():
            previous_dir.replace(output_dir)
        raise


def _publish_rejection_manifest(
    output_dir: Path,
    *,
    max_pages: int,
    reason: str,
) -> None:
    """사전 거부도 이전 PDF를 제거하고 manifest만 게시한다."""

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=f".{output_dir.name}-meeting-rejected-",
        dir=output_dir.parent,
    ) as transaction_name:
        transaction_dir = Path(transaction_name)
        candidate_dir = transaction_dir / "candidate"
        candidate_dir.mkdir()
        manifest = [
            {
                "renderer_family": "meeting_minutes",
                "template_slug": None,
                "variation_slug": None,
                "status": "rejected",
                "artifact_published": False,
                "max_pages": max_pages,
                "actual_pages": None,
                "source_text_present": None,
                "html": None,
                "pdf": None,
                "reason": reason,
            }
        ]
        (candidate_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _publish_output_directory(candidate_dir, output_dir, transaction_dir)


def render_meeting_minutes_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 1,
    base_seed: int = _DEFAULT_VARIATION_SEED,
    variation_offset: int = 0,
    template_slugs: Collection[str] | None = None,
    required_source_texts: Sequence[str] = (),
    max_pages: int = MEETING_MINUTES_MAX_PAGES,
    input_metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, object]]:
    """일반 GeneratedDocumentIR을 순서대로 회의록으로 렌더링한다."""

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
    variants = _selected_variants(template_slugs)
    if not variants:
        raise ValueError("At least one meeting minutes template is required")
    try:
        _validate_render_budget(base_context, max_pages=max_pages)
        _validate_manifest_values(base_context, input_metadata)
    except ValueError as error:
        _publish_rejection_manifest(
            output_dir,
            max_pages=max_pages,
            reason=str(error),
        )
        raise

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    source_texts = tuple(
        text
        for text in required_source_texts
        if isinstance(text, str) and text.strip()
    )
    template_texts = (
        str(base_context.get("document_type_label") or ""),
        str(base_context.get("title") or ""),
        str(base_context.get("agency_name") or ""),
    )
    combined_source_texts = list(source_texts)
    normalized_agency = _normalized(template_texts[2])
    if normalized_agency:
        for index, value in enumerate(combined_source_texts):
            if _normalized(value) == normalized_agency:
                combined_source_texts.pop(index)
                break
    template_required = _prepare_required_texts(template_texts)
    source_required = _prepare_required_texts(source_texts)
    combined_required = _prepare_required_texts(
        (*template_texts, *combined_source_texts)
    )
    matcher = _PatternMatcher(
        template_required.counts.keys()
        | source_required.counts.keys()
        | combined_required.counts.keys()
    )
    manifest: list[dict[str, object]] = []
    failures: list[str] = []
    publish_pairs: list[tuple[Path, Path]] = []

    with TemporaryDirectory(
        prefix=f".{output_dir.name}-meeting-",
        dir=output_dir.parent,
    ) as transaction_name:
        transaction_dir = Path(transaction_name)
        work_dir = transaction_dir / "work"
        candidate_dir = transaction_dir / "candidate"
        candidate_dir.mkdir()

        for variant in variants:
            if failures:
                break
            template_slug = variant["slug"]
            template = _ENVIRONMENT.get_template(variant["template"])
            staged_template_dir = work_dir / template_slug
            staged_template_dir.mkdir(parents=True, exist_ok=True)
            candidate_template_dir = candidate_dir / template_slug

            for spec in build_meeting_minutes_variation_specs(
                template_slug,
                count=per_template,
                base_seed=base_seed,
                start_offset=variation_offset,
            ):
                try:
                    html = template.render(
                        **base_context,
                        variant_class=(
                            f"{variant['variant_class']} {spec.css_class}"
                        ),
                        variation_css=_variation_css(spec),
                    )
                    staged_html_path = (
                        staged_template_dir / f"{spec.slug}.html"
                    )
                    staged_pdf_path = (
                        staged_template_dir / f"{spec.slug}.pdf"
                    )
                    staged_html_path.write_text(html, encoding="utf-8")
                    HTML(
                        string=html,
                        base_url=str(TEMPLATE_DIR),
                        url_fetcher=_RESTRICTED_URL_FETCHER,
                    ).write_pdf(staged_pdf_path)
                    actual_pages, pdf_text, first_page_text = _inspect_pdf(
                        staged_pdf_path
                    )
                except Exception as error:
                    _publish_rejection_manifest(
                        output_dir,
                        max_pages=max_pages,
                        reason=f"render_error:{type(error).__name__}",
                    )
                    raise RuntimeError(
                        "Meeting minutes rendering failed"
                    ) from error

                found_counts = matcher.count(pdf_text)
                missing_template_texts = _missing_texts(
                    template_required,
                    found_counts,
                )
                missing_source_texts = _missing_texts(
                    source_required,
                    found_counts,
                )
                missing_required_occurrences = _missing_texts(
                    combined_required,
                    found_counts,
                )
                title_present_on_first_page = (
                    _normalized(template_texts[1]) in first_page_text
                )
                page_count_valid = actual_pages <= max_pages
                status = (
                    "ok"
                    if (
                        page_count_valid
                        and title_present_on_first_page
                        and not missing_template_texts
                        and not missing_source_texts
                        and not missing_required_occurrences
                    )
                    else "rejected"
                )

                failure_reasons: list[str] = []
                if not page_count_valid:
                    failure_reasons.append(
                        f"maximum {max_pages} pages, got {actual_pages}"
                    )
                if not title_present_on_first_page:
                    failure_reasons.append("first-page title is incomplete")
                if missing_template_texts:
                    failure_reasons.append(
                        _missing_summary(
                            "template text",
                            missing_template_texts,
                        )
                    )
                if missing_source_texts:
                    failure_reasons.append(
                        _missing_summary(
                            "source text",
                            missing_source_texts,
                        )
                    )
                if missing_required_occurrences:
                    failure_reasons.append(
                        _missing_summary(
                            "required text occurrences",
                            missing_required_occurrences,
                        )
                    )
                if failure_reasons:
                    failures.append(
                        f"{template_slug}/{spec.slug}: "
                        + "; ".join(failure_reasons)
                    )

                agency_name = str(base_context.get("agency_name") or "")
                manifest.append(
                    {
                        "renderer_family": "meeting_minutes",
                        "template_slug": template_slug,
                        "template": variant["template"],
                        "variation_slug": spec.slug,
                        "status": status,
                        "artifact_published": False,
                        "max_pages": max_pages,
                        "actual_pages": actual_pages,
                        "required_text_present": (
                            not missing_template_texts
                            and not missing_required_occurrences
                        ),
                        "source_text_present": (
                            not missing_source_texts
                            and not missing_required_occurrences
                        ),
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

        try:
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
            _publish_output_directory(
                candidate_dir,
                output_dir,
                transaction_dir,
            )
        except Exception as error:
            _publish_rejection_manifest(
                output_dir,
                max_pages=max_pages,
                reason=f"publish_error:{type(error).__name__}",
            )
            raise RuntimeError(
                "Meeting minutes artifact publication failed"
            ) from error

        if failures:
            raise RuntimeError(
                "Meeting minutes render validation failed:\n"
                + "\n".join(f"- {failure}" for failure in failures)
            )
        return manifest
