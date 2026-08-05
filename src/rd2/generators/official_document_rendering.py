"""Jinja2 + WeasyPrint 공문 템플릿 공통 렌더러.

샘플 미리보기와 실제 생성 계약 입력이 같은 렌더링 경로를 사용하도록 한다.
문서 내용의 정규화/검증은 호출자가 담당하고, 이 모듈은 템플릿 변주와 출력물
검증만 담당한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

import fitz
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from rd2.generators.weasyprint_runtime import HTML
from rd2.generators.official_document_variations import (
    EXPECTED_PAGE_COUNTS,
    apply_identity_context,
    build_variation_specs,
    render_identity_css,
    render_variation_css,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

OFFICIAL_TEMPLATE_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "slug": "01_classic_municipal",
        "template": "official_variants/classic_municipal.html",
    },
    {
        "slug": "02_fire_station",
        "template": "official_variants/fire_station.html",
    },
    {
        "slug": "03_internal_approval",
        "template": "official_variants/internal_approval.html",
    },
    {
        "slug": "04_personnel_notice",
        "template": "official_variants/personnel_notice.html",
    },
    {
        "slug": "05_field_report",
        "template": "official_variants/field_report.html",
    },
    {
        "slug": "06_modern_public",
        "template": "official_variants/modern_public.html",
    },
    {
        "slug": "07_monochrome_hwp",
        "template": "official_variants/monochrome_hwp.html",
    },
    {
        "slug": "08_table_first_report",
        "template": "official_variants/table_first_report.html",
    },
    {
        "slug": "09_long_form",
        "template": "official_variants/long_form.html",
    },
    {
        "slug": "10_checklist_form",
        "template": "official_variants/checklist_form.html",
    },
)


def _page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as document:
        return document.page_count


def _normalized_pdf_text(pdf_path: Path) -> str:
    with fitz.open(pdf_path) as document:
        text = " ".join(page.get_text() for page in document)
    return "".join(text.split())


def _normalized_required_text(value: object) -> str:
    return "".join(str(value).split())


def _selected_variants(
    template_slugs: Collection[str] | None,
) -> tuple[dict[str, str], ...]:
    if template_slugs is None:
        return OFFICIAL_TEMPLATE_VARIANTS

    requested = frozenset(template_slugs)
    known = {variant["slug"] for variant in OFFICIAL_TEMPLATE_VARIANTS}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"Unknown official template slug(s): {', '.join(unknown)}")
    return tuple(
        variant
        for variant in OFFICIAL_TEMPLATE_VARIANTS
        if variant["slug"] in requested
    )


def render_official_document_variations(
    base_context: Mapping[str, Any],
    output_dir: Path,
    *,
    per_template: int = 3,
    base_seed: int = 20260728,
    variation_offset: int = 0,
    identity_seed: int | None = None,
    template_slugs: Collection[str] | None = None,
    protected_context_keys: Collection[str] = (),
    required_source_texts: Sequence[str] = (),
    enforce_expected_pages: bool = False,
    reject_legacy_identity: bool = False,
) -> list[dict[str, object]]:
    """공문 템플릿 변주를 렌더링하고 텍스트 누락을 검증한다.

    ``protected_context_keys``에 들어간 값은 렌더러 identity 처리에서
    변경하지 않는다. 생성 모델이 만든 제목과 본문은 반드시 이 목록으로
    보호해야 한다.
    """

    if per_template < 1:
        raise ValueError("per_template must be at least 1")
    if variation_offset < 0:
        raise ValueError("variation_offset must be at least 0")

    variants = _selected_variants(template_slugs)
    if not variants:
        raise ValueError("At least one official template must be selected")

    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(("html", "xml")),
        undefined=StrictUndefined,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    failures: list[str] = []

    source_texts = tuple(
        dict.fromkeys(
            text
            for text in required_source_texts
            if isinstance(text, str) and text.strip()
        )
    )
    source_agency_name = str(
        base_context.get("source_agency_name") or ""
    ).strip()

    for variant in variants:
        template_slug = variant["slug"]
        template = environment.get_template(variant["template"])
        template_dir = output_dir / template_slug
        template_dir.mkdir(parents=True, exist_ok=True)

        for spec in build_variation_specs(
            template_slug,
            count=per_template,
            base_seed=base_seed,
            start_offset=variation_offset,
        ):
            # 기관 identity는 payload가 제공한 기관명만 사용한다. 입력값이
            # 없으면 None으로 두어 가상 기관·주소·발신자 등을 생성하지 않는다.
            identity = None
            identity_context = apply_identity_context(
                base_context,
                identity,
                protected_keys=protected_context_keys,
            )
            state_classes = []
            signer_count = len(identity_context.get("signers") or ())
            if signer_count:
                state_classes.append(f"signer-count-{signer_count}")
            if not identity_context.get("details"):
                state_classes.append("has-no-details")
            table_context = identity_context.get("table") or {}
            if not table_context.get("headers"):
                state_classes.append("has-no-table")
            if not identity_context.get("attachments"):
                state_classes.append("has-no-attachments")
            if not identity_context.get("summary_text"):
                state_classes.append("has-no-summary")
            if not identity_context.get("signers"):
                state_classes.append("has-no-signers")
            if not any(
                identity_context.get(key)
                for key in (
                    "recipient",
                    "via",
                    "document_number",
                    "issue_date",
                    "disclosure",
                    "venue",
                )
            ):
                state_classes.append("has-no-routing-metadata")
            if not any(
                identity_context.get(key)
                for key in (
                    "postal_code",
                    "address",
                    "website",
                    "phone",
                    "fax",
                    "email",
                )
            ):
                state_classes.append("has-no-contact-metadata")
            if not any(
                identity_context.get(key)
                for key in ("issuer_title", "copy_recipients")
            ):
                state_classes.append("has-no-issuer-metadata")
            if not any(
                identity_context.get(key)
                for key in (
                    "slogan",
                    "brand_note",
                    "document_kind_label",
                    "document_kicker",
                    "details_heading",
                    "table_heading",
                    "secondary_heading",
                    "form_label",
                    "form_subtitle",
                    "guide_text",
                )
            ):
                state_classes.append("has-no-template-copy")
            if not identity_context.get("guide_text") and all(
                not item.get("owner") and not item.get("status")
                for item in identity_context.get("checklist_items", ())
            ):
                state_classes.append("has-no-workflow-metadata")
            context = {
                **identity_context,
                "variation_class": " ".join(
                    (
                        spec.css_class,
                        identity.css_class if identity else "identity-none",
                        *state_classes,
                    )
                ),
                "variation_css": (
                    render_variation_css(spec)
                    + render_identity_css(
                        identity,
                        accent=spec.accent,
                        light_accent=spec.light_accent,
                    )
                ),
            }
            html = template.render(**context)
            html_path = template_dir / f"{spec.slug}.html"
            pdf_path = template_dir / f"{spec.slug}.pdf"
            html_path.write_text(html, encoding="utf-8")
            HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(pdf_path)

            actual_pages = _page_count(pdf_path)
            expected_pages = (
                EXPECTED_PAGE_COUNTS[template_slug]
                if enforce_expected_pages
                else None
            )
            pdf_text = _normalized_pdf_text(pdf_path)

            required_template_texts = (
                str(base_context.get("title") or ""),
                str(base_context.get("document_number") or ""),
                source_agency_name or (identity.agency_name if identity else ""),
            )
            missing_template_texts = [
                value
                for value in required_template_texts
                if value
                and _normalized_required_text(value) not in pdf_text
            ]
            missing_source_texts = [
                value
                for value in source_texts
                if _normalized_required_text(value) not in pdf_text
            ]
            legacy_identity_present = (
                reject_legacy_identity and "한빛" in pdf_text
            )
            page_count_valid = (
                expected_pages is None or actual_pages == expected_pages
            )
            status = (
                "ok"
                if (
                    page_count_valid
                    and not missing_template_texts
                    and not missing_source_texts
                    and not legacy_identity_present
                )
                else "rejected"
            )

            failure_reasons: list[str] = []
            if not page_count_valid:
                failure_reasons.append(
                    f"expected {expected_pages} pages, got {actual_pages}"
                )
            if missing_template_texts:
                failure_reasons.append(
                    "missing template text "
                    + ", ".join(missing_template_texts)
                )
            if missing_source_texts:
                failure_reasons.append(
                    "missing source text "
                    + ", ".join(missing_source_texts)
                )
            if legacy_identity_present:
                failure_reasons.append("legacy identity token 한빛 remains")
            if failure_reasons:
                failures.append(
                    f"{template_slug}/{spec.slug}: "
                    + "; ".join(failure_reasons)
                )

            identity_manifest = {
                    "template_slug": template_slug,
                    "variation_index": spec.index,
                    "profile": "none",
                    "seed": None,
                    "agency_seed": None,
                    "agency_pool_index": None,
                    "agency_stem": None,
                    "organization_category": None,
                    "organization_type": None,
                    "issuer_role": None,
                    "romanized_name": None,
                    "slogan": None,
                    "brand_note": None,
                    "copy_recipients": None,
                    "road_name": None,
                    "district_name": None,
                    "agency_name": source_agency_name,
                    "display_agency_name": source_agency_name,
                    "issuer_title": None,
                    "administration_type": None,
                    "mark_text": "",
                    "css_class": "identity-none",
                    "agency_pool_size": 0,
                    "identity_source": "input" if source_agency_name else "none",
                    "selection_category": None,
                }

            manifest.append(
                {
                    "template_slug": template_slug,
                    "template": variant["template"],
                    "variation_slug": spec.slug,
                    "status": status,
                    "expected_pages": expected_pages,
                    "actual_pages": actual_pages,
                    "required_text_present": not missing_template_texts,
                    "source_text_present": not missing_source_texts,
                    "legacy_identity_absent": not legacy_identity_present,
                    "parameters": spec.to_dict(),
                    "identity": identity_manifest,
                    "approval": identity_context.get(
                        "approval_manifest",
                        [],
                    ),
                    "html": str(html_path),
                    "pdf": str(pdf_path),
                }
            )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        details = "\n".join(f"- {failure}" for failure in failures)
        raise RuntimeError(f"Official document render validation failed:\n{details}")
    return manifest
