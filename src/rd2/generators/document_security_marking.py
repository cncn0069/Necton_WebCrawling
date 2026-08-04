"""C 문서 PDF에 단색 대외비 스킨 또는 군사기밀 표지를 적용한다.

문서 유형별 템플릿은 본문 PDF만 만든다. 이 모듈은 본문 페이지 상한 적용이
끝난 PDF의 예약 여백에 단색 보안 스킨을 더한다. 군사기밀은 맨 앞에 등급별
표지 한 장을 추가하고 본문 각 면의 상·하단 중앙에 같은 등급표시도 넣는다.
표지는 본문 페이지 수에 포함하지 않는다.

기관명이나 정부부처 로고는 보안표지 결정에 사용하지 않는다. 호출자가 생성
계약에 ``classification=C``만 지정하면 대외비, 여기에
``military_secret_grade`` 1급·2급·3급을 지정하면 군사기밀로 처리한다. S/O
문서는 그대로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence
from uuid import uuid4

import fitz
from PIL import Image, ImageEnhance, ImageOps

from rd2.generators.agency_resolver import (
    MILITARY_SECRET_COVER_FILENAMES,
    MILITARY_SECRET_MARK_FILENAMES,
)
from rd2.generators.confidential_security_templates import (
    BODY_SAFE_TOP_BOTTOM_PT,
    ConfidentialSecurityTemplate,
    MILITARY_NEUTRAL_SECURITY_FRAME,
    body_safe_rect,
    draw_confidential_security_template,
    select_confidential_security_template,
)
from rd2.source_generation.contracts import GenerationTarget, TargetClassification

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOGO_DIR = _REPO_ROOT / "logo"

_MILITARY_MARK_HEIGHT_PT = 25.0
_MILITARY_MARK_EDGE_OFFSET_PT = 2.0

_POINTS_PER_CM = 72.0 / 2.54
_COVER_WIDTH_PT = 17.0 * _POINTS_PER_CM
_COVER_PAGE_MARGIN_PT = 12.0
_COVER_RASTER_WIDTH_PX = 2000
_COVER_COLORS: dict[str, tuple[int, int, int]] = {
    "1급": (190, 0, 0),
    "2급": (194, 142, 0),
    "3급": (0, 76, 173),
}


class SecurityMarkingError(RuntimeError):
    """대외비·군사기밀 표지를 안전하게 배치하거나 저장할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class SecurityMarkingSpec:
    kind: str
    mark_asset_path: Path | None = None
    cover_asset_path: Path | None = None
    military_secret_grade: str | None = None


def _classification_value(
    target: GenerationTarget | Mapping[str, Any] | None,
) -> str:
    if target is None:
        return ""
    classification = (
        target.get("classification")
        if isinstance(target, Mapping)
        else target.classification
    )
    return (
        classification.value
        if isinstance(classification, TargetClassification)
        else str(classification or "")
    ).strip().upper()


def _military_secret_grade(
    target: GenerationTarget | Mapping[str, Any] | None,
) -> str | None:
    if isinstance(target, Mapping):
        grade = target.get("military_secret_grade")
    elif target is not None:
        grade = target.military_secret_grade
    else:
        grade = None
    return str(grade).strip() if grade is not None else None


def _spec_for_grade(grade: str) -> SecurityMarkingSpec:
    if (
        grade not in MILITARY_SECRET_MARK_FILENAMES
        or grade not in MILITARY_SECRET_COVER_FILENAMES
    ):
        raise SecurityMarkingError(
            "military_secret_grade는 1급, 2급, 3급 중 하나여야 합니다"
        )
    return SecurityMarkingSpec(
        kind="military_secret",
        mark_asset_path=_LOGO_DIR / MILITARY_SECRET_MARK_FILENAMES[grade],
        cover_asset_path=_LOGO_DIR / MILITARY_SECRET_COVER_FILENAMES[grade],
        military_secret_grade=grade,
    )


def _confidential_spec() -> SecurityMarkingSpec:
    return SecurityMarkingSpec(kind="confidential")


def resolve_security_marking(
    target: GenerationTarget | Mapping[str, Any] | None,
) -> SecurityMarkingSpec | None:
    """C는 대외비로, 명시된 1·2·3급은 군사기밀 표지로 변환한다."""

    if _classification_value(target) != TargetClassification.C.value:
        return None
    grade = _military_secret_grade(target)
    if grade is None:
        return _confidential_spec()
    return _spec_for_grade(grade)


