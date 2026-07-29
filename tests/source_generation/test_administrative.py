from __future__ import annotations

import pytest
from pydantic import ValidationError

from rd2.administrative_status import (
    ADMIN_STATUS_TEXT_POLICIES,
    AdminStatus,
)
from rd2.schema.models import CsoClassification
from rd2.source_generation.administrative import (
    administrative_status_generation_requirements,
)
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AdministrativeStatusFinding,
    EvidenceSpan,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationTarget,
    ParagraphBlock,
    Pass2Assessment,
    TargetClassification,
)


@pytest.mark.parametrize("status", tuple(AdminStatus))
def test_every_admin_status_has_a_semantic_p1_requirement_without_fixed_phrase(
    status: AdminStatus,
):
    policy = ADMIN_STATUS_TEXT_POLICIES[status]
    requirements = administrative_status_generation_requirements((status,))
    document = GeneratedDocumentIR(
        title="상태별 생성문",
        blocks=(
            ParagraphBlock(
                block_id="g1",
                text="담당 부서에서 후속 절차를 준비하고 있다.",
            ),
        ),
    )

    assert requirements == (
        {
            "status": status.value,
            "semantic_condition": policy.detail,
            "writing_instruction": (
                f"상태명 '{status.value}'이나 고정 문구를 그대로 쓰지 않아도 된다. "
                f"{policy.detail}임이 문서의 상황과 문맥에서 자연스럽게 드러나도록 작성한다."
            ),
        },
    )
    assert policy.label not in document.body_text


def test_admin_only_target_has_no_legal_clause_but_requires_s():
    target = GenerationTarget(
        classification=TargetClassification.S,
        administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )

    assert target.clause_no is None
    assert target.subclause_key is None

    with pytest.raises(ValidationError, match="requires S"):
        GenerationTarget(
            classification=TargetClassification.C,
            administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )


def test_legal_and_administrative_targets_can_overlap():
    target = GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )

    assert target.clause_no == ClauseNumber.CLAUSE_5
    assert target.administrative_statuses == (AdminStatus.APPROVAL_PENDING,)


def test_pass2_legal_o_plus_admin_status_computes_effective_s():
    phrase = "결재 진행 중"
    assessment = Pass2Assessment(
        document_type=SemanticDocumentType.REPORT,
        classification=CsoClassification.O,
        administrative_statuses=(
            AdministrativeStatusFinding(
                status=AdminStatus.APPROVAL_PENDING,
                evidence_spans=(
                    EvidenceSpan(
                        block_id="g1",
                        start=5,
                        end=5 + len(phrase),
                        quote=phrase,
                    ),
                ),
                rationale="결재가 완료되지 않은 상태가 본문에 명시됐다.",
            ),
        ),
        rationale="정보공개법 조항 근거는 없고 행정상태만 확인된다.",
    )

    assert assessment.classification == CsoClassification.O
    assert assessment.clause_no is None
    assert assessment.effective_classification == CsoClassification.S
