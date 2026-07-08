"""C/S 트랙 합성 문서 생성 (텍스트만 — Genalog 이미지 증강은 후속 단계).

조항 데이터(clause_data.py)를 기반으로 LLM을 호출해 시나리오에 맞는 가상
문서 텍스트를 생성한다. 실제 기밀/민감 문서의 원문을 추출·복원하지 않는다
— clause_data.py의 scenario_prompts는 시나리오 설명일 뿐, 실제 존재하는
문서를 지칭하지 않는다.
"""

from __future__ import annotations

import os
import random

from dotenv import load_dotenv
from openai import OpenAI

from rd2.generators.clause_data import CLAUSES, ClauseDefinition
from rd2.schema.models import Document

load_dotenv()

_SYSTEM_PROMPT = (
    "너는 한국 공공기관의 문서 작성 스타일을 재현하는 어시스턴트다. "
    "실제로 존재하는 기관이나 사건을 지칭하지 말고, 완전히 가상의 기관명·인명·"
    "날짜·금액을 사용해 그럴듯한 공공기관 문서를 작성하라. "
    "한국 관공서 특유의 문서 형식(안건, 개요, 붙임 등)과 어투를 사용하고, "
    "AI가 작성했다는 티가 나는 상투적 설명은 넣지 마라. 문서 본문만 출력하라."
)


def _build_user_prompt(clause: ClauseDefinition, scenario: str) -> str:
    return (
        f"다음 시나리오에 해당하는 가상의 공공기관 내부 문서를 작성하라.\n\n"
        f"분류: {clause.classification.value} (제{clause.clause_no}호 - {clause.title})\n"
        f"조항 설명: {clause.description}\n"
        f"시나리오: {scenario}\n\n"
        f"완전히 가상의 기관명(예: 'OO도청', 'XX연구원' 형태로 실제 기관과 겹치지 않게)과 "
        f"가상의 담당자명, 날짜, 문서번호를 사용하라. 실제 사건이나 실제 기관을 지칭하지 마라."
    )


def generate_clause_document(
    clause_no: str,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-4o-mini",
    scenario_index: int | None = None,
) -> Document:
    """조항 하나에 대해 합성 문서 1건을 생성한다 (텍스트만, Genalog 미적용)."""
    clause = CLAUSES[clause_no]
    if clause.on_hold:
        raise ValueError(f"clause {clause_no} is on hold (RD-2 명시) — 생성 대상 아님")
    if not clause.scenario_prompts:
        raise ValueError(f"clause {clause_no} has no scenario prompts defined")

    idx = scenario_index if scenario_index is not None else random.randrange(len(clause.scenario_prompts))
    scenario = clause.scenario_prompts[idx % len(clause.scenario_prompts)]

    client = client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(clause, scenario)},
        ],
    )
    body_text = response.choices[0].message.content

    return Document(
        title=f"[합성] {scenario}",
        ordering_agency="가상기관(합성)",
        disclosure_status=_disclosure_status_for(clause),
        non_disclosure_reason=f"제{clause.clause_no}호 — {clause.title}",
        subject_category=clause.title,
        body_text=body_text,
        cso_classification=clause.classification,
        cso_sub_clause=clause.clause_no,
        source="synthetic-llm",
        source_url=None,
        doc_type="합성문서",
        is_synthetic=True,
    )


def _disclosure_status_for(clause: ClauseDefinition):
    from rd2.schema.models import DisclosureStatus

    return DisclosureStatus.CLOSED
