"""문서 재구성 파일럿 테스트: data/augmented/llm/의 치환 결과를 원본 PDF에
실제로 그려넣어서 레이아웃이 유지되는지 눈으로 확인한다 (RD-2 "다음 할 일 #2"
사전 검증용, 아직 정식 파이프라인 아니다).

원본 span의 bbox 위치를 흰 사각형으로 지운(redact) 뒤, 같은 위치에 치환
텍스트를 삽입한다. 원본 PDF에 임베드된 폰트는 서브셋이라 새 글자의 글리프가
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
import json
from pathlib import Path

import fitz

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_AUGMENTED_LLM_ROOT = _DATA_ROOT / "augmented" / "llm"
_RECONSTRUCTED_PDF_ROOT = _DATA_ROOT / "augmented" / "reconstructed_pdf"

# 로컬(macOS) 전용 경로 — EC2(Linux)에서는 `apt-get install fonts-nanum` 후
# /usr/share/fonts/truetype/nanum/NanumGothic.ttf 로 바꿀 것
_FALLBACK_FONT_PATH = (
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/"
    "bad9b4bf17cf1669dde54184ba4431c22dcad27b.asset/AssetData/NanumGothic.ttc"
)
_FALLBACK_FONT_NAME = "nanum-fallback"


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


def _extracted_path_for(source_pdf_path: str) -> Path:
    rel = Path(source_pdf_path).relative_to("data")
    return _DATA_ROOT / "extracted" / rel.parent / f"{rel.stem}.json"


def _build_span_index(extracted: dict) -> dict[int, dict]:
    index: dict[int, dict] = {}
    for page in extracted["pages"]:
        for span in page["spans"]:
            index[span["span_id"]] = {**span, "page_no": page["page_no"]}
    return index


def _color_tuple(color_int: int) -> tuple[float, float, float]:
    r = ((color_int >> 16) & 255) / 255
    g = ((color_int >> 8) & 255) / 255
    b = (color_int & 255) / 255
    return (r, g, b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--augmented", required=True, help="data/augmented/llm/.../*.json 경로")
    parser.add_argument(
        "--out", default=None, help="출력 PDF 경로 (기본: data/augmented/reconstructed_pdf/ 밑에 미러링)"
    )
    args = parser.parse_args()

    augmented = json.loads(Path(args.augmented).read_text(encoding="utf-8"))
    source_pdf_path = augmented["source_pdf_path"]
    selections = augmented["selections"]

    extracted = json.loads(_extracted_path_for(source_pdf_path).read_text(encoding="utf-8"))
    span_index = _build_span_index(extracted)

    doc = fitz.open(source_pdf_path)
    touched_pages: set[int] = set()

    for sel in selections:
        span_id = sel["span_id"]
        meta = span_index.get(span_id)
        if meta is None:
            print(f"  SKIP span_id={span_id}: extracted 데이터에서 못 찾음")
            continue

        page = doc[meta["page_no"] - 1]
        rect = fitz.Rect(meta["bbox"])
        color = _color_tuple(meta.get("color", 0))
        fontsize = meta["size"]

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
                fontfile=_FALLBACK_FONT_PATH,
                color=color,
                align=0,
            )
            if residual >= 0:
                break
            size -= 0.5
        if residual < 0:
            print(f"  WARN span_id={span_id}: 1pt까지 줄여도 안 맞음 (텍스트: {text!r})")

        touched_pages.add(meta["page_no"])
        print(
            f"  span_id={span_id} page={meta['page_no']} "
            f"fontsize {fontsize:.1f}->{size:.1f} : {sel['original']!r} -> {text!r}"
        )

    out_path = Path(args.out) if args.out else _default_out_path(Path(args.augmented))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    doc.close()

    print(f"\n총 {len(selections)}개 span 중 {len(touched_pages)}개 페이지 수정")
    print(f"영향받은 페이지: {sorted(touched_pages)}")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
