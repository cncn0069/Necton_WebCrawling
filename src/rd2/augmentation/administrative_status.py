""""행정처리" 비공개 사유(정보공개법 제9조 1~8호 밖의 절차적 비공개 상태)를
LLM으로 문서에 반영한다.

5~8호(`candidates.py`+`llm_augment.py`)는 정규식으로 후보 span을 먼저 좁힌 뒤
LLM에 보내지만, 이 트랙은 그 방식이 실측(2026-07-16)으로 안 맞는 걸 확인했다
— "결재"/"기안" 같은 키워드가 "경기안성"/"승강기안전공단"/"연결재무제표" 같은
무관한 단어와 충돌하고, 정형 필드("분류번호:" 등) 매칭률 자체가 전체 코퍼스의
1%뿐이었다. 애초에 정규식 전처리가 필요했던 이유는 "문서당 span이 수천 개라
전부 LLM에 못 보내서"였지 "LLM이 못 찾아서"가 아니었으므로, 여기서는 정규식
후보탐지 없이 문서 앞부분 span을 통째로 LLM에 보내고 알아서 고르게 한다.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from rd2.augmentation.administrative_status_data import ADMINISTRATIVE_STATUSES
from rd2.augmentation.llm_augment import _RESPONSE_SCHEMA, _passes_validation

_SYSTEM_PROMPT_TEMPLATE = (
    "너는 한국 공공기관 문서에서 '{title}' 상태를 자연스럽게 나타내는 문구를 "
    "만드는 어시스턴트다. '{title}'란: {description}\n\n"
    "주어진 건 문서 앞부분에 나오는 텍스트 조각(span) 목록이다. 이 중 이 상태를 "
    "자연스럽게 표현할 수 있는 자리가 있으면(예: 날짜·문서분류·결재란처럼 "
    "형식적인 필드이거나, 정책 설명 문장 중 일부를 이 상태를 알리는 문구로 "
    "바꿔써도 어색하지 않은 자리) 1~2개만 골라 치환하라. **적당한 자리가 "
    "없으면 억지로 고르지 말고 selections를 빈 배열로 반환하라** — 모든 문서에 "
    "이 상태가 있어야 하는 게 아니다.\n\n"
    "치환 문구는 원문과 길이가 비슷해야 한다(문서 레이아웃 보존 목적). 실제로 "
    "존재하는 기관·인물·사건을 지칭하지 말고 그럴듯한 가상의 내용으로 작성하라. "
    'JSON으로만 응답하라: {{"selections": [{{"span_id": int, "synthetic": str, '
    '"transformation": str, "reason": str}}]}}. transformation 필드는 절대 '
    "비워두지 말고 항상 채워라 — 영문 스네이크케이스 짧은 카테고리 라벨이다. "
    "reason 필드에는 이 치환이 왜 '{title}' 상태를 나타내는지 사람이 검토할 때 "
    "바로 이해할 수 있게 한국어 1문장으로 설명하라."
)


def build_document_context(annotated_doc: dict[str, Any], *, max_spans: int = 50) -> list[dict[str, Any]]:
    """문서 앞부분부터 span을 span_id/text/page_no로 최대 max_spans개까지 모은다.

    candidates.py의 정규식 매칭과 달리 키워드 필터가 전혀 없다 — 페이지 순서대로
    있는 그대로 가져올 뿐이다. 표지 페이지가 스캔 이미지라 텍스트가 하나도
    없는 경우가 실측상 흔해서(annotated 문서 샘플의 45%), 1페이지로 고정하지
    않고 max_spans에 닿을 때까지 다음 페이지로 계속 넘어간다. boilerplate span도
    포함한다 — 문서 식별용 정형 필드(문서번호·시행일자 등)가 반복 헤더로
    분류돼 있는 경우가 많기 때문이다.
    """
    context: list[dict[str, Any]] = []
    for page in annotated_doc.get("pages", []):
        for span in page["spans"]:
            text = span["cleaned_text"]
            if not text.strip():
                continue
            context.append({"span_id": span["span_id"], "text": span["text"], "page_no": page["page_no"]})
            if len(context) >= max_spans:
                return context
    return context


def build_messages(context: list[dict[str, Any]], category_key: str) -> list[dict[str, str]]:
    status = ADMINISTRATIVE_STATUSES[category_key]
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(title=status.title, description=status.description)
    candidates_json = json.dumps(
        [{"span_id": c["span_id"], "text": c["text"]} for c in context], ensure_ascii=False
    )
    user_prompt = (
        f"상태: {status.title}\n상태 설명: {status.description}\n\n"
        f"문서 앞부분 span 목록:\n{candidates_json}\n\n"
        f"적당한 자리가 있으면 1~2개 골라 치환 결과를 JSON으로 반환하라. 없으면 빈 배열."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def augment_administrative_status(
    context: list[dict[str, Any]],
    category_key: str,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-5.4",
) -> list[dict[str, Any]]:
    """문서 컨텍스트 span 목록을 LLM에 보내 '행정처리' 상태를 나타내는 문구로
    일부를 치환한 결과를 반환한다.

    반환값: [{span_id, page_no, category, original, synthetic, transformation,
    reason}] — LLM이 아무것도 안 골랐거나 컨텍스트에 없는 span_id를 골랐다면
    빈 리스트. `category`는 법정 조항 번호("clause")가 아니라 이 모듈의 8개
    카테고리 코드 중 하나다 — 5~8호와 절대 같은 필드명을 쓰지 않는다.
    """
    if not context:
        return []

    client = client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=build_messages(context, category_key),
        response_format=_RESPONSE_SCHEMA,
    )
    raw = response.choices[0].message.content

    try:
        parsed = json.loads(raw)
        selections = parsed["selections"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []

    context_by_id = {c["span_id"]: c for c in context}
    results: list[dict[str, Any]] = []
    for sel in selections:
        span_id = sel.get("span_id")
        if span_id not in context_by_id:
            continue  # 환각 방지 — 실제 컨텍스트에 없는 span_id는 채택 안 함
        synthetic = sel.get("synthetic")
        if not synthetic:
            continue
        original = context_by_id[span_id]["text"]
        if not _passes_validation(original, synthetic):
            continue
        results.append(
            {
                "span_id": span_id,
                "page_no": context_by_id[span_id].get("page_no"),
                "category": category_key,
                "original": original,
                "synthetic": synthetic,
                "transformation": sel.get("transformation", ""),
                "reason": sel.get("reason", ""),
            }
        )
    return results