@lru_cache(maxsize=4)
def _image_ratio(asset_path: Path) -> float:
    if not asset_path.is_file():
        raise SecurityMarkingError(f"보안표지 이미지가 없습니다: {asset_path}")
    try:
        with Image.open(asset_path) as image:
            if image.height <= 0:
                raise SecurityMarkingError(
                    f"보안표지 이미지 높이가 올바르지 않습니다: {asset_path}"
                )
            return image.width / image.height
    except OSError as exc:
        raise SecurityMarkingError(
            f"보안표지 이미지를 읽을 수 없습니다: {asset_path}"
        ) from exc


@lru_cache(maxsize=1)
def _confidential_mark_bytes(asset_path: Path) -> tuple[bytes, float]:
    """기존 붉은 대외비 도안을 어두운 단색 PNG로 변환한다."""

    if not asset_path.is_file():
        raise SecurityMarkingError(f"대외비 이미지가 없습니다: {asset_path}")
    try:
        with Image.open(asset_path) as source:
            rgba = source.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            grayscale = ImageOps.grayscale(background)
    except OSError as exc:
        raise SecurityMarkingError(
            f"대외비 이미지를 읽을 수 없습니다: {asset_path}"
        ) from exc

    grayscale = ImageOps.autocontrast(grayscale, cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.25)
    monochrome = ImageOps.colorize(
        grayscale,
        black=(34, 39, 44),
        white=(255, 255, 255),
    )
    output = BytesIO()
    monochrome.save(output, format="PNG", optimize=True)
    return output.getvalue(), monochrome.width / monochrome.height


@lru_cache(maxsize=3)
def _cover_image_bytes(asset_path: Path, grade: str) -> tuple[bytes, float]:
    """흑백 규정 도안을 등급 색으로 선명하게 만든 고해상도 PNG를 반환한다."""

    if not asset_path.is_file():
        raise SecurityMarkingError(f"군사기밀 표지 이미지가 없습니다: {asset_path}")
    try:
        with Image.open(asset_path) as source:
            rgba = source.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            grayscale = ImageOps.grayscale(background)
    except OSError as exc:
        raise SecurityMarkingError(
            f"군사기밀 표지 이미지를 읽을 수 없습니다: {asset_path}"
        ) from exc

    ratio = grayscale.width / grayscale.height
    target_height = max(1, round(_COVER_RASTER_WIDTH_PX / ratio))
    grayscale = grayscale.resize(
        (_COVER_RASTER_WIDTH_PX, target_height),
        Image.Resampling.LANCZOS,
    )
    grayscale = ImageOps.autocontrast(grayscale, cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.18)
    grayscale = ImageEnhance.Sharpness(grayscale).enhance(1.45)
    tinted = ImageOps.colorize(
        grayscale,
        black=_COVER_COLORS[grade],
        white=(255, 255, 255),
    )
    output = BytesIO()
    tinted.save(output, format="PNG", optimize=True)
    return output.getvalue(), ratio


def _military_mark_rect(
    page: fitz.Page,
    *,
    image_ratio: float,
    edge_offset: float,
    top: bool,
) -> fitz.Rect:
    mark_height = _MILITARY_MARK_HEIGHT_PT
    mark_width = mark_height * image_ratio
    x0 = (page.rect.width - mark_width) / 2
    y0 = edge_offset if top else page.rect.height - edge_offset - mark_height
    return fitz.Rect(x0, y0, x0 + mark_width, y0 + mark_height)


def _cover_rect(page: fitz.Page, *, image_ratio: float) -> fitz.Rect:
    width = min(_COVER_WIDTH_PT, page.rect.width - 2 * _COVER_PAGE_MARGIN_PT)
    height = width / image_ratio
    max_height = page.rect.height - 2 * _COVER_PAGE_MARGIN_PT
    if height > max_height:
        height = max_height
        width = height * image_ratio
    x0 = (page.rect.width - width) / 2
    y0 = (page.rect.height - height) / 2
    return fitz.Rect(x0, y0, x0 + width, y0 + height)


def _prepend_cover_page(document: fitz.Document, spec: SecurityMarkingSpec) -> None:
    if spec.cover_asset_path is None or spec.military_secret_grade is None:
        raise SecurityMarkingError("군사기밀 앞표지에 등급과 표지 자산이 없습니다")
    first_page_rect = document[0].rect
    cover_bytes, cover_ratio = _cover_image_bytes(
        spec.cover_asset_path,
        spec.military_secret_grade,
    )
    cover = document.new_page(
        pno=0,
        width=first_page_rect.width,
        height=first_page_rect.height,
    )
    cover.draw_rect(cover.rect, color=None, fill=(1, 1, 1), overlay=True)
    cover.insert_image(
        _cover_rect(cover, image_ratio=cover_ratio),
        stream=cover_bytes,
        keep_proportion=True,
        overlay=True,
    )


