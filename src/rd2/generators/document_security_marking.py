"""C 문서 PDF에 대외비·군사기밀 분류표지를 결정론적으로 적용한다.

문서 유형별 Jinja/WeasyPrint 템플릿은 본문 배치만 담당한다. 이 모듈은
렌더가 완료된 PDF의 여백을 검사한 뒤 ``logo/`` 원본 이미지를 전경에
삽입한다. 따라서 새 문서 유형이 추가되어도 공통 렌더 진입점만 거치면
같은 정책이 적용된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping, MutableMapping, Sequence
from uuid import uuid4

import fitz
from PIL import Image

from rd2.generators.agency_resolver import (
    MILITARY_SECRET_MARK_FILENAMES,
    is_military_secret_agency,
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


class SecurityMarkingError(RuntimeError):
    """보안표지를 안전하게 배치하거나 저장할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class SecurityMarkingSpec:
    kind: str
    asset_path: Path
    military_secret_grade: str | None = None


def resolve_security_marking(
    target: GenerationTarget | Mapping[str, Any] | None,
    *,
    agency_name: str | None,
) -> SecurityMarkingSpec | None:
    """생성 목표와 기관을 실제 PDF 표지 정책으로 변환한다."""

    if target is None:
        return None

    if isinstance(target, Mapping):
        classification = target.get("classification")
        grade = target.get("military_secret_grade")
    else:
        classification = target.classification
        grade = target.military_secret_grade

    classification_value = (
        classification.value
        if isinstance(classification, TargetClassification)
        else str(classification or "")
    )
    if classification_value.strip().upper() != TargetClassification.C.value:
        return None

    agency = (agency_name or "").strip()
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
    content_sha256: str,
) -> dict[str, Any]:
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

    return {
        "kind": spec.kind,
        "asset": f"logo/{spec.asset_path.name}",
        "military_secret_grade": spec.military_secret_grade,
        "placement": placement,
        "overlay": True,
    }


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

    staged: list[tuple[Path, Path, MutableMapping[str, Any], dict[str, Any]]] = []
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
            marking = _save_marked_pdf(
                pdf_path,
                staged_path,
                spec=spec,
                content_sha256=content_sha256,
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
                entry["security_marking"] = marking
        except OSError as exc:
            for pdf_path, backup_path in reversed(backups):
                backup_path.replace(pdf_path)
            for _, _, entry, _ in staged:
                entry.pop("security_marking", None)
            raise SecurityMarkingError(
                "보안표지 PDF 묶음을 최종 경로에 게시하지 못했습니다"
            ) from exc
        else:
            for _, backup_path in backups:
                backup_path.unlink(missing_ok=True)
            backups.clear()
    finally:
        for staged_path, _, _, _ in staged:
            staged_path.unlink(missing_ok=True)
        for _, backup_path in backups:
            backup_path.unlink(missing_ok=True)
