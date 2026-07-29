"""C/S 트랙 합성 문서 생성 (텍스트만 — Genalog 이미지 증강은 후속 단계).

조항 데이터(clause_data.py)를 기반으로 LLM을 호출해 시나리오에 맞는 가상
문서 텍스트를 생성한다. 실제 기밀/민감 문서의 원문을 추출·복원하지 않는다
— clause_data.py의 scenario_prompts는 시나리오 설명일 뿐, 실제 존재하는
문서를 지칭하지 않는다.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

from rd2.generators.clause_data import CLAUSES, ClauseDefinition
from rd2.generators.doc_templates import ApprovalState, DocTemplateSpec
from rd2.schema.models import Document
from rd2.storage.naming import DOC_TYPE_SYNTHETIC_DOCUMENT

load_dotenv()

# 조항 1~4(candidates.py의 span 탐지 로직이 아예 없는 조항 — 국가안보/진행중
# 수사 등은 애초에 공개문서에서 매칭될 수 없음) 전용 완전 폴백 프롬프트.
# 2026-07-20 사용자 결정(R3): 시나리오 자체는 완전 창작이지만, 기관명과
# 생산일자는 rd2 DB에 실제로 존재하는 (ordering_agency, production_date) 쌍을
# 그대로 보존해야 한다 — "가상기관(합성)" 같은 가짜 이름이나 임의의 날짜를
# 더 이상 쓰지 않는다. 인명·전화번호·금액 등 나머지 세부사항만 가상으로 짓는다.
# generate_clause_document()가 ordering_agency/production_date를 필수 인자로
# 받는 이유가 이것이다(agency_resolver.sample_real_agency_and_date_for_fallback로
# rd2 DB의 실제 쌍 중 하나를 뽑아 전달).
#
# 2026-07-21 사용자 결정: title을 scenario 원문 그대로 쓰던 이전 방식(같은
# scenario_index가 여러 건에 반복되면 title 컬럼이 통째로 동일 문자열이 되는
# 문제 — 사용자 지적)을 버리고, LLM이 시나리오 범주 안에서 구체적이고 서로
# 다른 사건을 스스로 설정해 title/body를 함께 JSON으로 생성하게 한다. 다만
# 7/21 앞선 결정("[합성]" 라벨이 title=파일명 소스에 섞이면 안 된다)의 취지는
# 유지해야 하므로, 프롬프트로 라벨 금지를 지시하고 _strip_synthetic_label로
# 후처리 가드까지 이중으로 건다.
_SYSTEM_PROMPT = (
    "너는 한국 공공기관의 문서 작성 스타일을 재현하는 어시스턴트다. "
    "아래에 실제로 존재하는 기관명과 생산일자가 주어진다 — 이 값들은 그대로 쓰고 "
    "다른 값으로 바꾸지 마라. 다만 인명·전화번호·금액 등 나머지 세부사항은 "
    "여전히 완전히 가상으로 지어내라(실제 사건을 지칭하지 마라). "
    "한국 관공서 특유의 문서 형식(안건, 개요, 붙임 등)과 어투를 사용하고, "
    "AI가 작성했다는 티가 나는 상투적 설명은 넣지 마라.\n\n"
    "주어지는 시나리오는 하나의 큰 범주일 뿐이다 — 그 범주 안에서 구체적이고 "
    "고유한 하나의 사건(장소·배경·경위 등)을 스스로 설정하고, 그 사건에 맞는 "
    "자연스러운 문서 제목을 지어라. 제목에는 '[합성]', '(가상)', 'AI 생성', "
    "'synthetic' 같은 라벨이나 이 문서가 합성·가상이라는 티를 내는 표현을 "
    "절대 넣지 마라 — 실제 관공서 문서 제목처럼 써라. 지정된 JSON 스키마로만 "
    "응답하라."
)

_FALLBACK_RESPONSE_JSON_SCHEMA = {
    "name": "fallback_document",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "문서 제목 — 시나리오 범주 안에서 스스로 설정한 구체적 사건에 "
                    "맞는 자연스러운 제목. 합성/가상 문서임을 암시하는 라벨을 "
                    "포함하지 마라."
                ),
            },
            "body_text": {
                "type": "string",
                "description": "문서 본문",
            },
        },
        "required": ["title", "body_text"],
        "additionalProperties": False,
    },
}

_SYNTHETIC_LABEL_PREFIX_RE = re.compile(r"^\s*[\[(（][^\])）]*[\])）]\s*")


def _strip_synthetic_label(title: str) -> str:
    """LLM이 지시를 무시하고 title 앞에 '[합성]' 류 라벨을 붙였을 때를 대비한
    후처리 가드 — 프롬프트 지시만으로는 100% 보장되지 않으므로 이중으로 건다."""
    stripped = title.strip()
    prev = None
    while prev != stripped:
        prev = stripped
        stripped = _SYNTHETIC_LABEL_PREFIX_RE.sub("", stripped).strip()
    return stripped


def _default_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다 — .env 파일이나 "
            "환경변수로 OpenAI API 키를 설정하세요."
        )
    return OpenAI(api_key=api_key)


# [별표 2](군사기밀 보호법 시행령 제5조제1항)의 등급 설명을 요약한 프롬프트용 라벨.
# 등급 마크(agency_resolver.MILITARY_SECRET_MARK_FILENAMES)와 본문 내용의 심각성이
# 어긋나지 않도록, 같은 등급 값을 여기 프롬프트와 PDF 렌더링 양쪽에 그대로 넘겨야
# 한다(2026-07-21 사용자 결정).
_MILITARY_SECRET_GRADE_PROMPT_LABELS: dict[str, str] = {
    "1급": "Ⅰ급비밀(TOP SECRET) — 노출 시 국가안보에 치명적 손실을 초래할 수준",
    "2급": "Ⅱ급비밀(SECRET) — 노출 시 국가안보에 심각한 손실을 초래할 수준",
    "3급": "Ⅲ급비밀(CONFIDENTIAL) — 노출 시 국가안보에 손실을 초래할 수 있는 수준",
}


def _build_user_prompt(
    clause: ClauseDefinition,
    scenario: str,
    *,
    ordering_agency: str,
    production_date: str,
    military_secret_grade: str | None = None,
    generation_guidance: str | None = None,
) -> str:
    grade_line = ""
    if military_secret_grade:
        grade_label = _MILITARY_SECRET_GRADE_PROMPT_LABELS.get(
            military_secret_grade, military_secret_grade
        )
        grade_line = (
            f"\n이 문서는 군사기밀보호법상 {grade_label}에 해당하는 문서다 — 그 등급에 "
            f"맞는 심각성과 구체성으로 내용을 작성하라(등급이 높을수록 더 치명적이고 "
            f"광범위한 파급효과를 가정하라)."
        )
    guidance_line = ""
    if generation_guidance:
        guidance_line = f"\n\n세부 생성 지침:\n{generation_guidance.strip()}"
    return (
        f"다음 시나리오에 해당하는 공공기관 내부 문서를 작성하라.\n\n"
        f"분류: {clause.classification.value} (제{clause.clause_no}호 - {clause.title})\n"
        f"조항 설명: {clause.description}\n"
        f"시나리오: {scenario}\n"
        f"위 시나리오는 하나의 큰 범주다 — 그 범주 안에서 구체적이고 고유한 하나의 "
        f"사건(장소·배경·경위 등 세부사항)을 스스로 설정해 작성하라. 같은 시나리오로 "
        f"이미 만들어졌을 다른 문서들과 내용이 겹치지 않아야 한다.\n\n"
        f"기관명: {ordering_agency} (실제 존재하는 값 — 그대로 쓰고 바꾸지 마라)\n"
        f"생산일자: {production_date} (실제 존재하는 값 — 그대로 쓰고 바꾸지 마라)\n"
        f"담당자명·전화번호·금액·문서번호는 완전히 가상으로 지어내라. "
        f"실제 사건을 지칭하지 마라."
        f"{grade_line}"
        f"{guidance_line}"
    )


def generate_clause_document(
    clause_no: str,
    *,
    ordering_agency: str,
    production_date: str,
    client: OpenAI | None = None,
    model: str = "gpt-4o-mini",
    scenario_index: int | None = None,
    military_secret_grade: str | None = None,
    generation_guidance: str | None = None,
) -> Document:
    """조항 하나에 대해 합성 문서 1건을 생성한다 (텍스트만, Genalog 미적용).

    ordering_agency/production_date는 rd2 DB에 실제로 존재하는 값이어야 한다
    (agency_resolver.sample_real_agency_and_date_for_fallback로 얻는다) — 이
    함수 자체는 그 값을 검증하지 않고 그대로 신뢰해 프롬프트와 Document에 반영한다.

    military_secret_grade("1급"/"2급"/"3급")가 주어지면 그 등급에 맞는 심각성으로
    본문을 쓰라는 지시를 프롬프트에 추가한다 — 호출자(agency_resolver의
    is_military_secret_agency로 국방부/국가정보원 문서만 판단)가 PDF에 붙일 등급
    마크와 같은 값을 넘겨야 마크와 본문 내용이 어긋나지 않는다.
    """
    clause = CLAUSES[clause_no]
    if clause.on_hold:
        raise ValueError(f"clause {clause_no} is on hold (RD-2 명시) — 생성 대상 아님")
    if not clause.scenario_prompts:
        raise ValueError(f"clause {clause_no} has no scenario prompts defined")

    idx = scenario_index if scenario_index is not None else random.randrange(len(clause.scenario_prompts))
    scenario = clause.scenario_prompts[idx % len(clause.scenario_prompts)]

    client = client or _default_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(
                    clause, scenario, ordering_agency=ordering_agency,
                    production_date=production_date,
                    military_secret_grade=military_secret_grade,
                    generation_guidance=generation_guidance,
                ),
            },
        ],
        response_format={"type": "json_schema", "json_schema": _FALLBACK_RESPONSE_JSON_SCHEMA},
    )
    parsed = json.loads(response.choices[0].message.content)
    title = _strip_synthetic_label(parsed["title"]) or scenario
    body_text = parsed["body_text"]

    return Document(
        title=title,
        ordering_agency=ordering_agency,
        production_date=production_date,
        disclosure_status=_disclosure_status_for(clause),
        non_disclosure_reason=f"제{clause.clause_no}호 — {clause.title}",
        subject_category=clause.title,
        body_text=body_text,
        cso_classification=clause.classification,
        cso_sub_clause=clause.clause_no,
        source="synthetic-llm",
        source_url=None,
        doc_type=DOC_TYPE_SYNTHETIC_DOCUMENT,
        is_synthetic=True,
    )


def _disclosure_status_for(clause: ClauseDefinition):
    from rd2.schema.models import DisclosureStatus

    return DisclosureStatus.CLOSED


# --- PRISM 시드 기반 생성 (D1 우선순위 3번) ---
# _SYSTEM_PROMPT/_build_user_prompt(위)와 반대 방향의 지시를 준다: 저 프롬프트는
# "실제 기관·사건을 지칭하지 말라"고 명시하는데, 여기서는 정확히 그 반대로
# "제공된 실제 기관명·조항·비공개사유·요약을 그대로 보존하라"고 지시한다 — 둘을
# 같은 프롬프트로 겸용하면 시드값이 지워진다(2026-07-14 plan-eng-review에서 확인).


@dataclass(frozen=True)
class SeedInput:
    """PRISM 실제 비공개/부분공개 문서에서 추출한 시드 — 그대로 프롬프트에 반영된다."""

    ordering_agency: str
    clause_no: str
    classification: str  # "C" 또는 "S" (CLAUSES[clause_no].classification.value)
    non_disclosure_reason: str
    content_summary: str  # PRISM의 실제 초록(content_summary 컬럼) — "abstract" 컬럼은 없음
    fiscal_year_hint: int | None = None
    department: str | None = None  # PRISM 실데이터에 있으면 그대로 보존, 없으면 LLM이 채움


@dataclass(frozen=True)
class SeededResult:
    department: str
    unit_task: str
    production_date: str  # YYYY-MM-DD
    body_text: str
    tokens_in: int | None
    tokens_out: int | None


_SYSTEM_PROMPT_SEEDED = (
    "너는 한국 공공기관의 문서 작성 스타일을 재현하는 어시스턴트다. "
    "아래에 실제 존재하는 문서의 기관명·조항·비공개사유·요약이 주어진다 — 이 값들을 "
    "절대 다른 값으로 바꾸거나 가상의 기관명을 새로 지어내지 말고 본문에 그대로 "
    "반영하라. 담당부서가 함께 제공되면 그 값도 기관명과 마찬가지로 그대로 쓰고 "
    "절대 다른 부서명으로 바꾸지 마라. 제공된 요약을 바탕으로, 이 문서의 비공개 "
    "처리가 없었다면 실제로 어떤 내용이었을지 한국 관공서 특유의 문서 형식(안건, "
    "개요, 붙임 등)과 어투로 재구성하라. 담당부서가 제공되지 않았다면 그 값을, "
    "그리고 항상 비어 있는 단위업무·생산일자를 기관명·조항 맥락에 맞는 짧고 "
    "그럴듯한 값으로 채워라. AI가 작성했다는 티가 나는 상투적 설명은 넣지 말고, "
    "지정된 JSON 스키마로만 응답하라."
)

_SEEDED_RESPONSE_JSON_SCHEMA = {
    "name": "seeded_document",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "department": {
                "type": "string",
                "description": "생성부서 — 기관명·조항 맥락에 맞는 짧고 그럴듯한 부서명",
            },
            "unit_task": {
                "type": "string",
                "description": "단위업무 — 짧고 그럴듯한 업무명",
            },
            "production_date": {
                "type": "string",
                "description": "생산일자, YYYY-MM-DD 형식",
            },
            "body_text": {
                "type": "string",
                "description": "재구성된 문서 본문",
            },
        },
        "required": ["department", "unit_task", "production_date", "body_text"],
        "additionalProperties": False,
    },
}


def _build_seeded_user_prompt(seed: SeedInput) -> str:
    date_hint = (
        f"생산일자는 {seed.fiscal_year_hint}년 내의 날짜로 하라."
        if seed.fiscal_year_hint
        else "생산일자는 올해 내의 날짜로 하라."
    )
    department_line = f"담당부서: {seed.department}\n" if seed.department else ""
    department_hint = (
        " 담당부서도 위 값을 그대로 쓰고, 절대 다른 부서명으로 바꾸지 마라."
        if seed.department
        else ""
    )
    return (
        "다음은 실제 존재하는 문서의 정보다. 이 정보를 바탕으로 문서를 재구성하라.\n\n"
        f"기관명: {seed.ordering_agency}\n"
        f"{department_line}"
        f"분류: {seed.classification} (제{seed.clause_no}호)\n"
        f"비공개사유: {seed.non_disclosure_reason}\n"
        f"요약: {seed.content_summary}\n\n"
        f"{date_hint} 기관명은 위 값을 그대로 쓰고, 절대 가상의 기관명으로 바꾸지 마라."
        f"{department_hint}"
    )


def generate_seeded_body(
    seed: SeedInput,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-4o-mini",
) -> SeededResult:
    """PRISM 실제 시드를 바탕으로 빈 필드 보완 + 본문을 하나의 LLM 호출로 생성한다.

    _SYSTEM_PROMPT(위)와 달리 실제 기관명·조항·비공개사유·요약을 보존하도록
    지시한다 — 완전 가상 생성용 프롬프트를 재사용하면 시드값이 지워진다.
    """
    client = client or _default_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT_SEEDED},
            {"role": "user", "content": _build_seeded_user_prompt(seed)},
        ],
        response_format={"type": "json_schema", "json_schema": _SEEDED_RESPONSE_JSON_SCHEMA},
    )
    parsed = json.loads(response.choices[0].message.content)
    usage = response.usage
    return SeededResult(
        department=parsed["department"],
        unit_task=parsed["unit_task"],
        production_date=parsed["production_date"],
        body_text=parsed["body_text"],
        tokens_in=usage.prompt_tokens if usage else None,
        tokens_out=usage.completion_tokens if usage else None,
    )


# --- span 근거 기반 생성 (조항 5/6/7/8, 2026-07-20 사용자 결정 R2) ---
# generate_seeded_body()/generate_anchored_body()는 여전히 DB의 content_summary/
# non_disclosure_reason(메타데이터)를 근거로 쓴다. 실제 운영에서는 메타데이터
# 없이 문서 내용만으로 판단해야 하므로, 학습 데이터 생성도 그 조건을 재현해야
# 한다 — candidates.py의 정규식/키워드 매칭은 그대로 저비용 1차 필터로 남겨두고
# (이미 튜닝된 필터라 LLM으로 교체하지 않는다), 2차 단계인 이 함수는 그렇게
# 골라진 후보 문서의 실제 원문 전체를 근거로 삼는다. matched_span_text는 "왜 이
# 조항인지"의 근거 앵커일 뿐이고, 실제 추출·재구성은 source_document_text
# 전체에서 한다 — 원문에 없는 사실을 지어내지 않는다는 점이 SeedInput 경로
# (요약 한 문단만 보고 창작)와의 핵심 차이다.


def _template_constraint_lines(template: DocTemplateSpec | None) -> str:
    """template이 있으면 그 구조적 제약(수신·결재상태·금지문구)을 프롬프트 문장으로 만든다.

    레이아웃 자체(표·결재선·공개구분)는 pdf_render.py가 DocTemplateSpec만 보고
    결정적으로 그리므로 LLM 출력과 무관하다 — 여기서 주는 제약은 LLM이 담당하는
    좁은 프로즈 슬롯(본문 첫 줄/불릿 문단)이 그 결정적 레이아웃과 모순되는 말을
    쓰지 않도록 하는 소프트 가드일 뿐이고, validate_row()가 최종 방어선이다.
    """
    if template is None:
        return ""

    lines = [f"\n이 문서는 템플릿 {template.template_id} 제약을 지켜야 한다: {template.description}"]
    if template.recipient:
        lines.append(f"- 수신: {template.recipient}")
    if template.approval_state is ApprovalState.PENDING:
        lines.append(
            "- 결재 상태: 아직 결재가 완료되지 않은 내부검토 진행 상태다 — "
            "'결재 완료'나 '최종 승인' 같은 완결 표현을 쓰지 마라."
        )
    elif template.approval_state is ApprovalState.COMPLETE:
        lines.append("- 결재 상태: 결재가 완료된 확정 문서다.")
    if template.forbidden_phrases:
        joined = ", ".join(f"'{p}'" for p in template.forbidden_phrases)
        lines.append(f"- 다음 표현은 절대 쓰지 마라: {joined}")
    return "\n".join(lines)


_SYSTEM_PROMPT_SPAN_SEEDED = (
    "너는 한국 공공기관의 문서 작성 스타일을 재현하는 어시스턴트다. "
    "아래에 실제 존재하는 공공기관 문서의 원문 전체와, 그 문서가 특정 조항에 "
    "해당하는 근거가 되는 부분(근거 문구)이 함께 주어진다. 기관명은 절대 다른 "
    "값으로 바꾸지 말고 그대로 써라. 담당부서가 함께 제공되면 그 값도 그대로 "
    "쓰고 바꾸지 마라. 문서 원문에 실제로 있는 내용(업무 성격·시기·맥락)만 "
    "근거로 삼아 재구성하고, 원문에 없는 사실을 지어내지 마라. 근거 문구는 "
    "왜 이 조항에 해당하는지를 보여주는 단서일 뿐이니 그 부분만 베끼지 말고 "
    "문서 전체 맥락을 반영하라. 담당부서가 제공되지 않았다면 그 값을, 그리고 "
    "항상 비어 있는 단위업무·생산일자를 문서 맥락에 맞는 짧고 그럴듯한 값으로 "
    "채워라. AI가 작성했다는 티가 나는 상투적 설명은 넣지 말고, 지정된 JSON "
    "스키마로만 응답하라."
)


@dataclass(frozen=True)
class SpanSeedInput:
    """candidates.py가 찾은 실제 문서 span에서 뽑은 시드.

    DB의 content_summary/non_disclosure_reason 대신 실제 매칭된 문서 원문
    (source_document_text)을 유일한 근거로 쓴다(R2). matched_span_text는 왜 이
    조항에 해당하는지의 근거 앵커일 뿐, 실제 추출은 원문 전체에서 한다.
    """

    ordering_agency: str
    clause_no: str
    classification: str
    doc_type: str
    source_document_text: str
    matched_span_text: str
    department: str | None = None
    fiscal_year_hint: int | None = None
    template: DocTemplateSpec | None = None


def _build_span_seeded_user_prompt(seed: SpanSeedInput) -> str:
    date_hint = (
        f"생산일자는 {seed.fiscal_year_hint}년 내의 날짜로 하라."
        if seed.fiscal_year_hint
        else "생산일자는 올해 내의 날짜로 하라."
    )
    department_line = f"담당부서: {seed.department}\n" if seed.department else ""
    truncated = seed.source_document_text[:_MAX_ANCHOR_TEXT_CHARS]
    return (
        "다음은 실제 존재하는 문서의 원문과, 그 문서가 아래 조항에 해당하는 "
        "근거가 되는 부분이다. 이 정보를 바탕으로 문서를 재구성하라.\n\n"
        f"기관명: {seed.ordering_agency}\n"
        f"{department_line}"
        f"분류: {seed.classification} (제{seed.clause_no}호)\n"
        f"문서유형: {seed.doc_type}\n"
        f"근거 문구(왜 이 조항에 해당하는지의 단서): {seed.matched_span_text}\n\n"
        f"{date_hint} 기관명은 위 값을 그대로 쓰고, 절대 가상의 기관명으로 바꾸지 마라."
        f"{_template_constraint_lines(seed.template)}\n\n"
        f"--- 문서 원문 (실제 근거 — 여기에 없는 사실을 지어내지 마라) ---\n{truncated}"
    )


def generate_span_seeded_body(
    seed: SpanSeedInput,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-4o-mini",
) -> SeededResult:
    """candidates.py가 찾은 실제 문서 span을 근거로 빈 필드 보완 + 본문을 생성한다.

    응답 스키마는 generate_seeded_body()와 동일(SeededResult) — CSV 기록/PDF
    렌더링 쪽에서 시드 종류와 무관하게 같은 방식으로 소비할 수 있게 한다.
    """
    client = client or _default_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT_SPAN_SEEDED},
            {"role": "user", "content": _build_span_seeded_user_prompt(seed)},
        ],
        response_format={"type": "json_schema", "json_schema": _SEEDED_RESPONSE_JSON_SCHEMA},
    )
    parsed = json.loads(response.choices[0].message.content)
    usage = response.usage
    return SeededResult(
        department=parsed["department"],
        unit_task=parsed["unit_task"],
        production_date=parsed["production_date"],
        body_text=parsed["body_text"],
        tokens_in=usage.prompt_tokens if usage else None,
        tokens_out=usage.completion_tokens if usage else None,
    )


# --- 부분공개/비공개 문서 + 유사 공개문서 앵커링 (TODOS.md D1 최우선 소스) ---
# generate_seeded_body()는 요약(content_summary) 한 문단만 보고 본문을 창작한다.
# 요약이 짧을수록 LLM이 실제 공공기관 문서의 형식(문단 길이, 항목 구성, 붙임
# 방식)을 못 지키고 뭉뚱그린 서술문을 만드는 경향이 있어(2026-07-15 관찰),
# 같은 기관의 실제 공개문서 전문을 "형식 참고용"으로 함께 제공해 구조적
# 사실성을 높인다. 사실 내용(날짜·금액·사건)은 여전히 시드 자체(요약/비공개
# 사유)에서만 가져오도록 프롬프트로 명시한다 — 참고문서를 베끼면 그 문서의
# 실제 사건이 엉뚱한 문서에 섞여 들어가는 사고가 난다.

_MAX_ANCHOR_TEXT_CHARS = 4000

_SYSTEM_PROMPT_ANCHORED = (
    _SYSTEM_PROMPT_SEEDED
    + " 추가로 같은 기관이 실제로 작성한 공개문서 전문이 '형식 참고자료'로 주어진다. "
    "이 참고문서에서는 오직 문체·문단 구성·항목 나누는 방식·형식적 관용구만 "
    "참고하라. 참고문서에 등장하는 구체적 사실(날짜, 금액, 인명, 사건 경위, "
    "고유명사)은 절대 가져오지 마라 — 그건 완전히 다른 사건을 다룬 별개의 "
    "문서이며, 지금 작성하는 문서의 사실은 오직 위에서 제공된 요약과 "
    "비공개사유에서만 가져와야 한다."
)


@dataclass(frozen=True)
class AnchorInput:
    """SeedInput + 같은 기관의 실제 공개문서 본문(형식 참고용, 사실 내용은 미반영)."""

    seed: SeedInput
    anchor_body_text: str


def _build_anchored_user_prompt(anchor: AnchorInput) -> str:
    truncated = anchor.anchor_body_text[:_MAX_ANCHOR_TEXT_CHARS]
    return (
        f"{_build_seeded_user_prompt(anchor.seed)}\n\n"
        f"--- 형식 참고자료 (같은 기관의 실제 공개문서 전문, 사실 내용은 무시하고 "
        f"형식만 참고) ---\n{truncated}"
    )


def generate_anchored_body(
    anchor: AnchorInput,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-4o-mini",
) -> SeededResult:
    """시드(SeedInput)에 같은 기관의 실제 공개문서 전문을 형식 참고자료로 더해 생성한다.

    generate_seeded_body()와 응답 스키마는 동일(SeededResult) — 앵커 유무는
    호출자가 어느 함수를 썼는지로만 구분되고 결과 타입엔 표시되지 않는다.
    """
    client = client or _default_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT_ANCHORED},
            {"role": "user", "content": _build_anchored_user_prompt(anchor)},
        ],
        response_format={"type": "json_schema", "json_schema": _SEEDED_RESPONSE_JSON_SCHEMA},
    )
    parsed = json.loads(response.choices[0].message.content)
    usage = response.usage
    return SeededResult(
        department=parsed["department"],
        unit_task=parsed["unit_task"],
        production_date=parsed["production_date"],
        body_text=parsed["body_text"],
        tokens_in=usage.prompt_tokens if usage else None,
        tokens_out=usage.completion_tokens if usage else None,
    )
