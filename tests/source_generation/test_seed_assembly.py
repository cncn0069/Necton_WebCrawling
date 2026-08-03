from __future__ import annotations

from rd2.administrative_status import AdminStatus
from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSE_DEFINITIONS,
    ClauseNumber,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    EvidenceSpan,
    GenerationMode,
    GenerationTarget,
    SourceActorRole,
    SourceAssessment,
    SourceClassification,
    SourceEvidenceLevel,
    SourceSlot,
    SourceSlotKind,
    SourceSuitability,
    TargetClassification,
)
from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import DocumentForm
from rd2.source_generation.contracts import AssessmentScope
from rd2.source_generation.seed_assembly import build_sensitive_seed


def _assessment(**kwargs) -> SourceAssessment:
    defaults = dict(
        source_classification=SourceClassification(
            document_form=DocumentForm.ADMINISTRATIVE_RULE,
            classification=CsoClassification.O,
            rationale="공개 고시다",
        ),
        source_suitability=SourceSuitability(
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
            evidence_spans=(EvidenceSpan(block_id="p1:b0", quote="고시한다"),),
            rationale="업무 맥락만 있다",
            reason_code="contextual_only",
        ),
        business_context="고용보험료 산정 고시 개정",
        subject_roles=(SourceActorRole.PUBLIC_OFFICIAL,),
        available_slots=(
            SourceSlot(
                name="기초일액 개정안",
                kind=SourceSlotKind.PARAGRAPH,
                evidence_span=EvidenceSpan(block_id="p1:b0", quote="고시한다"),
            ),
        ),
        primary_subclause=SubclauseKey.DECISION_REVIEW,
        primary_rationale="고시 개정 검토 업무가 내부검토 자리를 제공한다.",
    )
    defaults.update(kwargs)
    return SourceAssessment(**defaults)


def _target(subclause: SubclauseKey, clause: ClauseNumber) -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=clause,
        subclause_key=subclause,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def test_seed_carries_the_target_subclause_requirements_and_source_context():
    seed = build_sensitive_seed(
        _assessment(),
        _target(SubclauseKey.DECISION_REVIEW, ClauseNumber.CLAUSE_5),
    )

    assert seed is not None
    definition = SUBCLAUSE_DEFINITIONS[SubclauseKey.DECISION_REVIEW]
    # 그 조항에 필요한 내용이 taxonomy에서 그대로 실린다.
    assert definition.definition in seed
    for item in definition.includes:
        assert item in seed
    # 원문에서 관찰한 것이 함께 실린다.
    assert "고용보험료 산정 고시 개정" in seed
    assert "public_official" in seed
    assert "기초일액 개정안" in seed


def test_seed_is_deterministic_and_differs_per_source():
    target = _target(SubclauseKey.DECISION_REVIEW, ClauseNumber.CLAUSE_5)
    a = _assessment()
    b = _assessment(business_context="산업표준 개정 고시")

    assert build_sensitive_seed(a, target) == build_sensitive_seed(a, target)
    assert build_sensitive_seed(a, target) != build_sensitive_seed(b, target)


def test_seed_differs_per_target_subclause():
    a = _assessment()

    assert build_sensitive_seed(
        a, _target(SubclauseKey.DECISION_REVIEW, ClauseNumber.CLAUSE_5)
    ) != build_sensitive_seed(
        a, _target(SubclauseKey.UNIT_COST, ClauseNumber.CLAUSE_7)
    )


def test_admin_only_target_has_no_seed():
    """조항이 없으면 심을 법적 내용이 없으므로 seed도 없다."""

    # 조항 없는 목표는 계약상 행정상태가 최소 1개 있어야 한다.
    admin_only = GenerationTarget(
        classification=TargetClassification.S,
        administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )

    assert build_sensitive_seed(_assessment(), admin_only) is None


def test_seed_never_asks_for_bare_field_names():
    """값 없이 항목명만 나열하는 생성을 막는 지시가 들어 있어야 한다."""

    seed = build_sensitive_seed(
        _assessment(),
        _target(SubclauseKey.DECISION_REVIEW, ClauseNumber.CLAUSE_5),
    )

    assert seed is not None
    assert "값 없이 항목명만 나열하지" in seed
    assert "원문에 없는 새 가상 값" in seed
