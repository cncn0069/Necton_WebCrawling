from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from rd2.administrative_status import AdminStatus
from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    ConsistencyAssessment,
    EvidenceSpan,
    FailureCode,
    FailureStage,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    ParagraphBlock,
    RepairCode,
    RelevanceSelectionResponse,
    SensitiveMonitorDecision,
    SensitivePipelineStatus,
    SensitiveVerdict,
    SourceActorRole,
    SourceAssessment,
    SourceClassification,
    SourceDocumentSnapshot,
    SourceEvidenceLevel,
    SourcePage,
    SourceSlot,
    SourceSlotKind,
    SourceSuitability,
    SourceTextBlock,
    TargetClassification,
)
from rd2.source_generation.document_select import (
    SelectionConfig,
    finalize_relevance_selection,
    prepare_document_selection,
)
from rd2.source_generation.legacy_synthetic import FullySyntheticContext
from rd2.source_generation.pipeline import (
    PipelineConfig,
    RetryingGateway,
    StructuredCall,
    StructuredCallError,
    build_generation_plan,
    execute_classification,
    execute_consistency_validation,
    execute_generation,
    model_sha256,
    run_source_sensitive_pipeline,
    run_three_stage_pipeline,
)

from .v2_fixtures import (
    FakeGateway,
    generated_document,
    snapshot,
    source_assessment,
    target,
)


def _config(*, sensitive: bool = False) -> PipelineConfig:
    return PipelineConfig(
        classifier_model="shared-model",
        generator_model="shared-model",
        validator_model="validator-model",
        source_sensitive_mode=sensitive,
    )


def _selection():
    selected = prepare_document_selection(snapshot()).selection
    assert selected is not None
    return selected


def _general_generated() -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        title="입찰 평가 검토",
        blocks=(
            ParagraphBlock(
                block_id="generated:b0",
                text=(
                    "평가기준 35점과 협상 상한 380,000,000원은 공고 전 "
                    "내부 검토자료다."
                ),
            ),
        ),
    )


def _general_consistency() -> ConsistencyAssessment:
    quote = "평가기준 35점과 협상 상한 380,000,000원은 공고 전 내부 검토자료다."
    return ConsistencyAssessment(
        document_form=DocumentForm.OFFICIAL_LETTER,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        evidence_spans=(
            EvidenceSpan(block_id="generated:b0", quote=quote),
        ),
        rationale="공고 전 평가기준이 확인된다.",
    )


def _clause5_target() -> GenerationTarget:
    return target(
        clause=ClauseNumber.CLAUSE_5,
        subclause=SubclauseKey.BID_CONTRACT,
    )


def _clause5_assessment(**kwargs) -> SourceAssessment:
    return source_assessment(
        clause=ClauseNumber.CLAUSE_5,
        subclause=SubclauseKey.BID_CONTRACT,
        **kwargs,
    )


def test_pipeline_call_order_models_and_blind_validator_input():
    gateway = FakeGateway(
        [_clause5_assessment(), _general_generated(), _general_consistency()]
    )

    result = run_three_stage_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=_clause5_target(),
        gateway=gateway,
        config=_config(),
        sensitive_seed="공고 전 평가 세부값",
    )

    assert result.succeeded
    assert [call["model"] for call in gateway.calls] == [
        "shared-model",
        "shared-model",
        "validator-model",
    ]
    assert [call["response_model"].__name__ for call in gateway.calls] == [
        "SourceAssessment",
        "GeneratedDocumentIR",
        "ConsistencyAssessment",
    ]
    assert (
        "[SOURCE CONTEXT — 분류를 복사하지 않음]"
        in gateway.calls[1]["user_prompt"]
    )


def _accepted_sensitive_decision(
    rationale: str = "신청인과 개인 연락처가 직접 연결된다.",
) -> SensitiveMonitorDecision:
    return SensitiveMonitorDecision(
        classification=CsoClassification.S,
        rationale=rationale,
        # S에는 근거 문장이 필요하다 — 어느 문장을 보고 판단했는지가 원문 때문인지
        # 삽입 때문인지 가릴 유일한 단서다.
        evidence_spans=(
            EvidenceSpan(
                block_id="generated:b0",
                quote="신청인 김민서의 개인 연락처는 010-1234-5678이다.",
            ),
        ),
    )


def _open_sensitive_decision() -> SensitiveMonitorDecision:
    return SensitiveMonitorDecision(
        classification=CsoClassification.O,
        rationale="구체적인 개인정보 값이 없다.",
    )