def _save_marked_pdf(
    source_path: Path,
    output_path: Path,
    *,
    spec: SecurityMarkingSpec,
    security_template: ConfidentialSecurityTemplate,
) -> dict[str, Any]:
    military = spec.kind == "military_secret"
    if military:
        if spec.mark_asset_path is None:
            raise SecurityMarkingError("군사기밀 등급표시 자산이 없습니다")
        effective_mark_asset_path = spec.mark_asset_path
    else:
        effective_mark_asset_path = _LOGO_DIR / security_template.stamp_asset
    if military:
        mark_ratio = _image_ratio(effective_mark_asset_path)
        try:
            mark_bytes = effective_mark_asset_path.read_bytes()
        except OSError as exc:
            raise SecurityMarkingError(
                "군사기밀 등급표시 이미지를 읽을 수 없습니다: "
                f"{effective_mark_asset_path}"
            ) from exc
        skin_mark_bytes = None
        skin_mark_ratio = None
    else:
        mark_bytes, mark_ratio = _confidential_mark_bytes(
            effective_mark_asset_path
        )
        skin_mark_bytes = mark_bytes
        skin_mark_ratio = mark_ratio
    try:
        source_document = fitz.open(source_path)
    except (OSError, RuntimeError) as exc:
        raise SecurityMarkingError(f"PDF를 열 수 없습니다: {source_path}") from exc

    marked_document = fitz.open()
    try:
        if source_document.page_count == 0:
            raise SecurityMarkingError(
                f"빈 PDF에는 보안표지를 넣을 수 없습니다: {source_path}"
            )

        content_page_count = source_document.page_count
        mark_xref = 0
        skin_placements: list[dict[str, object]] = []
        for page_number, source_page in enumerate(source_document):
            page = marked_document.new_page(
                width=source_page.rect.width,
                height=source_page.rect.height,
            )
            page.show_pdf_page(
                body_safe_rect(page),
                source_document,
                page_number,
                keep_proportion=True,
                overlay=True,
            )
            # WeasyPrint continuation pages can leave an active clipping or
            # graphics state at the end of their content stream. Isolate the
            # imported body before drawing security marks so a continued table
            # cannot clip a stamp on the following page.
            page.wrap_contents()
            skin_placements.append(
                draw_confidential_security_template(
                    page,
                    template=security_template,
                    mark_bytes=skin_mark_bytes,
                    mark_ratio=skin_mark_ratio,
                )
            )
            if military:
                for rect in (
                    _military_mark_rect(
                        page,
                        image_ratio=mark_ratio,
                        edge_offset=_MILITARY_MARK_EDGE_OFFSET_PT,
                        top=True,
                    ),
                    _military_mark_rect(
                        page,
                        image_ratio=mark_ratio,
                        edge_offset=_MILITARY_MARK_EDGE_OFFSET_PT,
                        top=False,
                    ),
                ):
                    mark_xref = page.insert_image(
                        rect,
                        stream=mark_bytes,
                        xref=mark_xref,
                        keep_proportion=True,
                        overlay=True,
                    )

        if military:
            _prepend_cover_page(marked_document, spec)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        marked_document.set_metadata(source_document.metadata)
        marked_document.save(output_path, deflate=True, garbage=4)
        final_pdf_page_count = marked_document.page_count
    except (OSError, RuntimeError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        if isinstance(exc, SecurityMarkingError):
            raise
        raise SecurityMarkingError(
            f"PDF 보안표지 적용에 실패했습니다: {source_path}"
        ) from exc
    finally:
        marked_document.close()
        source_document.close()

    placement: dict[str, Any] = {
        "strategy": (
            "front_cover_top_bottom_and_neutral_frame"
            if military
            else "monochrome_security_skin"
        ),
        "body": {
            "strategy": "reserved_security_frame",
            "safe_top_bottom_pt": BODY_SAFE_TOP_BOTTOM_PT,
            "security_template": security_template.to_dict(),
            "page_placements": skin_placements,
        },
    }
    if military:
        assert spec.cover_asset_path is not None
        assert spec.military_secret_grade is not None
        placement["cover"] = {
            "position": "before_content",
            "page_count": 1,
            "counted_in_page_limit": False,
            "width_cm": 17.0,
            "color_rgb": list(_COVER_COLORS[spec.military_secret_grade]),
        }
        placement["body"].update(
            {
                "military_mark_strategy": "top_bottom_center",
                "top_offset_pt": _MILITARY_MARK_EDGE_OFFSET_PT,
                "bottom_offset_pt": _MILITARY_MARK_EDGE_OFFSET_PT,
            }
        )

    marking: dict[str, Any] = {
        "kind": spec.kind,
        "asset": f"logo/{effective_mark_asset_path.name}",
        "asset_kind": (
            "military_grade_mark"
            if military
            else "synthetic_security_stamp"
        ),
        "security_template": security_template.to_dict(),
        "palette": {
            "mode": "monochrome_dark",
            "ink_hex": "#22272C",
        },
        "placement": placement,
        "content_page_count": content_page_count,
        "final_pdf_page_count": final_pdf_page_count,
        "overlay": True,
    }
    if military:
        assert spec.cover_asset_path is not None
        marking["cover_asset"] = f"logo/{spec.cover_asset_path.name}"
        marking["military_secret_grade"] = spec.military_secret_grade
    return marking


def prepend_military_secret_cover(pdf_path: Path, grade: str) -> None:
    """기존 PDF의 본문을 건드리지 않고 등급별 앞표지 한 장만 원자적으로 붙인다."""

    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise SecurityMarkingError(f"PDF가 없습니다: {pdf_path}")
    spec = _spec_for_grade(grade)
    staged_path = pdf_path.with_name(
        f".{pdf_path.stem}.{uuid4().hex}.military-cover.pdf"
    )
    try:
        try:
            document = fitz.open(pdf_path)
        except (OSError, RuntimeError) as exc:
            raise SecurityMarkingError(f"PDF를 열 수 없습니다: {pdf_path}") from exc
        try:
            if document.page_count == 0:
                raise SecurityMarkingError(
                    f"빈 PDF에는 군사기밀 표지를 넣을 수 없습니다: {pdf_path}"
                )
            _prepend_cover_page(document, spec)
            document.save(staged_path, deflate=True, garbage=4)
        finally:
            document.close()
        staged_path.replace(pdf_path)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, SecurityMarkingError):
            raise
        raise SecurityMarkingError(
            f"PDF 군사기밀 앞표지 추가에 실패했습니다: {pdf_path}"
        ) from exc
    finally:
        staged_path.unlink(missing_ok=True)


