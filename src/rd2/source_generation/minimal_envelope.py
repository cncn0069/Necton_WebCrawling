"""최소 경로 산출물을 PDF 렌더러가 읽는 envelope로 조립한다.

PDF 렌더러(``generated_document_pipeline.render_generation_payload``)는 envelope에서
딱 둘만 꺼낸다 — 본문(``result.generated_document``)과 템플릿 선택 키
(``result.source_classification.document_type``). 원문도 프롬프트도 보지 않는다.

**``document_type``은 RDS에서 온 값을 그대로 쓴다.** 판별기에게 다시 맞히게 하지
않는다. 원문 행에 수집 시점 ``doc_type``이 이미 붙어 있고, 그 값이 곧 렌더러가
읽는 ``SemanticDocumentType``이다. 아는 값을 다시 판정시키면 틀릴 자리만 하나
늘고, 판별기가 고르는 ``DocumentForm``(17종)은 렌더러 enum(27종)과 값이 5개만
겹쳐 변환 없이는 계약에서 거부된다 — 그 변환을 아예 안 하는 것이 이 선택이다.

그래서 판별기는 세부조항만 찾는다(``FixedFormSourceAssessment``). 문서 형식은
``DOCUMENT_FORM_BY_TYPE``으로 ``doc_type``에서 유도한다.

``document_type``이 없는 옛 payload는 ``payload_document_type``이 문서 형식과
제목·첫 문단으로 보수적으로 복원한다. 이 모듈은 그 자리를 침범하지 않는다 —
``doc_type``을 받으면 그대로 싣고, 못 받으면 비워서 그쪽이 채우게 둔다.

이 모듈은 ``generated_document_pipeline``을 import하지 않는다. 그쪽은 weasyprint를
끌어오는데 렌더링 없이 envelope만 만들 때는 필요 없고, 환경에 따라 그 import가
실패한다(실측: ``libgobject-2.0-0.dll`` 로드 실패). 조립과 렌더링을 떼어 두면
envelope는 어디서든 만들 수 있고 렌더링만 되는 곳에서 돌리면 된다.
"""

from __future__ import annotations

from typing import Any, Mapping

from rd2.source_generation.classification_taxonomy import (
    DOCUMENT_FORM_BY_TYPE,
    DocumentForm,
    SemanticDocumentType,
    SubclauseKey,
    clause_of_subclause,
    expected_classification,
)
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    GeneratorResponse,
    SourceDocumentSnapshot,
    TargetClassification,
)

MINIMAL_ENVELOPE_VERSION = "minimal-envelope-2026-08-04-v2"

#: 최소 루트의 생성 route. 원문의 골격을 유지한 채 값을 갈아끼우므로 문서를 새로
#: 지어내는 ``fully_synthetic``이 아니라 원문 정렬이다. ``render_minimal_pdf``가
#: 렌더러에 넘기는 값과 같아야 한다 — 다르면 PDF와 DB 행이 서로 다른 route로
#: 만들어졌다고 말하게 된다.
MINIMAL_GENERATION_ROUTE = GenerationRoute.SOURCE_ALIGNED


def minimal_generation_target(subclause: SubclauseKey) -> GenerationTarget:
    """판별기가 준 세부조항을 **그대로** 생성 목표로 고정한다.

    큰 파이프라인은 요청 목표와 최종 목표를 나눠 두고, 원문이 요청을 지지하지
    못하면 계획기가 호환 세부조항으로 갈아탄다. 최소 루트에는 계획기가 없고
    요청 목표라는 것도 없다 — 판별기가 원문을 읽고 고른 ``primary_subclause``가
    처음이자 마지막 목표다. 그래서 갈아타기 자리를 두지 않는다.

    조항과 분류는 세부조항에서 유도한다. 손으로 ``"S"``를 박으면
    ``GenerationTarget``의 정합성 검사(조항↔분류)와 어긋날 자리가 생기는데,
    여기서 유도하면 애초에 어긋날 수 없다.
    """

    clause_no = clause_of_subclause(subclause)
    return GenerationTarget(
        classification=TargetClassification(
            expected_classification(clause_no).value
        ),
        clause_no=clause_no,
        subclause_key=subclause,
        generation_mode=GenerationMode.SOURCE_ALIGNED,
    )