def _clause6_generated_artifact(document: GeneratedDocumentIR):
    assessment = source_assessment()
    plan = build_generation_plan(
        assessment=assessment,
        requested_target=target(),
        snapshot=snapshot(),
        selection=_selection(),
    )
    execution = execute_generation(
        snapshot=snapshot(),
        selection=_selection(),
        assessment=assessment,
        plan=plan,
        gateway=FakeGateway([document]),
        config=_config(sensitive=True),
    )
    assert execution.artifact is not None
    return assessment, plan, execution.artifact
    assert "[AUTHORITATIVE OUTPUT TARGET]" in gateway.calls[1]["user_prompt"]
    validator_input = gateway.calls[2]["user_prompt"]
    assert "source_assessment" not in validator_input
    assert "generation_plan" not in validator_input
    assert "repair_codes" not in validator_input
    assert "generated:b0" in validator_input


def test_validator_must_differ_from_classifier_and_generator():
    gateway = FakeGateway([])
    config = PipelineConfig(
        classifier_model="same",
        generator_model="same",
        validator_model="same",
    )

    result = run_three_stage_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=_clause5_target(),
        gateway=gateway,
        config=config,
        sensitive_seed="seed",
    )

    assert result.failure is not None
    assert result.failure.code == FailureCode.MODEL_CONFIGURATION_INVALID
    assert gateway.calls == []


def test_invalid_classifier_evidence_prevents_planning_and_generation():
    invalid = _clause5_assessment().model_copy(
        update={
            "available_slots": (
                SourceSlot(
                    name="없는 칸",
                    kind=SourceSlotKind.PARAGRAPH,
                    evidence_span=EvidenceSpan(
                        block_id="missing",
                        quote="없는 인용",
                    ),
                ),
            )
        }
    )
    gateway = FakeGateway([invalid])

    execution = execute_classification(
        snapshot=snapshot(),
        selection=_selection(),
        gateway=gateway,
        config=_config(),
    )

    assert execution.failure is not None
    assert execution.failure.stage == FailureStage.CLASSIFICATION
    assert execution.failure.code == FailureCode.EVIDENCE_INVALID
    assert len(gateway.calls) == 1


def test_source_s_is_locked_to_its_exact_label():
    assessment = _clause5_assessment(
        classification=CsoClassification.S,
    )
    plan = build_generation_plan(
        assessment=assessment,
        requested_target=target(
            clause=ClauseNumber.CLAUSE_8,
            subclause=SubclauseKey.CORNERING,
        ),
        snapshot=snapshot(),
        selection=_selection(),
    )

    assert plan.generation_route == GenerationRoute.SOURCE_ALIGNED
    assert plan.final_target.clause_no == ClauseNumber.CLAUSE_5
    assert plan.final_target.subclause_key == SubclauseKey.BID_CONTRACT
    assert plan.final_target.generation_mode == GenerationMode.SOURCE_ALIGNED
    plan.validate_against(assessment)


@pytest.mark.parametrize(
    ("level", "seed", "has_synthetic", "expected"),
    (
        (
            SourceEvidenceLevel.DIRECT_SENSITIVE_SPAN,
            None,
            False,
            GenerationRoute.SPAN_SEEDED,
        ),
        (
            SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
            "민감 seed",
            False,
            GenerationRoute.ANCHORED,
        ),
        (
            SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE,
            None,
            True,
            GenerationRoute.FULLY_SYNTHETIC,
        ),
    ),
)
def test_deterministic_planner_route_matrix(
    level,
    seed,
    has_synthetic,
    expected,
):
    assessment = _clause5_assessment(evidence_level=level)

    plan = build_generation_plan(
        assessment=assessment,
        requested_target=_clause5_target(),
        snapshot=snapshot(),
        selection=_selection(),
        sensitive_seed=seed,
        has_synthetic_generator=has_synthetic,
    )

    assert plan.generation_route == expected
    assert plan.source_assessment_sha256 == model_sha256(assessment)


def test_admin_only_target_uses_administrative_augmented_route():
    assessment = source_assessment(
        evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
    )
    requested = GenerationTarget(
        classification=TargetClassification.S,
        administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )

    plan = build_generation_plan(
        assessment=assessment,
        requested_target=requested,
        snapshot=snapshot(),
        selection=_selection(),
    )

    assert plan.generation_route == GenerationRoute.ADMINISTRATIVE_AUGMENTED
    assert plan.final_target == requested


