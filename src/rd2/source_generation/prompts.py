"""2-pass 생성과 relevance 선택의 versioned prompt bundle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from string import Template
from typing import Type

from pydantic import BaseModel

from rd2.administrative_status import ADMIN_STATUS_TEXT_POLICIES
from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.source_generation.classification_taxonomy import (
    TAXONOMY_VERSION,
    render_taxonomy_guidance,
)
from rd2.source_generation.contracts import (
    Pass1Result,
    Pass2Assessment,
    RelevanceSelectionResponse,
)
from rd2.source_generation.document_select import SelectionConfig

PROMPT_BUNDLE_VERSION = "source-generation-prompts-2026-07-28-v10"

TAXONOMY_GUIDANCE = render_taxonomy_guidance()
ADMINISTRATIVE_STATUS_GUIDANCE = "\n".join(
    (
        "행정상태 taxonomy(법적 정보공개법 조항과 독립적으로 판정):",
        *(
            f"- {status.value}: {policy.detail}"
            for status, policy in ADMIN_STATUS_TEXT_POLICIES.items()
        ),
        (
            "행정상태 문구만으로 정보공개법 제9조 조항을 추정하지 않는다. "
            "법적 근거가 없으면 classification은 O, clause_no와 "
            "subclause_key는 null로 반환한다."
        ),
        (
            "본문에 상태명이 직접 쓰이지 않았더라도 문서의 상황과 문맥에서 "
            "행정상태를 판단할 수 있으면 administrative_statuses에 상태별 "
            "finding과 본문에 글자 그대로 존재하는 evidence_spans를 반환한다. "
            "최종 민감 분류는 코드가 법적 classification과 행정상태를 합쳐 "
            "effective_classification으로 계산한다."
        ),
    )
)

RELEVANCE_SYSTEM_PROMPT = """\
당신은 대한민국 공공문서 입력 선택기다.
제공된 것은 86쪽 이상 문서의 앞부분뿐이다. 아래 taxonomy의 문서유형과
정보공개법 제9조 세부조항 판단에 가장 유용한 block을 고른다.
선택하지 않은 block은 이후 단계에서 영구히 보이지 않는다. 따라서 특정
세부조항을 직접 지지하는 구체적 사실(평가기준·배점·예정가격, 개인 식별정보,
보안 취약점, 원가·납품단가, 협상조건, 감사 지적사항 등)이 있는 block을
표지·목차·인사말·일반 현황 서술보다 우선한다.
입력에 실제 존재하는 block ID만 반환하고, 내용을 생성·수정하거나 보이지 않는
뒷부분을 추측하지 않는다.
"""

RELEVANCE_USER_TEMPLATE = """\
다음 후보 block에서 최대 $max_selected_blocks개를 선택하라.

$source_blocks
"""

PASS1_SYSTEM_PROMPT = """\
당신은 대한민국 공공문서를 분석하고 학습용 불완전 문서를 만드는 생성기다.
먼저 원문의 semantic document type, C/S/O, 정보공개법 제9조 호와 세부조항을
분류한다. 이어서 source evidence 수준에 맞는 generation route를 선택한다.
C/S 원문은 source_aligned를 선택하고 그 분류를 생성 목표로 유지한다. O 원문은
source label을 O로 보존하면서 span_seeded, anchored,
administrative_augmented, fully_synthetic 중 실제 입력 근거에 맞는 route를
선택한다. 제공된 counterfactual C/S 목표는 제안값이며,
원문에 더 적합한 유효한 목표가 있으면 변경할 수 있다. 기존 템플릿 이름이나 규칙
기반 추론을 사용하지 않는다.

route 규칙:
- source_aligned: C/S source + direct_legal_evidence +
  generation_mode=source_aligned 조합이다. 명시적 조항·비공개 사유를 지지하는
  source evidence가 필요하고 target은 source 분류와 일치해야 한다.
- span_seeded: O source + direct_sensitive_span +
  generation_mode=counterfactual 조합이다. 목표를 직접 지지하는 source span이
  필요하며 제6호에는 사용하지 않는다.
- [SENSITIVE SEED]는 source span이 아니다. SOURCE DOCUMENT 안에 직접 민감 span이
  없고 공개 업무 맥락과 별도 sensitive seed만 있으면 반드시 anchored를 선택한다.
