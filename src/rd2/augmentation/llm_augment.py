"""조항별 후보 span 중 일부를 LLM으로 골라 "기밀도 상승" 문구로 치환한다
(Hard-example 증강). 문서 원본은 건드리지 않고, "어떤 span을 무엇으로
바꿀지"에 대한 결정만 만든다 — 실제 문서 재구성은 다음 단계(오늘 범위 아님).

`generators/generate.py`와 같은 OpenAI 클라이언트 패턴을 재사용하되, 자유
텍스트가 아니라 구조화된 선택 결과(JSON)를 받는다.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from rd2.generators.clause_data import CLAUSES, ClauseDefinition

load_dotenv()

# strict JSON Schema 모드 — 단순 프롬프트 지시만으로는 transformation 필드가
# 종종 빈 값으로 오는 걸 실측(2026-07-16)으로 확인해서, 스키마로 필드 존재 자체를
# 강제한다(필수 필드 누락이면 API가 애초에 스키마 위반 응답을 안 만듦).
_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "span_selections",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "selections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "span_id": {"type": "integer"},
                            "synthetic": {"type": "string"},
                            "transformation": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["span_id", "synthetic", "transformation", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["selections"],
            "additionalProperties": False,
        },
    },
}

_SYSTEM_PROMPT_UPGRADE = (
    "너는 한국 공공기관 문서에서 기밀도가 상승할 만한 문구를 만드는 어시스턴트다. "
    "주어진 텍스트 조각(후보) 목록 중 일부를 골라, 정보공개법 제9조 해당 조항 "
    "내용에 부합하도록 바꿔써라.\n\n"
    "**중요 — 반드시 '더 일반화/희석'이 아니라 '더 구체적이고 민감하게' 바꿔야 한다.** "
    "이미 공개된 수치·명칭을 애매한 표현으로 바꾸는 건 실패다. 대신 다음과 같이 "
    "'공개된 정보'를 '내부에서만 알 수 있는 정보'로 재구성하라: 협상 중 비공개 "
    "기준가, 사전(비공개) 감정평가액, 내부 검토 단계의 대안, 낙찰 예정가 추정치, "
    "공개 전 계획 등.\n\n"
    "예시:\n"
    '원문: "총 사업비 5억원" (공개된 예산액)\n'
    '치환: "협상 기준액 5.3억원" (아직 공개 안 된 내부 협상 마지노선)\n'
    "→ 숫자는 비슷한 자릿수를 유지하되, '이미 확정되어 공개된 사실'을 "
    "'아직 확정 전이거나 내부에서만 아는 사실'로 바꿔야 기밀도가 실제로 올라간다.\n\n"
    "각 치환 문구는 원문과 길이가 비슷해야 한다(문서 레이아웃 보존 목적). "
    "실제로 존재하는 기관·인물·사건을 지칭하지 말고 그럴듯한 가상의 내용으로 작성하라. "
    'JSON으로만 응답하라: {"selections": [{"span_id": int, "synthetic": str, '
    '"transformation": str, "reason": str}]}. transformation 필드는 절대 비워두지 말고 '
    '항상 채워라 — 영문 스네이크케이스 짧은 카테고리 라벨이다(예: '
    '"contract_negotiation_info", "pre_disclosure_appraisal", "internal_bid_estimate"). '
    "reason 필드에는 이 치환이 해당 조항 기준으로 왜 기밀도를 높이는지 사람이 검토할 때 "
    "바로 이해할 수 있게 한국어 1문장으로 설명하라(예: \"협상 중인 비공개 기준가는 "
    "낙찰 전 유출되면 입찰 공정성을 해칠 수 있어 5호 내부검토 정보에 해당\")."
)

_SYSTEM_PROMPT_CLAUSE_7 = (
    _SYSTEM_PROMPT_UPGRADE
    + "\n\n**7호(법인·경영상 비밀) 전용 주의사항**: 원본 문서는 정부기관의 예산·결산·"
    "정책 보고서다. 문서 맥락과 무관한 별개의 가상 민간기업 서사(예: '가상 A사의 "
    "수주목표', '거래처 B사 매출')를 새로 지어내면 안 된다 — 그런 식으로 소재 자체를 "
    "바꾸면 원문과 이질감이 커서 실제 문서에 삽입했을 때 부자연스럽다. 대신 그 예산·"
    "계약 항목이 원래 다루는 공공계약 상대방(수탁기관·낙찰업체·용역업체 등)의 "
    "관점에서, 아직 공개되지 않은 원가·입찰·협상 정보로 재구성하라. 문서의 주체"
    "(발주기관)와 대상(그 항목이 가리키는 계약상대방)은 원문 그대로 유지하고, 그 "
    "안에서만 알 수 있는 영업비밀(원가 구조, 협상 마지노선, 입찰 전략 등)을 채워넣는 "
    "방식이어야 한다."
)

_SYSTEM_PROMPT_CLAUSE_6 = (
    "너는 한국 공공기관 문서에서 개인정보 노출 사례를 재현하는 어시스턴트다. "
    "주어진 텍스트 조각(후보) 목록은 담당자·문의처 등 자리표시 성격의 문구다. "
    "이 중 일부를 골라, span 전체를 통째로 완전히 가상의 개인정보로 교체하라. "
    "원본 텍스트의 일부(예: 실제 담당자명)를 그대로 남겨두고 정보만 덧붙이면 안 된다 "
    "— 반드시 span 전체를 새로운 가상의 내용으로 완전히 바꿔써라.\n\n"
    "**법적 근거(개인정보 보호법 제2조): 개인정보는 그 정보만으로 특정 개인을 "
    "알아볼 수 있는 것뿐 아니라, 다른 정보와 쉽게 결합해 알아볼 수 있는 것도 "
    "포함한다 — 예를 들어 '성명+소속+연락처' 조합은 그 자체로 개인정보에 해당한다. "
    "따라서 주민등록번호가 꼭 있어야 하는 게 아니다.** span 주변에 이미 "
    "'담당자:', '문의:' 같은 맥락(소속·역할)이 있다면, 그 span 자리에는 "
    "**가상의 이름만** 넣어도(주변 맥락과 결합해) 개인정보 요건을 충분히 "
    "충족한다.\n\n"
    "**길이 제약이 중요하므로(문서 레이아웃 보존 목적), 원문 길이에 맞춰 어떤 "
    "종류·형식의 개인정보를 넣을지 골라라**:\n"
    '- 원문이 짧으면(10자 이하): 가상의 이름만(예: "김지훈", 2~4자) — 주변 '
    '맥락과 결합해 식별 가능하므로 이것만으로 충분하다.\n'
    '- 원문이 길면(15자 이상): 이름+연락처, 이름+주민등록번호 등 더 구체적인 '
    '조합(예: "이민준(830512-1234567)", 레이블 없이 압축된 형태)을 써도 된다.\n'
    "치환 문구 길이는 항상 원문과 비슷해야 한다 — 원문보다 몇 배 길어지면 안 된다.\n\n"
    'JSON으로만 응답하라: {"selections": [{"span_id": int, "synthetic": str, '
    '"transformation": str, "reason": str}]}. transformation은 항상 '
    '"fabricated_personal_info"로 고정하라. reason 필드에는 이 span이 왜 개인정보에 '
    "해당하는지(예: 주변 맥락과 결합해 특정 개인 식별 가능 여부) 한국어 1문장으로 설명하라."
)


def build_user_prompt(candidates_for_doc: list[dict[str, Any]], clause: ClauseDefinition) -> str:
    candidates_json = json.dumps(
        [{"span_id": c["span_id"], "text": c["text"]} for c in candidates_for_doc],
        ensure_ascii=False,
    )
    return (
        f"조항: 제{clause.clause_no}호 — {clause.title}\n"
        f"조항 설명: {clause.description}\n\n"
        f"후보 목록:\n{candidates_json}\n\n"
        f"이 중 2~6개를 골라 치환 결과를 JSON으로 반환하라."
    )


def build_messages(candidates_for_doc: list[dict[str, Any]], clause_no: str) -> list[dict[str, str]]:
    clause = CLAUSES[clause_no]
    if clause_no == "6":
        system_prompt = _SYSTEM_PROMPT_CLAUSE_6
    elif clause_no == "7":
        system_prompt = _SYSTEM_PROMPT_CLAUSE_7
    else:
        system_prompt = _SYSTEM_PROMPT_UPGRADE
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_user_prompt(candidates_for_doc, clause)},
    ]


_MIN_LENGTH_RATIO = 0.5
_MAX_LENGTH_RATIO = 1.5


def _passes_validation(original: str, synthetic: str) -> bool:
    """길이 비율(레이아웃 보존)과 원문 잔존 여부(실명 등 식별정보 방치 방지)를
    코드로 검증한다. "실제로 기밀도가 올라갔는지"는 로직으로 판별 불가능해서
    검증하지 않음 — 그건 표본 육안 검수로만 확인 가능."""
    if not synthetic.strip() or not original.strip():
        return False
    ratio = len(synthetic) / len(original)
    if ratio > _MAX_LENGTH_RATIO or ratio < _MIN_LENGTH_RATIO:
        return False
    if original.strip() in synthetic:
        return False  # 원문을 그대로 두고 뒤에 덧붙이기만 한 경우(예: 실명+가짜번호) 탈락
    return True


def augment_document(
    candidates_for_doc: list[dict[str, Any]],
    clause_no: str,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-5.4",
) -> list[dict[str, Any]]:
    """문서 1개의 후보 span 목록을 LLM에 보내 일부를 치환한 결과를 반환한다.

    반환값: [{span_id, page_no, original, synthetic, transformation, reason}] —
    LLM이 하나도 고르지 않았거나 응답이 후보에 없는 span_id만 골랐다면 빈 리스트를
    반환한다. page_no는 candidates jsonl(추출 단계)에 이미 있던 값을 그대로 실어
    보낸다 — 검토할 때 몇 쪽인지 바로 알 수 있게.
    """
    if not candidates_for_doc:
        return []

    client = client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=build_messages(candidates_for_doc, clause_no),
        response_format=_RESPONSE_SCHEMA,
    )
    raw = response.choices[0].message.content

    try:
        parsed = json.loads(raw)
        selections = parsed["selections"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []

    candidates_by_id = {c["span_id"]: c for c in candidates_for_doc}
    results: list[dict[str, Any]] = []
    for sel in selections:
        span_id = sel.get("span_id")
        if span_id not in candidates_by_id:
            continue  # 환각 방지 — 실제 후보 목록에 없는 span_id는 채택 안 함
        synthetic = sel.get("synthetic")
        if not synthetic:
            continue
        original = candidates_by_id[span_id]["text"]  # LLM이 다시 쓰지 않고 원본에서 그대로 가져옴
        if not _passes_validation(original, synthetic):
            continue
        results.append(
            {
                "span_id": span_id,
                "page_no": candidates_by_id[span_id].get("page_no"),
                "clause": clause_no,  # 파일명(_clauseN)에만 의존하지 않고 selection 자체로도 조항을 알 수 있게
                "original": original,
                "synthetic": synthetic,
                "transformation": sel.get("transformation", ""),
                "reason": sel.get("reason", ""),
            }
        )
    return results