def test_counterfactual_request_is_preserved_when_source_primary_differs():
    """판별 결과는 원문 설명이고 배정 목표는 생성 명령이므로 서로 달라도 된다."""

    assessment = source_assessment(primary_subclause=SubclauseKey.PETITIONER_PII)
    requested = _clause5_target()

    plan = build_generation_plan(
        assessment=assessment,
        requested_target=requested,
        snapshot=snapshot(),
        selection=_selection(),
    )

    assert plan.requested_target == requested
    assert plan.final_target == requested
    assert plan.final_target.subclause_key == SubclauseKey.BID_CONTRACT
    assert plan.final_target.clause_no == ClauseNumber.CLAUSE_5
    assert plan.final_target.generation_mode == GenerationMode.COUNTERFACTUAL


def test_clause_six_request_does_not_require_a_matching_source_candidate():
    """제6호 전용 배치는 판별 후보가 달라도 배정한 제6호 목표를 유지한다."""

    assessment = source_assessment(
        primary_subclause=SubclauseKey.BID_CONTRACT,
        compatible_subclauses=(),
    )
    requested = target(
        clause=ClauseNumber.CLAUSE_6,
        subclause=SubclauseKey.PETITIONER_PII,
    )

    plan = build_generation_plan(
        assessment=assessment,
        requested_target=requested,
        snapshot=snapshot(),
        selection=_selection(),
    )

    assert plan.final_target == requested


def test_source_sensitive_pipeline_generates_when_source_primary_differs():
    """회귀: primary가 제5호면 제6호 목표를 교체한 뒤 생성 전에 막혔다."""

    assessment = source_assessment(
        primary_subclause=SubclauseKey.BID_CONTRACT,
        compatible_subclauses=(),
    )
    gateway = FakeGateway(
        [assessment, generated_document(), _accepted_sensitive_decision()]
    )

    run = run_source_sensitive_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=target(),
        gateway=gateway,
        config=_config(sensitive=True),
    )

    assert run.status == SensitivePipelineStatus.ACCEPTED_S
    assert run.final_result.generation_plan is not None
    assert run.final_result.generation_plan.final_target == target()
    assert [call["response_model"].__name__ for call in gateway.calls] == [
        "SourceAssessment",
        "GeneratedDocumentIR",
        "SensitiveMonitorDecision",
    ]


def test_generator_sees_full_source_even_when_classifier_view_is_truncated():
    pages = tuple(
        SourcePage(
            page_number=index,
            blocks=(
                SourceTextBlock(
                    block_id=f"source:{index}",
                    text=f"page-{index}-content",
                ),
            ),
        )
        for index in range(1, 87)
    )
    large = SourceDocumentSnapshot(
        source_document_id="large",
        source="fixture",
        manifest_key="fixture/large",
        source_sha256="b" * 64,
        pages=pages,
    )
    selection_config = SelectionConfig(
        front_page_limit=1,
        max_selected_blocks=1,
    )
    prepared = prepare_document_selection(large, selection_config)
    assert prepared.relevance_request is not None
    selection = finalize_relevance_selection(
        large,
        prepared.relevance_request,
        RelevanceSelectionResponse(
            selected_block_ids=("source:1",),
            rationale="첫 블록",
        ),
        selection_config,
    )
    span = EvidenceSpan(block_id="source:1", quote="page-1-content")
    assessment = SourceAssessment(
        source_classification=SourceClassification(
            document_form=DocumentForm.OFFICIAL_LETTER,
            classification=CsoClassification.O,
            rationale="공개 문서",
        ),
        source_suitability=SourceSuitability(
            assessment_scope=AssessmentScope.SELECTED_VIEW_ONLY,
            evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
            evidence_spans=(span,),
            rationale="첫 페이지 맥락",
            reason_code="TRUNCATED",
        ),
        business_context="입찰 업무",
        subject_roles=(SourceActorRole.APPLICANT,),
        available_slots=(
            SourceSlot(
                name="본문",
                kind=SourceSlotKind.PARAGRAPH,
                evidence_span=span,
            ),
        ),
        primary_subclause=SubclauseKey.BID_CONTRACT,
        primary_rationale="입찰 업무와 평가 항목 자리가 있다.",
    )
    gateway = FakeGateway(
        [assessment, _general_generated(), _general_consistency()]
    )

    result = run_three_stage_pipeline(
        snapshot=large,
        selection=selection,
        counterfactual_target=_clause5_target(),
        gateway=gateway,
        config=_config(),
        selection_config=selection_config,
        sensitive_seed="seed",
    )

    assert result.succeeded
    assert "page-86-content" not in gateway.calls[0]["user_prompt"]
    assert "page-86-content" in gateway.calls[1]["user_prompt"]