- anchored: O source + contextual_anchor_only +
  generation_mode=counterfactual 조합이다. 공개 원문의 업무 맥락 evidence와
  별도로 제공된 sensitive seed가 모두 필요하다.
- administrative_augmented: 법적 조항 없는 S 목표에 행정상태가 지정됐을 때
  O source + contextual_anchor_only + generation_mode=counterfactual 조합으로
  공개 원문의 업무 맥락은 유지하고 해당 행정상황이 자연스럽게 드러나게 한다.
  suggested_target의 clause_no·subclause_key가 null이고 administrative_statuses가
  있으면 반드시 이 route를 선택한다. legal clause를 새로 붙이거나 anchored로
  변경하지 않으며 sensitive seed도 요구하지 않는다.
- fully_synthetic: O source + no_usable_public_source +
  generation_mode=counterfactual 조합이다. 사용할 source evidence가 없을 때만
  선택하고 원문 내용을 생성 근거로 쓰지 않는다.

생성본은 자연스럽지만 일부 정보가 빠진 공문서여야 하며, paragraph, bullet_list,
key_value, table, attachment_reference block만 사용한다. source classification,
generation target, generated IR을 절대 같은 필드로 합치지 않는다.
generated_document는 최종 generation target을 실제 본문 내용으로 구현해야 한다.
독립 채점자가 generation target이나 생성 이유를 보지 않고 GeneratedDocumentIR만
읽어도 목표 classification·clause·subclause를 판단할 수 있을 만큼 구체적인
사실관계와 문맥을 포함한다. anchored route에서는 공개 원문의 업무 맥락을 문서
배경으로만 사용하고, [SENSITIVE SEED]의 상황을 본문의 핵심 안건·검토 내용·표·
첨부 참조 등에 자연스럽게 반영한다. source의 공개 내용만 요약해서는 안 된다.
문서가 무엇을 포함하거나 다룬다고 소개하지 말고 그 내용을 직접 작성한다.
"본 문서는 ~을 포함한다", "~을 다룬다", "~에 관한 보고서다", "~한 상황이다"처럼
내용의 존재만 말하는 문장은 사용하지 않는다. [SENSITIVE SEED]에 사건번호·날짜·
금액·점수·식별자·항목이 있으면 빠뜨리지 말고 본문 셀과 문장에 실제 값으로 쓴다.
가상 데이터라는 사실은 provenance에서 관리하므로 generated_document 본문에는
"합성", "가상", "예시"라는 표지를 반복하지 않는다. 최소 3개의 구체적 사실과,
입력에 비교 가능한 복수 항목이 있으면 table 또는 key_value block을 포함한다.
generation target의 administrative_statuses는 변경하거나 제거하지 않는다.
행정상태가 지정되면 generation plan의 writing_instruction에 따라 상태를 본문의
상황과 문맥으로 드러낸다. 상태명이나 정답용 고정 문구를 억지로 삽입하거나
key_value·별도 메타데이터처럼 나열하지 않는다. 지정되지 않은 행정상태는 만들지 않는다.
"""

PASS1_USER_TEMPLATE = """\
[GENERATION PLAN]
$generation_plan

[ASSESSMENT SCOPE]
$assessment_scope

[SENSITIVE SEED]
$sensitive_seed

[SOURCE DOCUMENT]
$source_document
"""

PASS2_SYSTEM_PROMPT = """\
당신은 독립 채점자다. 생성기의 분류, 목표, 이유, evidence를 볼 수 없으며
GeneratedDocumentIR만 처음 보는 것처럼 평가한다. 생성본의 semantic document
type, C/S/O, 정보공개법 제9조 호·세부조항을 독립 예측하고, 판단 근거가 된
문장을 evidence span으로 반환한다. 근거가 없으면 O로 판정하며 생성기의 의도를
추측하지 않는다.

evidence span은 실제 block ID와 그 block에 **글자 그대로 존재하는 인용문**을
담는다. 문자 위치는 시스템이 직접 찾으므로 세거나 계산하지 않는다. 요약하거나
바꿔 쓰지 말고 원문 그대로 복사하며, 같은 block에 두 번 이상 나오는 짧은
문구 대신 그 block에서 한 번만 나오는 길이의 인용문을 고른다.

출력 계약 규칙:
- document_type=other일 때는 other_document_type에 구체적인 유형명을 쓰고,
  other가 아니면 other_document_type=null로 반환한다.
