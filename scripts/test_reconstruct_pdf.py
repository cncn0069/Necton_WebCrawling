"""문서 재구성 파일럿 테스트: data/augmented/llm/의 치환 결과를 원본 PDF에
실제로 그려넣어서 레이아웃이 유지되는지 눈으로 확인한다 (RD-2 "다음 할 일 #2"
사전 검증용, 아직 정식 파이프라인 아니다).

후보가 참조하는 물리 line들의 bbox 합집합을 흰 사각형으로 지운(redact) 뒤,
같은 위치에 치환 텍스트를 삽입한다. 원본 PDF에 임베드된 폰트는 서브셋이라 새 글자의 글리프가
없을 수 있어(원문에 없던 한글 등), 대체 폰트로 삽입한다.

MuPDF 내장 CJK 폴백("korea-s"=Droid Sans Fallback)은 원본 문서에 쓰인
한글 서체(HYwulM, MalgunGothic 등)보다 훨씬 두껍고 넓어서 눈에 띄게
튀어보였다(실측 확인) — 대신 나눔고딕을 명시적으로 임베드해서 쓴다.
로컬(macOS)엔 시스템 폰트 에셋으로 이미 있어 그 경로를 쓰지만, 실제
EC2(Linux) 파이프라인으로 옮길 땐 `apt-get install fonts-nanum` 설치 후
`/usr/share/fonts/truetype/nanum/NanumGothic.ttf` 같은 경로로 바꿔야 한다
(OFL 라이선스라 재배포 가능 — 애플 시스템 폰트는 라이선스상 이 용도로
못 씀). 볼드/미디엄 굵기 매칭까지는 아직 안 함(기본 Regular 하나만) —
필요하면 다음 단계에서 다듬는다.

TODO: 지금은 원본 문서 전체를 그대로 저장해서 출력 파일이 큼(예:
410쪽/7.7MB짜리 문서에서 span 3개만 바꿔도 그대로 7.7MB). 나중에 이게
실제로 디스크·전송 부담이 되면, `touched_pages`만 새 fitz 문서에
`new_doc.insert_pdf(doc, from_page=p-1, to_page=p-1)`로 추출해서 저장하는
방식으로 바꿀 것 — 지금 당장은 문제 없어서 보류.

사용 예:
    python scripts/test_reconstruct_pdf.py \
        --augmented "data/augmented/llm/moe/budget_material/100384_2023년 결산보고서_clause8.json"
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import fitz

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.annotate import annotate_document_in_place  # noqa: E402
from rd2.extraction.storage import (  # noqa: E402
    compute_source_sha256,
    extraction_output_path,
    read_json_gz,
)

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_AUGMENTED_LLM_ROOT = _DATA_ROOT / "augmented" / "llm"
_RECONSTRUCTED_PDF_ROOT = _DATA_ROOT / "augmented" / "reconstructed_pdf"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"

# Korean source documents need a font with Korean glyph coverage. Resolve the
# appropriate platform font at runtime, or accept an explicit --font-file.
_DEFAULT_FONT_PATHS = (
    Path(r"C:\Windows\Fonts\malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path(
        "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/"
        "bad9b4bf17cf1669dde54184ba4431c22dcad27b.asset/AssetData/NanumGothic.ttc"
    ),
)
_FALLBACK_FONT_NAME = "rd2-korean-fallback"


def _default_out_path(augmented_path: Path) -> Path:
    """data/augmented/llm/.../foo_clauseN.json → data/augmented/reconstructed_pdf/.../foo_clauseN.pdf
    로 같은 상대구조를 그대로 미러링한다. llm/ 밑이 아닌 경로로 직접 넘기면
    스크래치패드에 저장한다(임시 확인용)."""
    try:
        rel = augmented_path.resolve().relative_to(_AUGMENTED_LLM_ROOT.resolve())
    except ValueError:
        return Path(
            "/private/tmp/claude-501/-Users-heoknoh-Desktop-study-neckton-Necton-WebCrawling/"
            "8d5ab6bf-6a4f-40e2-aa4e-a866f3ec693b/scratchpad/reconstruct_test.pdf"
        )
    return _RECONSTRUCTED_PDF_ROOT / rel.with_suffix(".pdf")


def _source_file_path(source_path: str) -> Path:
    path = Path(source_path)
    return path if path.is_absolute() else _REPO_ROOT / path


def _extracted_path_for(source_path: str) -> Path:
    return extraction_output_path(
        _source_file_path(source_path),
        _DATA_ROOT,
        _DATA_ROOT / "extracted",
    )


def _validate_augmented_candidate_provenance(
    augmented: dict[str, Any],
    *,
    candidates_root: Path = _CANDIDATES_ROOT,
) -> None:
    """Reject augmented selections that are not in the current candidate run."""

    if not isinstance(augmented, dict):
        raise ValueError("augmented 결과의 최상위 값이 JSON 객체가 아닙니다")
    clause_no = augmented.get("clause_no")
    if clause_no not in {"5", "6", "7", "8"}:
        raise ValueError(f"augmented 결과의 clause_no가 잘못되었습니다: {clause_no!r}")

    manifest_path = Path(candidates_root) / "_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"현재 candidate manifest를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise ValueError("현재 candidate manifest가 complete 상태가 아닙니다")

    expected_run_id = manifest.get("run_id")
    expected_rule_version = manifest.get("rule_version")
    if not isinstance(expected_run_id, str) or not expected_run_id:
        raise ValueError("candidate manifest에 run_id가 없습니다")
    if not isinstance(expected_rule_version, str) or not expected_rule_version:
        raise ValueError("candidate manifest에 rule_version이 없습니다")
    if augmented.get("candidate_run_id") != expected_run_id:
        raise ValueError(
            "augmented candidate_run_id가 현재 candidate manifest와 일치하지 않습니다"
        )
    if augmented.get("candidate_rule_version") != expected_rule_version:
        raise ValueError(
            "augmented candidate_rule_version이 현재 candidate manifest와 일치하지 않습니다"
        )

    counts = manifest.get("counts")
    expected_count = counts.get(clause_no) if isinstance(counts, dict) else None
    if not isinstance(expected_count, int) or expected_count < 0:
        raise ValueError(f"candidate manifest에 {clause_no}호 개수가 없습니다")

    candidate_path = Path(candidates_root) / f"clause_{clause_no}.jsonl"
    candidates_by_id: dict[str, dict[str, Any]] = {}
    try:
        with candidate_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                candidate = json.loads(line)
                if not isinstance(candidate, dict):
                    raise ValueError(f"{candidate_path}:{line_no}: JSON 객체가 아닙니다")
                candidate_id = candidate.get("candidate_id")
                if not isinstance(candidate_id, str) or not candidate_id:
                    raise ValueError(f"{candidate_path}:{line_no}: candidate_id가 없습니다")
                if candidate_id in candidates_by_id:
                    raise ValueError(f"{candidate_path}:{line_no}: 중복 candidate_id")
                if candidate.get("run_id") != expected_run_id:
                    raise ValueError(f"{candidate_path}:{line_no}: run_id 불일치")
                if candidate.get("rule_version") != expected_rule_version:
                    raise ValueError(f"{candidate_path}:{line_no}: rule_version 불일치")
                candidates_by_id[candidate_id] = candidate
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"현재 candidate JSONL을 읽을 수 없습니다: {exc}") from exc
    if len(candidates_by_id) != expected_count:
        raise ValueError(
            "candidate JSONL 개수가 manifest와 일치하지 않습니다: "
            f"records={len(candidates_by_id)}, manifest={expected_count}"
        )

    selections = augmented.get("selections")
    if not isinstance(selections, list):
        raise ValueError("augmented selections가 배열이 아닙니다")
    source_path = augmented.get("source_path")
    expected_fields = {
        "extraction_id": "extraction_id",
        "text_sha256": "text_sha256",
        "line_ids": "line_ids",
        "page": "page",
        "original": "text",
    }
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            raise ValueError(f"selections[{index}]가 JSON 객체가 아닙니다")
        candidate_id = selection.get("candidate_id")
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(
                f"selection candidate_id가 현재 후보 run에 없습니다: {candidate_id!r}"
            )
        if candidate.get("source_path") != source_path:
            raise ValueError(f"selection source_path 불일치: {candidate_id}")
        if selection.get("clause") != clause_no:
            raise ValueError(f"selection clause 불일치: {candidate_id}")
        for selection_field, candidate_field in expected_fields.items():
            if selection.get(selection_field) != candidate.get(candidate_field):
                raise ValueError(
                    f"selection {selection_field}가 현재 후보와 불일치: {candidate_id}"
                )


def _build_line_index(extracted: dict) -> dict[object, dict]:
    index: dict[object, dict] = {}
    for page in extracted["pages"]:
        for line in page["lines"]:
            line_id = line["line_id"]
            if line_id in index:
                raise ValueError(f"추출 결과에 중복 line_id가 있습니다: {line_id!r}")
            index[line_id] = {**line, "page": page["page"]}
    return index


def _selection_layout(selection: dict, line_index: dict[object, dict]) -> dict | None:
    line_ids = selection.get("line_ids")
    if not isinstance(line_ids, list) or not line_ids:
        raise ValueError(f"selection에 line_ids가 없습니다: {selection!r}")
    lines = [line_index.get(line_id) for line_id in line_ids]
    if any(line is None for line in lines):
        return None
    resolved = [line for line in lines if line is not None]
    combined_text = ""
    for line in resolved:
        text = str(line.get("cleaned_text") or "")
        needs_space = bool(combined_text) and not combined_text.endswith(" ") and not text.startswith(" ")
        combined_text += (" " if needs_space else "") + text
    original = selection.get("original")
    if combined_text != original:
        raise ValueError(
            f"selection 원문이 현재 line_ids 텍스트와 다릅니다: {selection.get('candidate_id')}"
        )
    expected_text_sha256 = selection.get("text_sha256")
    if (
        not isinstance(expected_text_sha256, str)
        or hashlib.sha256(combined_text.encode("utf-8")).hexdigest() != expected_text_sha256
    ):
        raise ValueError(
            f"selection text_sha256가 일치하지 않습니다: {selection.get('candidate_id')}"
        )
    pages = {line["page"] for line in resolved}
    if len(pages) != 1:
        raise ValueError(f"한 후보의 line_ids가 여러 페이지를 가리킵니다: {line_ids!r}")
    bboxes = [line.get("bbox_pt") for line in resolved]
    if any(not isinstance(bbox, list) or len(bbox) != 4 for bbox in bboxes):
        raise ValueError("PDF 재구성에는 모든 선택 line의 bbox_pt가 필요합니다")
    concrete_bboxes = [bbox for bbox in bboxes if isinstance(bbox, list)]
    style_runs = resolved[0].get("style_runs") or []
    style = style_runs[0] if style_runs else {}
    return {
        "page": resolved[0]["page"],
        "bbox_pt": [
            min(bbox[0] for bbox in concrete_bboxes),
            min(bbox[1] for bbox in concrete_bboxes),
            max(bbox[2] for bbox in concrete_bboxes),
            max(bbox[3] for bbox in concrete_bboxes),
        ],
        "size_pt": float(style.get("size_pt") or 10.0),
        "color": int(style.get("color") or 0),
    }


def _color_tuple(color_int: int) -> tuple[float, float, float]:
    r = ((color_int >> 16) & 255) / 255
    g = ((color_int >> 8) & 255) / 255
    b = (color_int & 255) / 255
    return (r, g, b)


def _resolve_font_path(value: str | None) -> str:
    if value:
        candidate = Path(value)
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(f"Requested font file does not exist: {candidate}")

    for candidate in _DEFAULT_FONT_PATHS:
        if candidate.exists():
            return str(candidate)
    searched = ", ".join(str(path) for path in _DEFAULT_FONT_PATHS)
    raise FileNotFoundError(
        "No Korean fallback font was found. Supply --font-file. Searched: " + searched
    )


def _color(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"Color must be an RGB list of three values, got {value!r}")
    return tuple(float(component) for component in value)  # type: ignore[return-value]


def _align(value: str | int | None) -> int:
    if isinstance(value, int):
        return value
    return {"left": 0, "center": 1, "right": 2}.get(value or "left", 0)


def _text_rect(rect: fitz.Rect, text: str, fontsize: float, vertical_align: str) -> fitz.Rect:
    """Return a padded text box; center multi-line labels in withholding panels."""
    padded = fitz.Rect(rect.x0 + 8, rect.y0 + 8, rect.x1 - 8, rect.y1 - 8)
    if vertical_align != "center":
        return padded
    estimated_height = max(fontsize * 1.55 * (text.count("\n") + 1), fontsize * 1.8)
    top = max(padded.y0, padded.y0 + (padded.height - estimated_height) / 2)
    return fitz.Rect(padded.x0, top, padded.x1, padded.y1)


def _insert_text(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    fontsize: float,
    color: tuple[float, float, float],
    align: int,
    font_path: str,
    vertical_align: str = "top",
) -> float:
    """Insert text, shrinking only when necessary, and return the final size."""
    size = fontsize
    residual = -1.0
    text_box = _text_rect(rect, text, size, vertical_align)
    while size > 1:
        residual = page.insert_textbox(
            text_box,
            text,
            fontsize=size,
            fontname=_FALLBACK_FONT_NAME,
            fontfile=font_path,
            color=color,
            align=align,
        )
        if residual >= 0:
            return size
        size -= 0.5
        text_box = _text_rect(rect, text, size, vertical_align)
    print(f"  WARN text did not fit at 1pt: {text!r}")
    return size


def _apply_panel(page: fitz.Page, panel: dict[str, Any], font_path: str) -> None:
    """Render a withholding panel or an explicit synthetic marker."""
    rect = fitz.Rect(panel["bbox"])
    mode = panel.get("mode", "redact")
    fill = _color(panel.get("fill"), (0.94, 0.94, 0.94))
    border = _color(panel.get("border_color"), (0.45, 0.45, 0.45))

    if mode == "redact":
        page.add_redact_annot(rect, fill=fill)
        page.apply_redactions()
        page.draw_rect(rect, color=border, fill=None, width=float(panel.get("border_width", 0.6)))
    elif mode == "overlay":
        page.draw_rect(rect, color=border, fill=fill, width=float(panel.get("border_width", 0.6)))
    else:
        raise ValueError(f"Unknown panel mode: {mode!r}")

    text = panel.get("label", "")
    if not text:
        return
    _insert_text(
        page,
        rect,
        text,
        fontsize=float(panel.get("font_size", 10)),
        color=_color(panel.get("text_color"), (0.2, 0.2, 0.2)),
        align=_align(panel.get("align", "center")),
        font_path=font_path,
        vertical_align=panel.get("vertical_align", "center"),
    )


def _flatten_document(doc: fitz.Document, dpi: int) -> fitz.Document:
    """Rasterize every page into a font-independent PDF at the requested DPI."""
    flattened = fitz.open()
    for page in doc:
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        target = flattened.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pixmap)
    return flattened


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--augmented", required=True, help="data/augmented/llm/.../*.json 경로")
    parser.add_argument(
        "--out", default=None, help="출력 PDF 경로 (기본: data/augmented/reconstructed_pdf/ 밑에 미러링)"
    )
    parser.add_argument(
        "--font-file",
        default=None,
        help="Korean TrueType/OpenType font path (default: resolve a platform font)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing --out file created by a previous reconstruction run",
    )
    parser.add_argument(
        "--flatten",
        action="store_true",
        help="Rasterize final pages to remove all source-font compatibility dependencies",
    )
    parser.add_argument(
        "--flatten-dpi",
        type=int,
        default=300,
        help="DPI used with --flatten (default: 300)",
    )
    args = parser.parse_args()
    if args.flatten and args.flatten_dpi < 72:
        parser.error("--flatten-dpi must be at least 72")

    augmented = json.loads(Path(args.augmented).read_text(encoding="utf-8"))
    _validate_augmented_candidate_provenance(augmented)
    source_path = augmented["source_path"]
    selections = augmented.get("selections", [])
    redactions = augmented.get("redactions", [])
    overlays = augmented.get("overlays", [])
    font_path = _resolve_font_path(args.font_file)

    extracted = read_json_gz(_extracted_path_for(source_path))
    if extracted.get("schema_version") != 2:
        raise ValueError("재구성에는 extraction schema_version=2가 필요합니다")
    if extracted.get("extraction_id") != augmented.get("extraction_id"):
        raise ValueError("augmented 결과가 현재 extraction_id와 일치하지 않습니다")
    source_file_path = _source_file_path(source_path)
    if compute_source_sha256(source_file_path) != extracted.get("source_sha256"):
        raise ValueError("원본 파일이 canonical 추출 이후 변경되었습니다")
    annotate_document_in_place(extracted)
    line_index = _build_line_index(extracted)

    doc = fitz.open(source_file_path)
    touched_pages: set[int] = set()

    for sel in selections:
        candidate_id = sel.get("candidate_id", "<unknown>")
        if sel.get("extraction_id") != extracted.get("extraction_id"):
            raise ValueError(f"selection extraction_id 불일치: {candidate_id}")
        meta = _selection_layout(sel, line_index)
        if meta is None:
            print(f"  SKIP candidate_id={candidate_id}: extracted 데이터에서 line_ids를 못 찾음")
            continue

        page = doc[meta["page"] - 1]
        rect = fitz.Rect(meta["bbox_pt"])
        color = _color_tuple(meta.get("color", 0))
        fontsize = meta["size_pt"]

        # 1) 원본 span 영역을 흰색으로 지움
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()

        # 2) 같은 위치에 치환 텍스트 삽입 — 원본 크기로 안 들어가면 줄여서 재시도
        text = sel["synthetic"]
        size = fontsize
        residual = -1.0
        while size > 1:
            residual = page.insert_textbox(
                rect,
                text,
                fontsize=size,
                fontname=_FALLBACK_FONT_NAME,
                fontfile=font_path,
                color=color,
                align=0,
            )
            if residual >= 0:
                break
            size -= 0.5
        if residual < 0:
            print(
                f"  WARN candidate_id={candidate_id}: "
                f"1pt까지 줄여도 안 맞음 (텍스트: {text!r})"
            )

        touched_pages.add(meta["page"])
        print(
            f"  candidate_id={candidate_id} page={meta['page']} "
            f"fontsize {fontsize:.1f}->{size:.1f} : {sel['original']!r} -> {text!r}"
        )

    for panel in [*redactions, *overlays]:
        page_no = int(panel["page_no"])
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"Panel references page {page_no}, but the document has {len(doc)} pages")
        _apply_panel(doc[page_no - 1], panel, font_path)
        touched_pages.add(page_no)
        print(f"  panel page={page_no} mode={panel.get('mode', 'redact')}: {panel.get('label', '')!r}")

    print(
        f"Rendered {len(selections)} candidate replacements, {len(redactions)} withholding panels, "
        f"and {len(overlays)} synthetic markers using {font_path}."
    )

    out_path = Path(args.out) if args.out else _default_out_path(Path(args.augmented))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        if not args.overwrite:
            parser.error(f"Output already exists: {out_path}. Re-run with --overwrite to replace it.")
        out_path.unlink()
    output_doc = _flatten_document(doc, args.flatten_dpi) if args.flatten else doc
    output_doc.save(out_path, deflate=True)
    if args.flatten:
        output_doc.close()
        print(f"Final PDF flattened at {args.flatten_dpi} DPI to remove font dependencies.")
    doc.close()

    print(f"\n총 {len(selections)}개 후보 중 {len(touched_pages)}개 페이지 수정")
    print(f"영향받은 페이지: {sorted(touched_pages)}")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