@dataclass
class SyntheticGenerator:
    document: GeneratedDocumentIR
    calls: int = 0

    def generate(self, *, target, context):
        self.calls += 1
        return self.document


def test_fully_synthetic_generation_skips_generator_gateway_and_has_no_receipt():
    assessment = _clause5_assessment(
        evidence_level=SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE
    )
    synthetic = SyntheticGenerator(_general_generated())
    gateway = FakeGateway([assessment, _general_consistency()])

    result = run_three_stage_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=_clause5_target(),
        gateway=gateway,
        config=_config(),
        fully_synthetic_generator=synthetic,
        fully_synthetic_context=FullySyntheticContext(
            scenario_id="synthetic-1",
            ordering_agency="가상기관",
            production_date="2026-07-29",
        ),
    )

    assert result.succeeded
    assert synthetic.calls == 1
    assert result.generation_receipt is None
    assert result.generation_artifact is not None
    assert (
        result.generation_artifact.provenance.generation_route
        == GenerationRoute.FULLY_SYNTHETIC
    )
    assert [call["response_model"].__name__ for call in gateway.calls] == [
        "SourceAssessment",
        "ConsistencyAssessment",
    ]


def test_validation_mismatch_produces_typed_repair_codes():
    assessment = _clause5_assessment()
    plan = build_generation_plan(
        assessment=assessment,
        requested_target=_clause5_target(),
        snapshot=snapshot(),
        selection=_selection(),
        sensitive_seed="seed",
    )
    generation_gateway = FakeGateway([_general_generated()])
    generated = execute_generation(
        snapshot=snapshot(),
        selection=_selection(),
        assessment=assessment,
        plan=plan,
        gateway=generation_gateway,
        config=_config(),
        sensitive_seed="seed",
    )
    assert generated.artifact is not None
    validator_gateway = FakeGateway(
        [
            ConsistencyAssessment(
                document_form=DocumentForm.REPORT,
                classification=CsoClassification.O,
                rationale="요건이 없다.",
            )
        ]
    )

    checked = execute_consistency_validation(
        assessment=assessment,
        plan=plan,
        artifact=generated.artifact,
        gateway=validator_gateway,
        config=_config(),
    )

    assert checked.succeeded
    assert checked.comparison is not None
    assert checked.comparison.requires_review
    assert checked.repair_codes == (
        RepairCode.FORM_MISMATCH,
        RepairCode.CLASSIFICATION_MISMATCH,
        RepairCode.CLAUSE_MISMATCH,
        RepairCode.SUBCLAUSE_MISMATCH,
    )


def test_source_sensitive_retry_reuses_classification_and_plan():
    generic = generated_document("신청인의 연락처 항목을 확인한다.")
    concrete = generated_document()
    gateway = FakeGateway(
        [
            source_assessment(),
            generic,
            _open_sensitive_decision(),
            concrete,
            _accepted_sensitive_decision(),
        ]
    )

    run = run_source_sensitive_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=target(),
        gateway=gateway,
        config=_config(sensitive=True),
        sensitive_seed="신청인에게 새 가상 전화번호를 부여",
    )

    assert run.status == SensitivePipelineStatus.ACCEPTED_S
    assert len(run.attempts) == 2
    assert [call["response_model"].__name__ for call in gateway.calls] == [
        "SourceAssessment",
        "GeneratedDocumentIR",
        "SensitiveMonitorDecision",
        "GeneratedDocumentIR",
        "SensitiveMonitorDecision",
    ]
    first = run.attempts[0].generation_artifact
    second = run.attempts[1].generation_artifact
    assert first is not None and second is not None
    assert second.attempt_index == 2
    assert second.parent_generation_sha256 == model_sha256(first)
    assert RepairCode.DIRECT_VALUE_MISSING in second.repair_codes
    assert "direct_value_missing" in gateway.calls[3]["user_prompt"]
    first_assessment = run.attempts[0].consistency_assessment
    final_assessment = run.attempts[-1].consistency_assessment
    assert first_assessment is not None
    assert first_assessment.classification == CsoClassification.O
    assert first_assessment.clause_no is None
    assert first_assessment.evidence_spans == ()
    assert final_assessment is not None
    assert final_assessment.classification == CsoClassification.S
    assert final_assessment.clause_no == ClauseNumber.CLAUSE_6
    # 검사기가 낸 근거를 그대로 옮긴다 — 어느 문장을 보고 S라 했는지가
    # 원문 때문인지 삽입 때문인지 가릴 유일한 단서다.
    assert [span.quote for span in final_assessment.evidence_spans] == [
        "신청인 김민서의 개인 연락처는 010-1234-5678이다."
    ]
    assert final_assessment.assertions == ()
    assert final_assessment.rationale == "신청인과 개인 연락처가 직접 연결된다."