- classification=O이면 clause_no=null, subclause_key=null로 반환한다.
- classification=C/S이면 해당 분류와 맞는 clause_no·subclause_key 조합 및
  최소 1개의 정확한 evidence span이 반드시 필요하다.
- administrative_statuses의 상태는 중복하지 않고 각 finding마다 실제 본문에
  존재하는 evidence span을 반환한다.

반환하는 clause_no와 subclause_key는 아래 taxonomy의 같은 조항에 속하는
조합이어야 한다. 각 세부조항의 판정 정의와 포함·제외 기준, 경계 규칙을
그대로 적용하고, 라벨의 낱말이 겹친다는 이유로 세부조항을 고르지 않는다.

행정상태는 법적 조항과 별개의 축이다. 결재 진행 중, 초안, 내부 검토 중 등의
상태 표시를 법적 S 또는 제5호의 근거로 사용하지 않는다. 반대로 문서에 상태
문구가 명시되어 있으면 법적 classification이 O여도 해당 administrative_status
finding을 evidence와 함께 반환한다.
"""

PASS2_USER_TEMPLATE = """\
다음 GeneratedDocumentIR만 독립적으로 채점하라.

$generated_document_ir
"""


@dataclass(frozen=True)
class PromptDefinition:
    name: str
    system_prompt: str
    user_template: str
    response_model: Type[BaseModel]

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "user_template": self.user_template,
            "response_schema": self.response_model.model_json_schema(),
        }


@dataclass(frozen=True)
class PromptBundle:
    version: str
    taxonomy_version: str
    selection_config: SelectionConfig
    definitions: tuple[PromptDefinition, ...]

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "taxonomy_version": self.taxonomy_version,
            "selection_config": self.selection_config.fingerprint_payload(),
            "prompts": [
                definition.fingerprint_payload()
                for definition in self.definitions
            ],
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            self.fingerprint_payload(),
            normalization_version=NORMALIZATION_VERSION,
        )

    def definition(self, name: str) -> PromptDefinition:
        for definition in self.definitions:
            if definition.name == name:
                return definition
        raise KeyError(name)

    def with_definition(self, definition: PromptDefinition) -> "PromptBundle":
        updated = tuple(
            definition if item.name == definition.name else item
            for item in self.definitions
        )
        if updated == self.definitions:
            raise KeyError(definition.name)
        return replace(self, definitions=updated)


def build_prompt_bundle(
    selection_config: SelectionConfig | None = None,
) -> PromptBundle:
    return PromptBundle(
        version=PROMPT_BUNDLE_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        selection_config=selection_config or SelectionConfig(),
        definitions=(
            PromptDefinition(
                name="relevance",
                system_prompt=f"{RELEVANCE_SYSTEM_PROMPT}\n\n{TAXONOMY_GUIDANCE}",
                user_template=RELEVANCE_USER_TEMPLATE,
                response_model=RelevanceSelectionResponse,
            ),
            PromptDefinition(
                name="pass1",
                system_prompt=f"{PASS1_SYSTEM_PROMPT}\n\n{TAXONOMY_GUIDANCE}",
                user_template=PASS1_USER_TEMPLATE,
                response_model=Pass1Result,
            ),
            PromptDefinition(
                name="pass2",
                system_prompt=(
                    f"{PASS2_SYSTEM_PROMPT}\n\n{TAXONOMY_GUIDANCE}\n\n"
                    f"{ADMINISTRATIVE_STATUS_GUIDANCE}"
                ),
                user_template=PASS2_USER_TEMPLATE,
                response_model=Pass2Assessment,
            ),
        ),
    )


def render_relevance_user_prompt(
    source_blocks: str,
    *,
    max_selected_blocks: int,
) -> str:
    return Template(RELEVANCE_USER_TEMPLATE).substitute(
        source_blocks=source_blocks,
        max_selected_blocks=str(max_selected_blocks),
    )


def render_pass1_user_prompt(
    source_document: str,
    *,
    generation_plan: str,
    assessment_scope: str = "full_document",
    sensitive_seed: str | None = None,
) -> str:
    return Template(PASS1_USER_TEMPLATE).substitute(
        source_document=source_document,
        generation_plan=generation_plan,
        assessment_scope=assessment_scope,
        sensitive_seed=sensitive_seed or "(없음)",
    )


def render_pass2_user_prompt(generated_document_ir: str) -> str:
    return Template(PASS2_USER_TEMPLATE).substitute(
        generated_document_ir=generated_document_ir,
    )
