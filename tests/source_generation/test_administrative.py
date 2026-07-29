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


def test_declared_status_makes_effective_classification_s_without_p2_detection():
    """행정상태는 P2가 찾아내는 대상이 아니라 생성계획이 못 박는 메타데이터다.

    PDF 렌더러가 결재란을 강제로 그려 그 상태를 문서에 구성해 넣으므로,
    라벨은 판정이 아니라 구성으로 보장된다.
    """

    from rd2.source_generation.contracts import effective_classification

    legal_only = Pass2Assessment(
        document_type=SemanticDocumentType.REPORT,
        classification=CsoClassification.O,
        rationale="정보공개법 조항 근거는 없다.",
    )

    assert legal_only.classification == CsoClassification.O
    assert legal_only.clause_no is None
    # P2는 행정상태를 판정하지 않는다 — 계약에서 아예 사라졌다.
    assert "administrative_statuses" not in Pass2Assessment.model_fields

    # 선언된 상태가 있으면 최종 민감도는 S다.
    assert effective_classification(
        legal_only.classification, (AdminStatus.APPROVAL_PENDING,)
    ) == CsoClassification.S
    # 없으면 법적 판정 그대로다.
    assert effective_classification(legal_only.classification, ()) == CsoClassification.O
    # 법적 C는 행정상태와 무관하게 C를 유지한다.
    assert effective_classification(
        CsoClassification.C, (AdminStatus.DRAFT,)
    ) == CsoClassification.C


def test_reference_date_is_passed_to_the_model_for_not_yet_due_statuses():
    """기준일을 안 주면 모델은 미래·과거를 판단할 근거가 없다."""

    from datetime import date

    from rd2.administrative_status import AdminStatus
    from rd2.source_generation.administrative import (
        administrative_status_generation_requirements,
    )

    without = administrative_status_generation_requirements(
        (AdminStatus.RELEASE_NOT_DUE,)
    )[0]
    assert "reference_date" not in without

    with_ref = administrative_status_generation_requirements(
        (AdminStatus.RELEASE_NOT_DUE,), reference_date=date(2026, 7, 29)
    )[0]
    assert with_ref["reference_date"] == "2026-07-29"
    assert "기준일보다 나중" in with_ref["writing_instruction"]

    # 미래 날짜가 필요 없는 상태에는 날짜 지시를 덧붙이지 않는다.
    draft = administrative_status_generation_requirements(
        (AdminStatus.DRAFT,), reference_date=date(2026, 7, 29)
    )[0]
    assert "기준일보다 나중" not in draft["writing_instruction"]


def test_past_release_date_contradicts_a_not_yet_due_status():
    """실측 회귀: 2026년 생성문에 '공개 예정일 2024년 10월 1일'이 나왔다."""

    from datetime import date

    import pytest

    from rd2.administrative_status import AdminStatus
    from rd2.source_generation.administrative import (
        AdministrativeStatusError,
        validate_status_date_coherence,
    )

    reference = date(2026, 7, 29)
    statuses = (AdminStatus.RELEASE_NOT_DUE,)

    with pytest.raises(AdministrativeStatusError, match="이전이다"):
        validate_status_date_coherence(
            "지역 행정서비스 개선 검토안\n\n공개 예정일: 2024년 10월 1일",
            statuses,
            reference_date=reference,
        )

    # 미래 날짜가 하나라도 있으면 통과한다.
    validate_status_date_coherence(
        "공개 예정일: 2026년 10월 1일", statuses, reference_date=reference
    )
    # 날짜 없이 문맥만으로 상태를 드러내는 것도 허용된 표현이다.
    validate_status_date_coherence(
        "현재 대외 공개 전 상태이다", statuses, reference_date=reference
    )
    # 다른 상태는 날짜 규칙을 적용하지 않는다.
    validate_status_date_coherence(
        "작성일: 2024년 1월 1일", (AdminStatus.DRAFT,), reference_date=reference
    )
