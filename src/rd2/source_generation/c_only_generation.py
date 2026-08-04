"""C 전용 생성 루트: 비공개/기밀 문서를 별도로 생성하기 위한 최소 구현."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rd2.generators.agency_resolver import (
    MILITARY_SECRET_GRADE_LABELS,
    is_military_secret_agency,
)
from rd2.generators.clause_data import CLAUSES
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SubclauseKey,
    SUBCLAUSE_DEFINITIONS,
)
from rd2.source_generation.contracts import GeneratedDocumentIR, ParagraphBlock


@dataclass(frozen=True)
class COnlyGenerationRequest:
    title: str
    body_text: str
    document_type: str = "비공개/기밀 문서"
    whitelist_agency: str | None = None
    scenario: str | None = None
    article_range: str | None = None
    document_form: str | None = None
    clause_no: ClauseNumber | None = None
    subclause_key: SubclauseKey | None = None


@dataclass(frozen=True)
class COnlyGenerationResult:
    document: GeneratedDocumentIR
    classification: Literal["C"] = "C"


def build_c_only_document(request: COnlyGenerationRequest) -> COnlyGenerationResult:
    """C 분류에 맞는 문서를 생성한다.

    요청된 화이트리스트 기관·시나리오·문서형식 정보를 반영해, C 생성기용
    프롬프트 지시문을 바탕으로 문서를 구성한다.
    """

    body = request.body_text.strip()
    if not body:
        body = "비공개 사유에 따라 기밀 문서로 처리된 자료입니다."

    prompt_text = build_c_prompt(request)
    document_text = f"{prompt_text}\n\n{body}"

    document = GeneratedDocumentIR(
        title=request.title.strip() or "비공개/기밀 문서",
        blocks=(
            ParagraphBlock(block_id="p1", text=document_text),
        ),
    )
    return COnlyGenerationResult(document=document)


def _grade_hint(request: COnlyGenerationRequest) -> str:
    """군사기밀 등급 또는 대외비 표기를 위한 프롬프트 힌트."""

    if request.whitelist_agency and is_military_secret_agency(request.whitelist_agency):
        # document_type이 '군사상 비밀 1급' 등 형식일 때 등급을 추출
        if request.document_type and "군사상 비밀" in request.document_type:
            parts = request.document_type.split()
            for part in parts:
                if part in MILITARY_SECRET_GRADE_LABELS:
                    return f"- 기밀 등급: {MILITARY_SECRET_GRADE_LABELS[part]}"
        return "- 기밀 등급: Ⅰ급/Ⅱ급/Ⅲ급 중 하나를 선택하여 작성"
    return "- 기밀 등급: 대외비"


def build_c_prompt(request: COnlyGenerationRequest) -> str:
    """요청된 정보를 반영한 C 생성기 프롬프트 지시문을 만든다."""

    agency = request.whitelist_agency or "미지정 기관"
    scenario = request.scenario or "미지정 시나리오"
    document_form = request.document_form or "공공기관 표준 문서형식"

    clause_line = ""
    subclause_line = ""
    scenario_line = ""
    grade_line = _grade_hint(request)

    if request.clause_no is not None:
        clause = CLAUSES[request.clause_no.value]
        clause_line = (
            "\n[대상 법조문]\n"
            f"- 정보공개법 {request.clause_no.value}호: {clause.title}\n"
            f"- 설명: {clause.description}\n"
        )
        if clause.scenario_prompts:
            scenario_line = "\n[세부 시나리오]\n- " + clause.scenario_prompts[0] + "\n"
        if request.subclause_key is not None:
            definition = SUBCLAUSE_DEFINITIONS[request.subclause_key].definition
            label = SUBCLAUSE_DEFINITIONS[request.subclause_key].label
            subclause_line = (
                f"- 세부유형: {label}\n"
                f"- 정의: {definition}\n"
            )

    return f"""당신은 {agency}에 종사하고 있습니다.
{agency}의 상황에서 {scenario}에 해당하는 정보공개법 관련 기밀 정보를 담은 문서를 작성하고 있습니다.{clause_line}{subclause_line}{scenario_line}{grade_line}
문서의 형식은 [{document_form}]를 따릅니다.

[문서형식]
1. 문체 및 종결어미:
   - 철저히 개조식(Bullet points) 문체로 작성하세요.
   - 문장의 끝은 반드시 명사형 종결("~음", "~슴", "~임", "~함", "~축소", "~확대") 또는 "~것"으로 끝맺으세요.
   - 미사여구나 감정적 표현, 근거 없는 수식어는 모두 제거하고 수치와 사실 중심으로 간결하게 작성하세요.

2. 공공기관 표준 기호 체계 준수:
   - 다음 순서의 기호 체계를 지켜 단락과 하위 항목을 구성하세요.
     □ (대항목 - 현황, 문제점, 개선방안 등)
       ○ (중항목 - 핵심 내용)
         - (소항목 - 세부 사실 및 근거)
           ※ (참고/주의사항 - 보충 설명)

3. 확인 사항:
   - 화이트리스트 기관, 기관별 시나리오, 대상 조항 분포를 확인하고 문서 내용에 반영하세요.
   - 제9조 항목과 문서 내용의 연관성을 명확히 드러내세요.
"""