def apply_security_marking_to_manifest(
    manifest: Sequence[MutableMapping[str, Any]],
    *,
    target: GenerationTarget | Mapping[str, Any] | None,
    selection_seed: int = 0,
) -> None:
    """성공 PDF 묶음 전체를 staging한 뒤에만 C 문서판으로 교체한다."""

    spec = resolve_security_marking(target)
    if spec is None:
        for entry in manifest:
            entry.pop("agency_marking", None)
            entry.pop("security_marking", None)
        return

    staged: list[
        tuple[Path, Path, MutableMapping[str, Any], dict[str, Any]]
    ] = []
    backups: list[tuple[Path, Path]] = []
    try:
        for entry_index, entry in enumerate(manifest):
            if entry.get("status") != "ok":
                continue
            raw_pdf_path = entry.get("pdf")
            if not isinstance(raw_pdf_path, str) or not raw_pdf_path:
                raise SecurityMarkingError("성공 manifest에 PDF 경로가 없습니다")
            pdf_path = Path(raw_pdf_path)
            if not pdf_path.is_file():
                raise SecurityMarkingError(f"렌더링된 PDF가 없습니다: {pdf_path}")
            staged_path = pdf_path.with_name(
                f".{pdf_path.stem}.{uuid4().hex}.security-mark.pdf"
            )
            security_template = (
                MILITARY_NEUTRAL_SECURITY_FRAME
                if spec.kind == "military_secret"
                else select_confidential_security_template(
                    selection_seed,
                    offset=entry_index,
                )
            )
            marking = _save_marked_pdf(
                pdf_path,
                staged_path,
                spec=spec,
                security_template=security_template,
            )
            staged.append((staged_path, pdf_path, entry, marking))

        try:
            for staged_path, pdf_path, entry, marking in staged:
                backup_path = pdf_path.with_name(
                    f".{pdf_path.stem}.{uuid4().hex}.security-mark.backup.pdf"
                )
                pdf_path.replace(backup_path)
                backups.append((pdf_path, backup_path))
                try:
                    staged_path.replace(pdf_path)
                except OSError:
                    backup_path.replace(pdf_path)
                    backups.pop()
                    raise
        except OSError as exc:
            for pdf_path, backup_path in reversed(backups):
                backup_path.replace(pdf_path)
            raise SecurityMarkingError(
                "보안표지 PDF 묶음을 최종 경로에 게시하지 못했습니다"
            ) from exc
        else:
            for _, _, entry, marking in staged:
                entry.pop("agency_marking", None)
                entry["security_marking"] = marking
            for _, backup_path in backups:
                backup_path.unlink(missing_ok=True)
            backups.clear()
    finally:
        for staged_path, _, _, _ in staged:
            staged_path.unlink(missing_ok=True)
        for _, backup_path in backups:
            backup_path.unlink(missing_ok=True)
