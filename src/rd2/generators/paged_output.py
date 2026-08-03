"""생성 문서 PDF의 원문 보존 검증과 최종 페이지 제한 처리."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence
from uuid import uuid4

import fitz


class RenderedSourceTextError(RuntimeError):
    """자연 배치된 PDF에서 입력 원문 전체의 순서를 확인하지 못했다."""


@dataclass(frozen=True)
class PageLimitResult:
    original_page_count: int
    retained_page_count: int
    discarded_page_count: int
    truncated: bool

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def _normalized(value: str) -> str:
    return "".join(value.split())


def _fragmentation_tolerant_text_present(
    value: str,
    normalized_pdf_text: str,
) -> bool:
    """반복 머리말이 끼어도 원문 전체가 순서대로 남았는지 검증한다.

    페이지 분할 시 PDF 텍스트 추출 순서에는 머리말·쪽번호가 긴 문단 사이에
    삽입될 수 있다. 짧은 값은 기존처럼 완전 일치를 요구하고, 긴 값은 PDF에
    추가 문자가 삽입되는 것만 허용하는 부분수열 검사로 중간 원문까지 확인한다.
    """

    normalized = _normalized(value)
    if not normalized:
        return True
    if normalized in normalized_pdf_text:
        return True
    if len(normalized) < 80:
        return False

    source_index = 0
    for character in normalized_pdf_text:
        if character != normalized[source_index]:
            continue
        source_index += 1
        if source_index == len(normalized):
            return True
    return False


def validate_pdf_source_texts(
    pdf_path: Path,
    required_source_texts: Sequence[str],
) -> None:
    """페이지를 자르기 전 PDF가 모든 원문 단위를 실제로 담았는지 검증한다."""

    with fitz.open(pdf_path) as document:
        normalized_pdf_text = _normalized(
            "\n".join(page.get_text() for page in document)
        )
    missing = [
        value
        for value in dict.fromkeys(required_source_texts)
        if value.strip()
        and not _fragmentation_tolerant_text_present(
            value,
            normalized_pdf_text,
        )
    ]
    if missing:
        raise RenderedSourceTextError(
            f"Rendered PDF is missing {len(missing)} source text value(s)"
        )


def _stage_truncated_pdf(
    pdf_path: Path,
    *,
    max_pages: int,
) -> tuple[PageLimitResult, Path | None]:
    """절단본을 원본 옆에 준비하되 아직 게시하지 않는다."""

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    with fitz.open(pdf_path) as source:
        original_page_count = source.page_count
        retained_page_count = min(original_page_count, max_pages)
        result = PageLimitResult(
            original_page_count=original_page_count,
            retained_page_count=retained_page_count,
            discarded_page_count=(
                original_page_count - retained_page_count
            ),
            truncated=original_page_count > max_pages,
        )
        if not result.truncated:
            return result, None

        staged_path = pdf_path.with_name(
            f".{pdf_path.stem}.truncated-{uuid4().hex}.pdf"
        )
        truncated = fitz.open()
        try:
            truncated.insert_pdf(source, from_page=0, to_page=max_pages - 1)
            try:
                truncated.save(staged_path, garbage=4, deflate=True)
            except Exception:
                staged_path.unlink(missing_ok=True)
                raise
        finally:
            truncated.close()
    return result, staged_path


def truncate_pdf_to_page_limit(
    pdf_path: Path,
    *,
    max_pages: int,
) -> PageLimitResult:
    """자연 배치 결과의 첫 ``max_pages`` 페이지만 원자적으로 보존한다."""

    result, staged_path = _stage_truncated_pdf(pdf_path, max_pages=max_pages)
    if staged_path is not None:
        try:
            staged_path.replace(pdf_path)
        except OSError:
            staged_path.unlink(missing_ok=True)
            raise
    return result


def finalize_manifest_page_limits(
    manifest: Sequence[dict[str, object]],
    *,
    max_pages: int,
    render_page_budget: int,
    required_source_texts: Sequence[str],
) -> None:
    """성공 산출물을 검증한 뒤 자르고 manifest를 최종 출력 기준으로 갱신한다."""

    successful: list[tuple[dict[str, object], Path]] = []
    for entry in manifest:
        if entry.get("status") != "ok":
            continue
        pdf_value = entry.get("pdf")
        if not pdf_value:
            raise RuntimeError("Successful render did not publish a PDF path")
        pdf_path = Path(str(pdf_value))
        validate_pdf_source_texts(pdf_path, required_source_texts)
        successful.append((entry, pdf_path))

    prepared: list[
        tuple[dict[str, object], Path, PageLimitResult, Path | None]
    ] = []
    try:
        for entry, pdf_path in successful:
            result, staged_path = _stage_truncated_pdf(
                pdf_path,
                max_pages=max_pages,
            )
            prepared.append((entry, pdf_path, result, staged_path))
    except Exception:
        for _, _, _, staged_path in prepared:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
        raise

    backups: list[tuple[Path, Path]] = []
    try:
        for _, pdf_path, _, staged_path in prepared:
            if staged_path is None:
                continue
            backup_path = pdf_path.with_name(
                f".{pdf_path.stem}.pre-truncation-{uuid4().hex}.pdf"
            )
            pdf_path.replace(backup_path)
            try:
                staged_path.replace(pdf_path)
            except OSError:
                backup_path.replace(pdf_path)
                raise
            backups.append((pdf_path, backup_path))
    except OSError:
        for pdf_path, backup_path in reversed(backups):
            backup_path.replace(pdf_path)
        raise
    finally:
        for _, _, _, staged_path in prepared:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)

    for _, backup_path in backups:
        backup_path.unlink(missing_ok=True)

    for entry, _, result, _ in prepared:
        entry.update(result.to_dict())
        entry["actual_pages"] = result.retained_page_count
        entry["max_pages"] = max_pages
        entry["render_page_budget"] = render_page_budget
        entry["source_text_present"] = True
        entry["source_text_validation_scope"] = "pre_truncation_pdf"