def document_form_for_doc_type(doc_type: str | None) -> DocumentForm | None:
    """RDS ``doc_type``에서 문서 형식을 유도한다.

    판별기에게 형식을 묻지 않기 위한 것이다. 모르는 값이면 ``None``을 돌려
    호출자가 형식까지 판정하는 경로로 보내게 한다 — 임의로 ``other``를 주면
    형식×세부조항 호환 검사가 조용히 느슨해진다.
    """

    if not doc_type:
        return None
    try:
        semantic = SemanticDocumentType(doc_type)
    except ValueError:
        return None
    return DOCUMENT_FORM_BY_TYPE.get(semantic)


def build_generation_envelope(
    *,
    assessment: Mapping[str, Any],
    response: GeneratorResponse,
    snapshot: SourceDocumentSnapshot,
    doc_type: str | None,
    document_form: DocumentForm | None = None,
    classifier_prompt: Mapping[str, str] | None = None,
    generator_prompt: Mapping[str, str] | None = None,
    ground_id: str | None = None,
    source_pages: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """렌더러가 읽는 ``result``와, 기록으로만 남는 나머지를 함께 담는다.

    ``result`` 밖의 키는 렌더러가 무시한다(모든 모델이 ``extra="allow"``). 그래도
    함께 두는 이유는 **산출물 하나만 보고 되짚을 수 있게** 하기 위해서다 —
    프롬프트를 바꿔 가며 돌릴 때 어느 버전으로 뽑은 PDF인지 파일 밖에서 알 방법이
    없으면 비교가 안 된다.

    ``contract_version``은 ``result``와 ``generated_document``가 같아야 한다.
    다르면 envelope 검증이 거부한다.
    """

    classification: dict[str, Any] = {}
    if doc_type:
        classification["document_type"] = doc_type
    resolved_form = document_form or document_form_for_doc_type(doc_type)
    if resolved_form is not None:
        # 렌더러는 안 보지만 남긴다 — ``payload_document_type``이 document_type
        # 없는 payload를 복원할 때 읽는 값이고, 사후에 어느 형식으로 생성했는지
        # 되짚는 유일한 단서다.
        classification["document_form"] = resolved_form.value

    return {
        "result": {
            "contract_version": CONTRACT_SCHEMA_VERSION,
            "source_classification": classification,
            "generated_document": response.document.model_dump(
                mode="json", exclude_computed_fields=True
            ),
        },
        "minimal_path": {
            "envelope_version": MINIMAL_ENVELOPE_VERSION,
            "source_document_id": snapshot.source_document_id,
            "source_sha256": snapshot.source_sha256,
            "manifest_key": snapshot.manifest_key,
            "doc_type": doc_type,
            "ground_id": ground_id,
            "planted_grounds": list(response.planted_grounds),
            "assessment": dict(assessment),
            "kept_structure": response.kept_structure,
            "ground_plan": response.ground_plan,
            "transformations": [
                note.model_dump(mode="json") for note in response.transformations
            ],
            "classifier_prompt": dict(classifier_prompt or {}),
            "generator_prompt": dict(generator_prompt or {}),
            # 원문 block을 구조 그대로 남긴다. 프롬프트 문자열 안에도 들어 있지만
            # 거기서 꺼내려면 파싱해야 하고, ``transformations``의 block_id를
            # 원문과 맞추려면 매번 원본 파일을 다시 읽어야 했다.
            "source_pages": source_pages
            or {
                str(page.page_number): [
                    {"block_id": block.block_id, "text": block.text}
                    for block in page.blocks
                ]
                for page in snapshot.pages
            },
        },
    }
