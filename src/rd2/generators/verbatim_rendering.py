"""이미 완성된 문서를 템플릿 합성 없이 그대로 PDF로 낸다.

``render_official_document_variations``는 생성기가 만든 **본문**을 받아 가상
기관 정체성·레터헤드·결재선을 붙여 공문 한 통을 조립한다. 다른 route는 실제로
본문만 만들므로 그 전제가 맞다.

``mask_restoration``만 다르다. 이 route의 산출물은 부분공개 원문 그 자체이고,
기관명 행·수신란·시행번호·결재선 푸터가 **이미 들어 있다.** 템플릿에 부으면
레터헤드가 두 번 생기고, 템플릿 context에 자리가 없는 원문 푸터 block은 아예
빠져 렌더 검증이 ``missing source text``로 떨어진다(실측: 부분공개 원문 2건 중
2건).

그래서 조립하지 않는다. block을 순서대로 놓고 A4로 인쇄할 뿐이다. 대신
"모든 원문 글자가 PDF에 있어야 한다"는 검증은 그대로 유지한다 — 렌더 방식이
바뀌어도 그 보장은 같아야 한다.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from rd2.generators.official_document_rendering import (
    _normalized_pdf_text,
    _normalized_required_text,
    _page_count,
)

# ``official_document_rendering``이 weasyprint import 전에 처리하는 MSYS2/ssl
# 문제를 그대로 물려받는다 — 위 import가 이미 그 준비를 끝낸 뒤에만 아래가
# 성립하므로 순서를 바꾸지 않는다.
from weasyprint import HTML  # noqa: E402

VERBATIM_SLUG = "00_verbatim"

#: 원문 서식을 흉내 내지 않는다. 결재문서를 예쁘게 재현하는 것이 목적이 아니라
#: 원문 글자를 그대로 담은 인쇄물을 만드는 것이 목적이다 — 꾸미기 시작하면
#: 어느 것이 원문이고 어느 것이 렌더러가 넣은 것인지 구분이 사라진다.
_CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: "Malgun Gothic", "Noto Sans KR", sans-serif;
       font-size: 10.5pt; line-height: 1.65; color: #111; }
h1 { font-size: 14pt; margin: 0 0 14pt; padding-bottom: 8pt;
     border-bottom: 1.5pt solid #222; }
.block { margin: 0 0 9pt; white-space: pre-wrap; word-break: break-all; }
"""


def render_verbatim_document(
    document: Any,
    output_dir: Path,
    *,
    required_source_texts: tuple[str, ...],
) -> dict[str, object]:
    """block을 그대로 인쇄하고, 원문 글자 누락만 검사한다."""

    # ``generated_document_pipeline``이 이 모듈을 import하므로 최상위에서
    # 되받으면 순환이 된다. 평탄화 규칙 하나만 빌려 쓰는 것이라 호출 시점에
    # 가져온다.
    from rd2.generators.generated_document_pipeline import blocks_to_body_text

    target_dir = output_dir / VERBATIM_SLUG
    target_dir.mkdir(parents=True, exist_ok=True)
    html_path = target_dir / f"{VERBATIM_SLUG}.html"
    pdf_path = target_dir / f"{VERBATIM_SLUG}.pdf"

    # 이 모듈이 보는 block은 렌더러 쪽 재선언 계약이라 ``render_text``가 없다.
    # 평탄화 규칙은 ``blocks_to_body_text``가 이미 갖고 있으므로 block 하나짜리
    # 리스트로 호출해 그 규칙을 그대로 쓴다 — 여기서 다시 쓰면 두 곳이 갈린다.
    body = "\n".join(
        f'<div class="block">{escape(blocks_to_body_text([block]))}</div>'
        for block in document.blocks
    )
    html = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(document.title)}</title><style>{_CSS}</style>"
        f"</head><body><h1>{escape(document.title)}</h1>{body}</body></html>"
    )
    html_path.write_text(html, encoding="utf-8")
    HTML(string=html).write_pdf(pdf_path)

    pdf_text = _normalized_pdf_text(pdf_path)
    missing = [
        value
        for value in required_source_texts
        if value and _normalized_required_text(value) not in pdf_text
    ]
    return {
        "template": VERBATIM_SLUG,
        "variation": VERBATIM_SLUG,
        "html": str(html_path),
        "pdf": str(pdf_path),
        "pages": _page_count(pdf_path),
        "status": "ok" if not missing else "rejected",
        "source_text_present": not missing,
        "failure_reasons": (
            [] if not missing else ["missing source text " + ", ".join(missing)]
        ),
    }
