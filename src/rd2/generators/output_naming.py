"""생성 문서 산출물의 안전하고 추적 가능한 파일명 규칙."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename_stem(raw: object, fallback: str) -> str:
    value = _INVALID_FILENAME_CHARS.sub("_", str(raw or "")).strip(" .")
    if value.lower().endswith(".pdf"):
        value = value[:-4].rstrip(" .")
    return value or fallback


def generation_output_filename(
    *,
    generation_route: str,
    source_filename: str,
    generated_title: str,
) -> str:
    """원문 정렬 문서는 원본명, 새 생성 문서는 생성 제목을 파일명으로 쓴다."""

    raw_stem = (
        Path(source_filename).stem
        if generation_route != "fully_synthetic"
        else generated_title
    )
    fallback = Path(source_filename).stem or "generated-document"
    return f"{safe_filename_stem(raw_stem, fallback)}.pdf"


def requested_output_filename(
    payload: Mapping[str, object],
    index: int,
) -> str | None:
    raw = payload.get("output_filename")
    if raw is None:
        return None
    return f"{safe_filename_stem(raw, f'document-{index:05d}')}.pdf"


def rename_rendered_files(
    rendered: list[dict[str, object]],
    requested_filename: str | None,
) -> None:
    """각 템플릿 폴더의 산출물을 요청 파일명으로 바꾸고 manifest 경로를 갱신한다."""

    if requested_filename is None:
        return
    stem = Path(requested_filename).stem
    counts_by_parent: dict[Path, int] = {}
    for entry in rendered:
        parent = Path(str(entry["pdf"])).parent
        counts_by_parent[parent] = counts_by_parent.get(parent, 0) + 1

    for entry in rendered:
        pdf_path = Path(str(entry["pdf"]))
        html_path = Path(str(entry["html"]))
        suffix = ""
        if counts_by_parent[pdf_path.parent] > 1:
            suffix = f"__{entry['variation_slug']}"
        new_pdf = pdf_path.with_name(f"{stem}{suffix}.pdf")
        new_html = html_path.with_name(f"{stem}{suffix}.html")
        pdf_path.replace(new_pdf)
        html_path.replace(new_html)
        entry["pdf"] = str(new_pdf)
        entry["html"] = str(new_html)
