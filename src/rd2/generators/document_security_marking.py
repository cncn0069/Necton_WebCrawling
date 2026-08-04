"""C 문서 PDF에 기관 워터마크와 보안 분류표지를 결정론적으로 적용한다.

문서 유형별 Jinja/WeasyPrint 템플릿은 본문 배치만 담당한다. 이 모듈은
렌더가 완료된 PDF에 ``logo/`` 원본 이미지를 삽입한다. 기관 로고는 본문을
읽을 수 있는 투명한 중앙 워터마크로, 대외비·군사기밀 표지는 충돌하지 않는
여백 전경으로 들어간다. 자산 매핑과 합성 수치는 ``logo/README.md``를 따른다.
따라서 새 문서 유형이 추가되어도 공통 렌더 진입점만 거치면 같은 정책이
적용된다.
"""

from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping, MutableMapping, Sequence
from uuid import uuid4

import fitz
from PIL import Image, ImageChops, ImageEnhance, ImageFilter

from rd2.generators.agency_resolver import (
    MILITARY_SECRET_MARK_FILENAMES,
    is_military_secret_agency,
    resolve_agency_logo,
)
from rd2.source_generation.contracts import (
    GenerationTarget,
    TargetClassification,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOGO_DIR = _REPO_ROOT / "logo"
_CONFIDENTIAL_MARK_ASSET = _LOGO_DIR / "대외비.png"

_GENERAL_MARK_HEIGHT_PT = 26.0
_GENERAL_EDGE_PT = 7.0
_GENERAL_ANCHORS = (0.0, 0.25, 0.5, 0.75, 1.0)
_GENERAL_SLOT_COUNT = 2 * len(_GENERAL_ANCHORS)

_MILITARY_MARK_HEIGHT_PT = 20.0
_MILITARY_EDGE_OFFSETS_PT = (4.0, 10.0, 16.0, 22.0, 28.0, 34.0, 40.0, 46.0)
_COLLISION_PADDING_PT = 3.0
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# 사용자 승인 미리보기(2026-08-03)의 작은·선명한 기관 워터마크 값.
# 각 페이지의 같은 상대 좌표에 넣으므로 한 문서 안에서는 위치가 변하지 않는다.
_AGENCY_MARK_WIDTH_RATIO = 0.42
_AGENCY_MARK_MAX_HEIGHT_RATIO = 0.30
_AGENCY_MARK_VERTICAL_OFFSET_RATIO = 0.036
_AGENCY_MARK_GRAY = 82
_AGENCY_MARK_MAX_ALPHA = 74
_AGENCY_MARK_RASTER_WIDTH_PX = 1040
_AGENCY_MARK_WHITE_THRESHOLD = 8


class SecurityMarkingError(RuntimeError):
    """보안표지를 안전하게 배치하거나 저장할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class SecurityMarkingSpec:
    kind: str
    asset_path: Path
    military_secret_grade: str | None = None


@dataclass(frozen=True)
class AgencyMarkingSpec:
    agency_name: str
    asset_path: Path


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


def resolve_agency_marking(
    target: GenerationTarget | Mapping[str, Any] | None,
    *,
    agency_name: str | None,
) -> AgencyMarkingSpec | None:
    """C 문서의 입력 기관명을 기관 워터마크 자산으로 변환한다."""

    if _classification_value(target) != TargetClassification.C.value:
        return None
    resolved = resolve_agency_logo(agency_name)
    if resolved is None:
        return None
    canonical_agency, filename = resolved
    return AgencyMarkingSpec(
        agency_name=canonical_agency,
        asset_path=_LOGO_DIR / filename,
    )


def resolve_security_marking(
    target: GenerationTarget | Mapping[str, Any] | None,
    *,
    agency_name: str | None,
) -> SecurityMarkingSpec | None:
    """생성 목표와 기관을 실제 PDF 표지 정책으로 변환한다."""

    if isinstance(target, Mapping):
        grade = target.get("military_secret_grade")
    elif target is not None:
        grade = target.military_secret_grade
    else:
        grade = None

    if _classification_value(target) != TargetClassification.C.value:
        return None

    resolved_agency = resolve_agency_logo(agency_name)
    agency = (
        resolved_agency[0]
        if resolved_agency is not None
        else (agency_name or "").strip()
    )
    if grade is not None and grade not in MILITARY_SECRET_MARK_FILENAMES:
        raise SecurityMarkingError(
            "military_secret_grade는 1급, 2급, 3급 중 하나여야 합니다"
        )
    if is_military_secret_agency(agency):
        if grade is None:
            raise SecurityMarkingError(
                f"{agency} C 문서는 military_secret_grade가 필요합니다"
            )
        asset_path = _LOGO_DIR / MILITARY_SECRET_MARK_FILENAMES[grade]
        return SecurityMarkingSpec(
            kind="military_secret",
            asset_path=asset_path,
            military_secret_grade=grade,
        )

    if grade is not None:
        raise SecurityMarkingError(
            "군사기밀 등급은 국방부·국가정보원 C 문서에만 "
            "사용할 수 있습니다"
        )
    return SecurityMarkingSpec(
        kind="confidential",
        asset_path=_CONFIDENTIAL_MARK_ASSET,
    )


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


def _open_agency_asset(asset_path: Path) -> Image.Image:
    """PNG와 SVG 기관 자산을 RGBA 이미지로 읽는다."""

    if not asset_path.is_file():
        raise SecurityMarkingError(f"기관 로고 이미지가 없습니다: {asset_path}")
    try:
        if asset_path.suffix.lower() == ".svg":
            svg_document = fitz.open(
                stream=asset_path.read_bytes(),
                filetype="svg",
            )
            try:
                if svg_document.page_count != 1:
                    raise SecurityMarkingError(
                        f"기관 SVG는 한 페이지여야 합니다: {asset_path}"
                    )
                pixmap = svg_document[0].get_pixmap(
                    matrix=fitz.Matrix(4, 4),
                    alpha=True,
                )
                return Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGBA")
            finally:
                svg_document.close()
        with Image.open(asset_path) as image:
            return image.convert("RGBA")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, SecurityMarkingError):
            raise
        raise SecurityMarkingError(
            f"기관 로고 이미지를 읽을 수 없습니다: {asset_path}"
        ) from exc


@lru_cache(maxsize=16)
def _agency_watermark_image(asset_path: Path) -> tuple[bytes, float]:
    """흰 배경을 지우고 선명한 저알파 회색 워터마크 PNG를 만든다."""

    source = _open_agency_asset(asset_path)
    red, green, blue, original_alpha = source.split()
    distance_from_white = ImageChops.lighter(
        ImageChops.invert(red),
        ImageChops.lighter(ImageChops.invert(green), ImageChops.invert(blue)),
    )
    foreground = distance_from_white.point(
        lambda value: (
            0
            if value <= _AGENCY_MARK_WHITE_THRESHOLD
            else min(255, (value - _AGENCY_MARK_WHITE_THRESHOLD) * 8)
        )
    )
    alpha = ImageChops.multiply(original_alpha, foreground)
    content_box = alpha.getbbox()
    if content_box is None:
        raise SecurityMarkingError(
            f"기관 로고에 표시할 픽셀이 없습니다: {asset_path}"
        )

    # 승인된 미리보기는 원본 캔버스의 내부 여백을 보존한다. 흰색 픽셀만
    # 투명하게 만들고 crop하지 않아, 같은 42% 박스 안에서도 국방부 문양과
    # 정부상징처럼 원본별 실제 문양 크기 차이가 그대로 남는다.
    target_width = _AGENCY_MARK_RASTER_WIDTH_PX
    target_height = max(1, round(target_width * alpha.height / alpha.width))
    alpha = alpha.resize(
        (target_width, target_height),
        Image.Resampling.LANCZOS,
    )
    alpha = ImageEnhance.Contrast(alpha).enhance(1.35)
    alpha = alpha.filter(
        ImageFilter.UnsharpMask(radius=1.1, percent=260, threshold=2)
    )
    alpha = alpha.point(
        lambda value: round(value * _AGENCY_MARK_MAX_ALPHA / 255)
    )

    watermark = Image.new(
        "RGBA",
        alpha.size,
        (_AGENCY_MARK_GRAY, _AGENCY_MARK_GRAY, _AGENCY_MARK_GRAY, 0),
    )
    watermark.putalpha(alpha)
    output = BytesIO()
    watermark.save(output, format="PNG", optimize=True)
    return output.getvalue(), watermark.width / watermark.height


def _agency_mark_rect(page: fitz.Page, *, image_ratio: float) -> fitz.Rect:
    mark_width = page.rect.width * _AGENCY_MARK_WIDTH_RATIO
    mark_height = mark_width / image_ratio
    max_height = page.rect.height * _AGENCY_MARK_MAX_HEIGHT_RATIO
    if mark_height > max_height:
        mark_height = max_height
        mark_width = mark_height * image_ratio
    x0 = (page.rect.width - mark_width) / 2
    y0 = (
        (page.rect.height - mark_height) / 2
        + page.rect.height * _AGENCY_MARK_VERTICAL_OFFSET_RATIO
    )
    return fitz.Rect(x0, y0, x0 + mark_width, y0 + mark_height)


def _expanded(rect: fitz.Rect, padding: float) -> fitz.Rect:
    return fitz.Rect(
        rect.x0 - padding,
        rect.y0 - padding,
        rect.x1 + padding,
        rect.y1 + padding,
    )


def _occupied_rects(page: fitz.Page) -> tuple[fitz.Rect, ...]:
    """텍스트와 기존 이미지를 충돌 검사 대상으로 반환한다."""

    blocks = page.get_text("dict", sort=True).get("blocks", [])
    return tuple(
        _expanded(fitz.Rect(block["bbox"]), _COLLISION_PADDING_PT)
        for block in blocks
        if block.get("bbox")
    )


def _general_mark_rect(
    page: fitz.Page,
    slot: int,
    *,
    image_ratio: float,
) -> fitz.Rect:
    mark_height = _GENERAL_MARK_HEIGHT_PT
    mark_width = mark_height * image_ratio
    top = slot < len(_GENERAL_ANCHORS)
    y0 = (
        _GENERAL_EDGE_PT
        if top
        else page.rect.height - _GENERAL_EDGE_PT - mark_height
    )
    anchor = _GENERAL_ANCHORS[slot % len(_GENERAL_ANCHORS)]
    available_width = page.rect.width - (2 * _GENERAL_EDGE_PT) - mark_width
    x0 = _GENERAL_EDGE_PT + max(0.0, available_width) * anchor
    return fitz.Rect(x0, y0, x0 + mark_width, y0 + mark_height)


def _preferred_general_slot(content_sha256: str) -> int:
    if not _SHA256_RE.fullmatch(content_sha256):
        raise SecurityMarkingError(
            "content_sha256는 소문자 64자리 SHA-256이어야 합니다"
        )
    return int(content_sha256[:16], 16) % _GENERAL_SLOT_COUNT


def _select_general_slot(
    document: fitz.Document,
    *,
    image_ratio: float,
    content_sha256: str,
    occupied_by_page: Sequence[Sequence[fitz.Rect]],
) -> int:
    preferred = _preferred_general_slot(content_sha256)
    for offset in range(_GENERAL_SLOT_COUNT):
        slot = (preferred + offset) % _GENERAL_SLOT_COUNT
        clear_on_every_page = all(
            not any(
                _general_mark_rect(page, slot, image_ratio=image_ratio).intersects(
                    occupied
                )
                for occupied in occupied_by_page[page_number]
            )
            for page_number, page in enumerate(document)
        )
        if clear_on_every_page:
            return slot
    raise SecurityMarkingError(
        "모든 페이지에서 공통으로 비어 있는 대외비 표지 위치가 없습니다"
    )


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
    y0 = (
        edge_offset
        if top
        else page.rect.height - edge_offset - mark_height
    )
    return fitz.Rect(x0, y0, x0 + mark_width, y0 + mark_height)


def _select_military_edge_offset(
    document: fitz.Document,
    *,
    image_ratio: float,
    top: bool,
    occupied_by_page: Sequence[Sequence[fitz.Rect]],
) -> float:
    for edge_offset in _MILITARY_EDGE_OFFSETS_PT:
        clear_on_every_page = all(
            not any(
                _military_mark_rect(
                    page,
                    image_ratio=image_ratio,
                    edge_offset=edge_offset,
                    top=top,
                ).intersects(occupied)
                for occupied in occupied_by_page[page_number]
            )
            for page_number, page in enumerate(document)
        )
        if clear_on_every_page:
            return edge_offset
    edge_name = "상단" if top else "하단"
    raise SecurityMarkingError(
        "모든 페이지에서 공통으로 비어 있는 "
        f"군사기밀 {edge_name} 위치가 없습니다"
    )


def _save_marked_pdf(
    source_path: Path,
    output_path: Path,
    *,
    spec: SecurityMarkingSpec,
    agency_spec: AgencyMarkingSpec | None,
    content_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    image_ratio = _image_ratio(spec.asset_path)
    try:
        image_bytes = spec.asset_path.read_bytes()
    except OSError as exc:
        raise SecurityMarkingError(
            f"보안표지 이미지를 읽을 수 없습니다: {spec.asset_path}"
        ) from exc
    try:
        document = fitz.open(source_path)
    except (OSError, RuntimeError) as exc:
        raise SecurityMarkingError(f"PDF를 열 수 없습니다: {source_path}") from exc

    try:
        if document.page_count == 0:
            raise SecurityMarkingError(
                f"빈 PDF에는 보안표지를 넣을 수 없습니다: {source_path}"
            )

        occupied_by_page = tuple(_occupied_rects(page) for page in document)
        agency_marking: dict[str, Any] | None = None
        if agency_spec is not None:
            agency_image_bytes, agency_image_ratio = _agency_watermark_image(
                agency_spec.asset_path
            )
            agency_image_xref = 0
            # WeasyPrint/Chromium PDF는 흰 페이지 배경을 불투명하게 그린다.
            # underlay(overlay=False)는 보이지 않으므로, logo/README.md의 정책대로
            # 자체 알파가 낮은 PNG를 전경에 합성해 본문 가독성을 보존한다.
            for page in document:
                agency_image_xref = page.insert_image(
                    _agency_mark_rect(page, image_ratio=agency_image_ratio),
                    stream=agency_image_bytes,
                    xref=agency_image_xref,
                    keep_proportion=True,
                    overlay=True,
                )
            agency_marking = {
                "agency_name": agency_spec.agency_name,
                "asset": f"logo/{agency_spec.asset_path.name}",
                "placement": {
                    "strategy": "center_watermark",
                    "width_ratio": _AGENCY_MARK_WIDTH_RATIO,
                    "max_height_ratio": _AGENCY_MARK_MAX_HEIGHT_RATIO,
                    "vertical_offset_ratio": _AGENCY_MARK_VERTICAL_OFFSET_RATIO,
                },
                "tone": {
                    "grayscale": _AGENCY_MARK_GRAY,
                    "max_alpha": _AGENCY_MARK_MAX_ALPHA,
                },
                "overlay": True,
            }

        image_xref = 0
        if spec.kind == "confidential":
            slot = _select_general_slot(
                document,
                image_ratio=image_ratio,
                content_sha256=content_sha256,
                occupied_by_page=occupied_by_page,
            )
            for page in document:
                image_xref = page.insert_image(
                    _general_mark_rect(page, slot, image_ratio=image_ratio),
                    stream=image_bytes,
                    xref=image_xref,
                    keep_proportion=True,
                    overlay=True,
                )
            placement: dict[str, Any] = {
                "strategy": "perimeter_slot",
                "slot": slot,
            }
        else:
            top_offset = _select_military_edge_offset(
                document,
                image_ratio=image_ratio,
                top=True,
                occupied_by_page=occupied_by_page,
            )
            bottom_offset = _select_military_edge_offset(
                document,
                image_ratio=image_ratio,
                top=False,
                occupied_by_page=occupied_by_page,
            )
            for page in document:
                for rect in (
                    _military_mark_rect(
                        page,
                        image_ratio=image_ratio,
                        edge_offset=top_offset,
                        top=True,
                    ),
                    _military_mark_rect(
                        page,
                        image_ratio=image_ratio,
                        edge_offset=bottom_offset,
                        top=False,
                    ),
                ):
                    image_xref = page.insert_image(
                        rect,
                        stream=image_bytes,
                        xref=image_xref,
                        keep_proportion=True,
                        overlay=True,
                    )
            placement = {
                "strategy": "top_bottom_center",
                "top_offset_pt": top_offset,
                "bottom_offset_pt": bottom_offset,
            }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path, deflate=True, garbage=4)
    except (OSError, RuntimeError, ValueError) as exc:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, SecurityMarkingError):
            raise
        raise SecurityMarkingError(
            f"PDF 보안표지 적용에 실패했습니다: {source_path}"
        ) from exc
    finally:
        document.close()

    return (
        {
            "kind": spec.kind,
            "asset": f"logo/{spec.asset_path.name}",
            "military_secret_grade": spec.military_secret_grade,
            "placement": placement,
            "overlay": True,
        },
        agency_marking,
    )


def apply_security_marking_to_manifest(
    manifest: Sequence[MutableMapping[str, Any]],
    *,
    target: GenerationTarget | Mapping[str, Any] | None,
    agency_name: str | None,
    content_sha256: str,
) -> None:
    """성공한 PDF 전체를 먼저 staging한 뒤 원본과 교체한다.

    한 PDF라도 충돌 검사나 저장에 실패하면 staging 파일만 지우고 기존 PDF는
    그대로 둔다. 모든 PDF가 준비된 뒤에만 교체하므로 불완전하게 표시된
    묶음이 정상 결과로 게시되지 않는다.
    """

    spec = resolve_security_marking(target, agency_name=agency_name)
    if spec is None:
        return
    agency_spec = resolve_agency_marking(target, agency_name=agency_name)

    staged: list[
        tuple[
            Path,
            Path,
            MutableMapping[str, Any],
            dict[str, Any],
            dict[str, Any] | None,
        ]
    ] = []
    backups: list[tuple[Path, Path]] = []
    try:
        for entry in manifest:
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
            marking, agency_marking = _save_marked_pdf(
                pdf_path,
                staged_path,
                spec=spec,
                agency_spec=agency_spec,
                content_sha256=content_sha256,
            )
            staged.append(
                (staged_path, pdf_path, entry, marking, agency_marking)
            )

        try:
            for (
                staged_path,
                pdf_path,
                entry,
                marking,
                agency_marking,
            ) in staged:
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
                entry["security_marking"] = marking
                if agency_marking is not None:
                    entry["agency_marking"] = agency_marking
                else:
                    entry.pop("agency_marking", None)
        except OSError as exc:
            for pdf_path, backup_path in reversed(backups):
                backup_path.replace(pdf_path)
            for _, _, entry, _, _ in staged:
                entry.pop("security_marking", None)
                entry.pop("agency_marking", None)
            raise SecurityMarkingError(
                "보안표지 PDF 묶음을 최종 경로에 게시하지 못했습니다"
            ) from exc
        else:
            for _, backup_path in backups:
                backup_path.unlink(missing_ok=True)
            backups.clear()
    finally:
        for staged_path, _, _, _, _ in staged:
            staged_path.unlink(missing_ok=True)
        for _, backup_path in backups:
            backup_path.unlink(missing_ok=True)