def test_sensitive_monitor_uses_locked_metadata_and_allows_blank_rationale():
    assessment, plan, artifact = _clause6_generated_artifact(generated_document())

    checked = execute_consistency_validation(
        assessment=assessment,
        plan=plan,
        artifact=artifact,
        gateway=FakeGateway([_accepted_sensitive_decision("")]),
        config=_config(sensitive=True),
    )

    assert checked.succeeded
    assert checked.assessment is not None
    assert checked.assessment.classification == CsoClassification.S
    assert checked.assessment.document_form == (
        assessment.source_classification.document_form
    )
    assert checked.assessment.clause_no == plan.final_target.clause_no
    assert checked.assessment.subclause_key == plan.final_target.subclause_key
    assert [span.quote for span in checked.assessment.evidence_spans] == [
        "신청인 김민서의 개인 연락처는 010-1234-5678이다."
    ]
    assert checked.assessment.assertions == ()
    assert checked.assessment.rationale == "부가 근거 기록 없음"


def test_general_consistency_still_requires_exact_evidence_for_s():
    with pytest.raises(
        ValueError,
        match="C/S classification requires at least one evidence span",
    ):
        ConsistencyAssessment(
            document_form=DocumentForm.OFFICIAL_LETTER,
            classification=CsoClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            rationale="일반 검증 계약은 기존 근거 의무를 유지한다.",
        )


def test_source_sensitive_o_is_excluded_after_one_generation_retry():
    gateway = FakeGateway(
        [
            source_assessment(),
            generated_document(),
            _open_sensitive_decision(),
            generated_document(),
            _open_sensitive_decision(),
        ]
    )

    run = run_source_sensitive_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=target(),
        gateway=gateway,
        config=_config(sensitive=True),
        sensitive_seed="seed",
    )

    assert run.status == SensitivePipelineStatus.EXCLUDED_AFTER_RETRY
    assert len(run.attempts) == 2
    assert all(
        attempt.consistency_assessment is not None
        and attempt.consistency_assessment.classification == CsoClassification.O
        for attempt in run.attempts
    )
    second = run.attempts[-1].generation_artifact
    assert second is not None
    assert second.repair_codes == (RepairCode.DIRECT_VALUE_MISSING,)


def test_sensitive_validator_failure_does_not_regenerate_document():
    gateway = FakeGateway(
        [
            source_assessment(),
            generated_document(),
            _general_consistency(),
        ]
    )

    run = run_source_sensitive_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=target(),
        gateway=gateway,
        config=_config(sensitive=True),
        sensitive_seed="seed",
    )

    assert run.status == SensitivePipelineStatus.PIPELINE_FAILED
    assert len(run.attempts) == 1
    assert run.attempts[0].failure is not None
    assert run.attempts[0].failure.code == FailureCode.STRUCTURED_OUTPUT_INVALID
    assert len(gateway.calls) == 3


def test_retrying_gateway_retries_only_selected_typed_failures():
    class Inner:
        def __init__(self):
            self.calls = 0

        def parse(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise StructuredCallError(
                    FailureCode.STRUCTURED_OUTPUT_INVALID,
                    "invalid",
                    retryable=False,
                )
            return StructuredCall(
                parsed=_general_generated(),
                response_id="ok",
            )

    inner = Inner()
    gateway = RetryingGateway(inner, max_attempts=2)

    call = gateway.parse(
        model="m",
        system_prompt="s",
        user_prompt="u",
        response_model=GeneratedDocumentIR,
        max_output_tokens=100,
    )

    assert call.response_id == "ok"
    assert inner.calls == 2
    assert gateway.retried == [
        (FailureCode.STRUCTURED_OUTPUT_INVALID, 1)
    ]


def test_retrying_gateway_does_not_retry_model_refusal():
    class Inner:
        def parse(self, **kwargs):
            raise StructuredCallError(
                FailureCode.MODEL_REFUSAL,
                "refused",
                retryable=False,
            )

    gateway = RetryingGateway(Inner(), max_attempts=2)

    with pytest.raises(StructuredCallError):
        gateway.parse(
            model="m",
            system_prompt="s",
            user_prompt="u",
            response_model=GeneratedDocumentIR,
            max_output_tokens=100,
        )
    assert gateway.retried == []
