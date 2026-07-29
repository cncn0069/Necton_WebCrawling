from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from rd2.administrative_status import AdminStatus
from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    EvidenceSpan,
    FailureCode,
    FailureStage,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    ParagraphBlock,
    Pass1Result,
    Pass2Assessment,
    SourceClassification,
    SourceEvidenceLevel,
    SourceSuitability,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
    TargetClassification,
    TokenUsage,
)
from rd2.source_generation.document_select import prepare_document_selection
from rd2.source_generation.pipeline import (
    OpenAIResponsesGateway,
    PipelineConfig,
    StructuredCall,
    StructuredCallError,
    run_two_pass,
)
from rd2.source_generation.legacy_synthetic import FullySyntheticContext


def _snapshot() -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document_id="source-1",
        source="PRISM",
        manifest_key="manifest/source-1",
        source_sha256="a" * 64,
        pages=(
            SourcePage(
                page_number=1,
                blocks=(
                    SourceTextBlock(
                        block_id="p1:b0",
                        text="입찰 평가 기준은 내부 검토 중이다.",
                    ),
                ),
            ),
        ),
    )


def _target() -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def _pass1(*, source_o: bool = False, source_block_id: str = "p1:b0") -> Pass1Result:
    snapshot_text = _snapshot().block_text("p1:b0")
    if source_o:
        source = SourceClassification(
            document_type=SemanticDocumentType.BID_NOTICE,
            classification=CsoClassification.O,
            rationale="공개 입찰공고다.",
        )
        target = _target()
        route = GenerationRoute.FULLY_SYNTHETIC
        suitability = SourceSuitability(
            evidence_level=SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            reason_code="NO_USABLE_SOURCE",
            rationale="생성 목표에 사용할 공개 근거가 없다.",
        )
    else:
        quote = "평가 기준"
        source = SourceClassification(
            document_type=SemanticDocumentType.BID_NOTICE,
            classification=CsoClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            evidence_spans=(
                EvidenceSpan(block_id=source_block_id, quote=quote),
            ),
            rationale="입찰 평가 기준이 내부 검토 중이다.",
        )
        target = GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.SOURCE_ALIGNED,
        )
        route = GenerationRoute.SOURCE_ALIGNED
        suitability = SourceSuitability(
            evidence_level=SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_spans=source.evidence_spans,
            reason_code="DIRECT_SOURCE_LABEL",
            rationale="원문에 입찰계약 관련 직접 근거가 있다.",
        )
    return Pass1Result(
        source_classification=source,
        source_suitability=suitability,
        generation_route=route,
        generation_target=target,
        generated_document=GeneratedDocumentIR(
            title="사업자 평가 검토안",
            blocks=(
                ParagraphBlock(
                    block_id="generated-p1",
                    text="사업자 선정 평가 기준과 배점은 내부 검토 중이다.",
                ),
            ),
        ),
    )


def _o_route_pass1(
    route: GenerationRoute,
    *,
    target: GenerationTarget | None = None,
) -> Pass1Result:
    text = _snapshot().block_text("p1:b0")
    quote = "평가 기준"
    evidence = EvidenceSpan(block_id="p1:b0", quote=quote)
    level_by_route = {
        GenerationRoute.SPAN_SEEDED: SourceEvidenceLevel.DIRECT_SENSITIVE_SPAN,
        GenerationRoute.ANCHORED: SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
    }
    return Pass1Result(
        source_classification=SourceClassification(
            document_type=SemanticDocumentType.BID_NOTICE,
            classification=CsoClassification.O,
            rationale="명시적 비공개 조항은 없지만 입찰 평가 맥락이 있다.",
        ),
        source_suitability=SourceSuitability(
            evidence_level=level_by_route[route],
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_spans=(evidence,),
            reason_code=f"{route.value.upper()}_EVIDENCE",
            rationale="원문에 입찰 평가와 직접 관련된 구절이 있다.",
        ),
        generation_route=route,
        generation_target=target or _target(),
        generated_document=GeneratedDocumentIR(
            title="사업자 평가 검토안",
            blocks=(
                ParagraphBlock(
                    block_id="generated-p1",
                    text="사업자 선정 평가 기준과 배점은 내부 검토 중이다.",
                ),
            ),
        ),
    )


def _pass2(
    *,
    subclause: SubclauseKey = SubclauseKey.BID_CONTRACT,
    block_id: str = "generated-p1",
) -> Pass2Assessment:
    text = _pass1().generated_document.block_text("generated-p1")
    quote = "평가 기준"
    return Pass2Assessment(
        document_type=SemanticDocumentType.BID_NOTICE,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=subclause,
        evidence_spans=(
            EvidenceSpan(block_id=block_id, quote=quote),
        ),
        rationale="생성본에 내부 평가 기준이 있다.",
    )


@dataclass
class FakeGateway:
    queued: list[Any]

    def __post_init__(self):
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        next_result = self.queued.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return StructuredCall(
            parsed=next_result,
            response_id=f"response-{len(self.calls)}",
            token_usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        )


@dataclass
class FakeFullySyntheticGenerator:
    document: GeneratedDocumentIR

    def __post_init__(self):
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.document


def _run(
    gateway: FakeGateway,
    *,
    config: PipelineConfig | None = None,
    fully_synthetic_generator: FakeFullySyntheticGenerator | None = None,
    include_fully_synthetic_dependencies: bool = True,
):
    snapshot = _snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None
    synthetic_generator = fully_synthetic_generator or FakeFullySyntheticGenerator(
        _pass1().generated_document
    )
    return run_two_pass(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_target(),
        gateway=gateway,
        config=config
        or PipelineConfig(
            generator_model="generator-model",
            grader_model="grader-model",
        ),
        fully_synthetic_generator=(
            synthetic_generator if include_fully_synthetic_dependencies else None
        ),
        fully_synthetic_context=(
            FullySyntheticContext(
                scenario_id="test-bid-contract-001",
                ordering_agency="조달청",
                production_date="2025-01-15",
            )
            if include_fully_synthetic_dependencies
            else None
        ),
    )


def test_two_pass_uses_different_models_and_blind_pass2_input():
    gateway = FakeGateway([_pass1(), _pass2()])
    result = _run(gateway)

    assert result.succeeded is True
    assert result.comparison is not None
    assert result.comparison.requires_review is False
    assert [call["model"] for call in gateway.calls] == [
        "generator-model",
        "grader-model",
    ]
    assert gateway.calls[0]["response_model"] is Pass1Result
    assert gateway.calls[1]["response_model"] is Pass2Assessment
    pass2_user_prompt = gateway.calls[1]["user_prompt"]
    assert "source_classification" not in pass2_user_prompt
    assert "generation_target" not in pass2_user_prompt
    assert "rationale" not in pass2_user_prompt
    assert "generated-p1" in pass2_user_prompt


def test_o_source_remains_o_while_generated_target_is_counterfactual_s():
    gateway = FakeGateway([_pass1(source_o=True), _pass2()])
    result = _run(gateway)

    assert result.succeeded is True
    assert result.pass1_result is not None
    assert (
        result.pass1_result.source_classification.classification
        == CsoClassification.O
    )
    assert (
        result.pass1_result.generation_target.classification
        == TargetClassification.S
    )
    assert (
        result.pass1_result.generation_target.generation_mode
        == GenerationMode.COUNTERFACTUAL
    )
    assert result.comparison is not None
    assert result.comparison.requires_review is False


def test_fully_synthetic_discards_the_document_created_in_source_seeing_pass1():
    source_leaking = _pass1(source_o=True)
    source_leaking = source_leaking.model_copy(
        update={
            "source_classification": source_leaking.source_classification.model_copy(
                update={"document_type": SemanticDocumentType.RESEARCH_REPORT}
            )
        }
    )
    replacement = GeneratedDocumentIR(
        title="별도 합성 입찰 평가안",
        blocks=(
            ParagraphBlock(
                block_id="generated-p1",
                text="사업자 선정 평가 기준과 배점은 내부 검토 중이다.",
            ),
        ),
    )
    synthetic_generator = FakeFullySyntheticGenerator(replacement)
    gateway = FakeGateway([source_leaking, _pass2()])

    result = _run(
        gateway,
        fully_synthetic_generator=synthetic_generator,
    )

    assert result.succeeded is True
    assert result.pass1_result is not None
    assert result.pass1_result.generated_document.title == "별도 합성 입찰 평가안"
    assert len(synthetic_generator.calls) == 1
    assert synthetic_generator.calls[0]["target"] == source_leaking.generation_target
    assert "별도 합성 입찰 평가안" in gateway.calls[1]["user_prompt"]
    assert result.generation_provenance is not None
    assert (
        result.generation_provenance.synthetic_scenario_id
        == "test-bid-contract-001"
    )


def test_fully_synthetic_fails_closed_without_source_free_generator_context():
    gateway = FakeGateway([_pass1(source_o=True)])

    result = _run(
        gateway,
        include_fully_synthetic_dependencies=False,
    )

    assert result.succeeded is False
    assert result.failure is not None
    assert result.failure.code == FailureCode.ROUTE_INVALID
    assert len(gateway.calls) == 1
    assert result.pass1_result is not None
    assert result.pass1_result.generated_document.title == "완전 합성 문서 생성 대기"
    assert "사업자 평가 검토안" not in result.pass1_result.generated_document.body_text


def test_span_seeded_route_is_validated_and_recorded_in_provenance():
    gateway = FakeGateway([_o_route_pass1(GenerationRoute.SPAN_SEEDED), _pass2()])
    result = _run(gateway)

    assert result.succeeded is True
    assert result.generation_provenance is not None
    assert (
        result.generation_provenance.generation_route
        == GenerationRoute.SPAN_SEEDED
    )
    assert result.generation_provenance.uses_source_evidence is True
    assert result.generation_provenance.validated_evidence_spans


def test_anchored_route_requires_sensitive_seed_before_calling_pass2():
    without_seed = FakeGateway([_o_route_pass1(GenerationRoute.ANCHORED)])
    failed = _run(without_seed)

    assert failed.failure is not None
    assert failed.failure.stage == FailureStage.PASS1
    assert failed.failure.code == FailureCode.ROUTE_INVALID
    assert "sensitive seed" in failed.failure.message
    assert len(without_seed.calls) == 1

    with_seed = FakeGateway([_o_route_pass1(GenerationRoute.ANCHORED), _pass2()])
    snapshot = _snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None
    succeeded = run_two_pass(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_target(),
        gateway=with_seed,
        config=PipelineConfig(
            generator_model="generator-model",
            grader_model="grader-model",
        ),
        sensitive_seed="평가위원별 세부 검토 의견",
    )

    assert succeeded.succeeded is True
    assert succeeded.generation_provenance is not None
    assert succeeded.generation_provenance.sensitive_seed_sha256 is not None


def test_p1_may_change_suggested_target_and_p2_grades_final_target():
    changed_target = GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.DECISION_REVIEW,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )
    gateway = FakeGateway(
        [
            _o_route_pass1(
                GenerationRoute.SPAN_SEEDED,
                target=changed_target,
            ),
            _pass2(subclause=SubclauseKey.DECISION_REVIEW),
        ]
    )
    result = _run(gateway)

    assert result.succeeded is True
    assert result.generation_provenance is not None
    assert result.generation_provenance.requested_target == _target()
    assert result.generation_provenance.final_target == changed_target
    assert result.comparison is not None
    assert result.comparison.subclause_match is True


def test_p2_receives_taxonomy_but_no_route_or_suitability_metadata():
    gateway = FakeGateway([_o_route_pass1(GenerationRoute.SPAN_SEEDED), _pass2()])
    result = _run(gateway)

    assert result.succeeded is True
    pass2_system_prompt = gateway.calls[1]["system_prompt"]
    pass2_user_prompt = gateway.calls[1]["user_prompt"]
    assert "bid_contract (입찰계약):" in pass2_system_prompt
    assert "decision_review (의사결정·내부검토):" in pass2_system_prompt
    # 라벨만이 아니라 판정 정의와 포함·제외 기준까지 채점자에게 전달된다.
    assert "예정가격 산정 근거" in pass2_system_prompt
    assert "핵심 업무가 감사면 audit_inspection" in pass2_system_prompt
    for forbidden in (
        "generation_route",
        "source_suitability",
        "requested_target",
        "span_seeded",
        "fully_synthetic",
    ):
        assert forbidden not in pass2_user_prompt


def test_same_model_fails_before_any_gateway_call():
    gateway = FakeGateway([_pass1(), _pass2()])
    result = _run(
        gateway,
        config=PipelineConfig(
            generator_model="same-model",
            grader_model="same-model",
        ),
    )

    assert result.failure is not None
    assert result.failure.code == FailureCode.MODEL_CONFIGURATION_INVALID
    assert gateway.calls == []


def test_pass1_failure_does_not_call_grader():
    gateway = FakeGateway(
        [
            StructuredCallError(
                FailureCode.MODEL_REFUSAL,
                "refused",
                retryable=False,
            )
        ]
    )
    result = _run(gateway)

    assert result.failure is not None
    assert result.failure.code == FailureCode.MODEL_REFUSAL
    assert result.pass1_result is None
    assert len(gateway.calls) == 1


def test_wrong_gateway_contract_type_is_typed_without_calling_grader():
    gateway = FakeGateway([_pass2()])
    result = _run(gateway)

    assert result.failure is not None
    assert result.failure.code == FailureCode.STRUCTURED_OUTPUT_INVALID
    assert result.pass1_result is None
    assert len(gateway.calls) == 1


def test_invalid_pass1_evidence_preserves_result_and_skips_grader():
    gateway = FakeGateway([_pass1(source_block_id="unselected")])
    result = _run(gateway)

    assert result.failure is not None
    assert result.failure.code == FailureCode.EVIDENCE_INVALID
    assert result.pass1_result is not None
    assert result.pass2_assessment is None
    assert len(gateway.calls) == 1


def test_invalid_suitability_evidence_preserves_result_and_skips_grader():
    parsed = _o_route_pass1(GenerationRoute.SPAN_SEEDED)
    invalid_span = parsed.source_suitability.evidence_spans[0].model_copy(
        update={"quote": "불일치"}
    )
    parsed = parsed.model_copy(
        update={
            "source_suitability": parsed.source_suitability.model_copy(
                update={"evidence_spans": (invalid_span,)}
            )
        }
    )
    gateway = FakeGateway([parsed])

    result = _run(gateway)

    assert result.failure is not None
    assert result.failure.code == FailureCode.EVIDENCE_INVALID
    assert result.pass1_result == parsed
    assert result.pass2_assessment is None
    assert len(gateway.calls) == 1


def test_pass2_failure_preserves_pass1_for_resume():
    gateway = FakeGateway(
        [
            _pass1(),
            StructuredCallError(
                FailureCode.SDK_ERROR,
                "SDK retries exhausted",
                retryable=True,
            ),
        ]
    )
    result = _run(gateway)

    assert result.failure is not None
    assert result.failure.code == FailureCode.SDK_ERROR
    assert result.failure.retryable is True
    assert result.pass1_result is not None
    assert result.pass2_assessment is None
    assert len(gateway.calls) == 2


def test_invalid_pass2_evidence_is_typed_and_keeps_both_outputs():
    gateway = FakeGateway([_pass1(), _pass2(block_id="missing")])
    result = _run(gateway)

    assert result.failure is not None
    assert result.failure.code == FailureCode.EVIDENCE_INVALID
    assert result.pass1_result is not None
    assert result.pass2_assessment is not None


def test_label_disagreement_is_kept_and_flagged_for_review():
    gateway = FakeGateway(
        [_pass1(), _pass2(subclause=SubclauseKey.DECISION_REVIEW)]
    )
    result = _run(gateway)

    assert result.succeeded is True
    assert result.comparison is not None
    assert result.comparison.classification_match is True
    assert result.comparison.clause_match is True
    assert result.comparison.subclause_match is False
    assert result.comparison.requires_review is True


def _admin_only_target(
    status: AdminStatus = AdminStatus.APPROVAL_PENDING,
) -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        administrative_statuses=(status,),
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def _admin_only_pass1(
    *,
    status: AdminStatus = AdminStatus.APPROVAL_PENDING,
    paragraph: str = (
        "담당자 기안은 마쳤으며 담당 부서 검토가 끝난 뒤 "
        "최종 승인 절차를 진행할 예정이다."
    ),
) -> Pass1Result:
    source_text = _snapshot().block_text("p1:b0")
    quote = "평가 기준"
    return Pass1Result(
        source_classification=SourceClassification(
            document_type=SemanticDocumentType.BID_NOTICE,
            classification=CsoClassification.O,
            rationale="법적 비공개 사유가 명시되지 않은 공개 업무 문서다.",
        ),
        source_suitability=SourceSuitability(
            evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_spans=(
                EvidenceSpan(block_id="p1:b0", quote=quote),
            ),
            reason_code="ADMIN_CONTEXT",
            rationale="문서 유형과 업무 맥락만 생성에 사용한다.",
        ),
        generation_route=GenerationRoute.ADMINISTRATIVE_AUGMENTED,
        generation_target=_admin_only_target(status),
        generated_document=GeneratedDocumentIR(
            title="검토보고서",
            blocks=(ParagraphBlock(block_id="generated-admin", text=paragraph),),
        ),
    )


def _admin_only_pass2(
    *,
    status: AdminStatus = AdminStatus.APPROVAL_PENDING,
    start: int | None = None,
) -> Pass2Assessment:
    text = _admin_only_pass1(status=status).generated_document.block_text(
        "generated-admin"
    )
    phrase = (
        "최종 승인 절차를 진행할 예정이다"
        if status == AdminStatus.APPROVAL_PENDING
        else "내용을 계속 보완"
    )
    offset = text.index(phrase) if start is None else start
    return Pass2Assessment(
        document_type=SemanticDocumentType.BID_NOTICE,
        classification=CsoClassification.O,
        rationale="법적 비공개 조항 근거는 없고 행정상태만 확인된다.",
    )


def test_admin_only_status_is_written_naturally_by_p1_and_graded_as_effective_s():
    snapshot = _snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None
    gateway = FakeGateway([_admin_only_pass1(), _admin_only_pass2(start=0)])

    result = run_two_pass(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_admin_only_target(),
        gateway=gateway,
        config=PipelineConfig(
            generator_model="generator-model",
            grader_model="grader-model",
        ),
    )

    assert result.succeeded is True
    assert result.pass1_result is not None
    assert "결재진행중" not in result.pass1_result.generated_document.body_text
    assert "결재 진행 중" not in result.pass1_result.generated_document.body_text
    assert "최종 승인 절차를 진행할 예정이다" in (
        result.pass1_result.generated_document.body_text
    )
    assert result.pass2_assessment is not None
    assert result.pass2_assessment.classification == CsoClassification.O
    from rd2.source_generation.contracts import effective_classification
    assert effective_classification(
        result.pass2_assessment.classification,
        _admin_only_target().administrative_statuses,
    ) == CsoClassification.S
    assert result.comparison is not None
    assert result.comparison.classification_match is True
    assert result.comparison.clause_match is True
    assert '"semantic_condition":"담당자 기안 완료' in gateway.calls[0]["user_prompt"]
    assert '"required_phrase"' not in gateway.calls[0]["user_prompt"]


def test_admin_only_target_no_longer_depends_on_p2_detecting_the_status():
    """행정상태는 선언값이라 P2가 못 찾아도 라벨이 흔들리지 않는다.

    이전에는 P2가 본문에서 상태를 탐지해야 effective S가 됐고, 실측에서
    9건 중 4건만 맞았다. 이제 PDF 렌더러가 결재란으로 상태를 구성해 넣고
    라벨은 생성계획이 못 박으므로, 상태 탐지 실패라는 개념 자체가 없다.
    """

    gateway = FakeGateway(
        [
            _admin_only_pass1(
                paragraph="본 검토보고서는 담당 부서에서 내용을 확인하고 있다."
            ),
            Pass2Assessment(
                document_type=SemanticDocumentType.BID_NOTICE,
                classification=CsoClassification.O,
                rationale="법적 비공개 근거는 없다.",
            ),
        ]
    )
    snapshot = _snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None

    result = run_two_pass(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_admin_only_target(),
        gateway=gateway,
        config=PipelineConfig(
            generator_model="generator-model",
            grader_model="grader-model",
        ),
    )

    assert result.succeeded is True
    assert result.comparison is not None
    # P2는 법적 O만 반환했는데도 목표 S와 일치로 집계된다 — 선언된 행정상태가
    # effective_classification을 S로 만들기 때문이다.
    assert result.comparison.classification_match is True
    assert "administrative_status_match" not in type(result.comparison).model_fields
    assert len(gateway.calls) == 2


def test_pass1_cannot_replace_the_requested_administrative_status():
    returned = _admin_only_pass1(
        status=AdminStatus.DRAFT,
        paragraph="본 문서는 초안 단계이며 내용을 계속 보완하고 있다.",
    )
    gateway = FakeGateway([returned])
    snapshot = _snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None

    result = run_two_pass(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_admin_only_target(),
        gateway=gateway,
        config=PipelineConfig(
            generator_model="generator-model",
            grader_model="grader-model",
        ),
    )

    assert result.failure is not None
    assert result.failure.code == FailureCode.ROUTE_INVALID
    assert "must preserve" in result.failure.message
    assert len(gateway.calls) == 1


class FakeResponsesResource:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class RaisingResponsesResource:
    def __init__(self, error):
        self.error = error

    def parse(self, **kwargs):
        raise self.error


def _sdk_response(*, parsed=None, status="completed", output=()):
    return SimpleNamespace(
        id="resp-1",
        status=status,
        output_parsed=parsed,
        output=output,
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
        ),
        _request_id="request-1",
    )


def test_openai_gateway_uses_pinned_responses_parse_contract():
    parsed = _pass1()
    resource = FakeResponsesResource(_sdk_response(parsed=parsed))
    client = SimpleNamespace(responses=resource)
    gateway = OpenAIResponsesGateway(client)

    result = gateway.parse(
        model="generator-model",
        system_prompt="system",
        user_prompt="user",
        response_model=Pass1Result,
        max_output_tokens=123,
    )

    assert result.parsed is parsed
    assert result.response_id == "resp-1"
    assert result.request_id == "request-1"
    assert result.token_usage.total_tokens == 18
    assert resource.kwargs == {
        "model": "generator-model",
        "instructions": "system",
        "input": "user",
        "text_format": Pass1Result,
        "max_output_tokens": 123,
        "store": False,
        "truncation": "disabled",
    }


def test_openai_gateway_rejects_incomplete_or_empty_response():
    incomplete_gateway = OpenAIResponsesGateway(
        SimpleNamespace(
            responses=FakeResponsesResource(
                _sdk_response(parsed=_pass1(), status="incomplete")
            )
        )
    )
    with pytest.raises(StructuredCallError) as incomplete:
        incomplete_gateway.parse(
            model="model",
            system_prompt="system",
            user_prompt="user",
            response_model=Pass1Result,
            max_output_tokens=100,
        )
    assert incomplete.value.code == FailureCode.STRUCTURED_OUTPUT_INVALID

    empty_gateway = OpenAIResponsesGateway(
        SimpleNamespace(
            responses=FakeResponsesResource(_sdk_response(parsed=None))
        )
    )
    with pytest.raises(StructuredCallError) as empty:
        empty_gateway.parse(
            model="model",
            system_prompt="system",
            user_prompt="user",
            response_model=Pass1Result,
            max_output_tokens=100,
        )
    assert empty.value.code == FailureCode.MODEL_RESPONSE_EMPTY


def test_openai_gateway_reports_validation_paths_without_response_values():
    invalid_response = {
        "contract_version": "1.0.0",
        "source_classification": {},
    }
    with pytest.raises(ValidationError) as validation:
        Pass1Result.model_validate(invalid_response)
    gateway = OpenAIResponsesGateway(
        SimpleNamespace(
            responses=RaisingResponsesResource(validation.value),
        )
    )

    with pytest.raises(StructuredCallError) as raised:
        gateway.parse(
            model="model",
            system_prompt="system",
            user_prompt="user",
            response_model=Pass1Result,
            max_output_tokens=100,
        )

    assert raised.value.code == FailureCode.STRUCTURED_OUTPUT_INVALID
    assert "source_classification.document_type: Field required" in str(raised.value)
    assert repr(invalid_response) not in str(raised.value)


def test_retrying_gateway_retries_only_stochastic_contract_violations():
    """실측: 20건 x 3회에서 3회 모두 실패한 케이스가 0건이었다.

    모든 실패가 실행마다 뒤집혔다 — 결정론적 버그가 아니라 확률적 사건이므로
    같은 요청을 한 번 더 보내는 것이 가장 큰 레버다.
    """
    from rd2.source_generation.pipeline import RetryingGateway

    # 1회차 계약 위반 -> 2회차 성공
    inner = FakeGateway([
        StructuredCallError(
            FailureCode.STRUCTURED_OUTPUT_INVALID, "self-contradiction", retryable=False
        ),
        _pass2(),
    ])
    gateway = RetryingGateway(inner, max_attempts=2)
    call = gateway.parse(
        model="grader",
        system_prompt="s",
        user_prompt="u",
        response_model=Pass2Assessment,
        max_output_tokens=100,
    )
    assert call.parsed == _pass2()
    assert len(inner.calls) == 2
    # 재시도가 조용히 일어나지 않도록 기록된다.
    assert gateway.retried == [(FailureCode.STRUCTURED_OUTPUT_INVALID, 1)]


def test_retrying_gateway_does_not_retry_deterministic_failures():
    """다시 보내도 같은 결과인 실패까지 재시도하면 비용만 두 배가 된다."""
    from rd2.source_generation.pipeline import RetryingGateway

    inner = FakeGateway([
        StructuredCallError(
            FailureCode.MODEL_REFUSAL, "content filter", retryable=False
        ),
        _pass2(),
    ])
    gateway = RetryingGateway(inner, max_attempts=3)

    with pytest.raises(StructuredCallError) as excinfo:
        gateway.parse(
            model="grader",
            system_prompt="s",
            user_prompt="u",
            response_model=Pass2Assessment,
            max_output_tokens=100,
        )

    assert excinfo.value.code == FailureCode.MODEL_REFUSAL
    assert len(inner.calls) == 1
    assert gateway.retried == []


def test_retrying_gateway_gives_up_after_max_attempts():
    from rd2.source_generation.pipeline import RetryingGateway

    inner = FakeGateway([
        StructuredCallError(FailureCode.STRUCTURED_OUTPUT_INVALID, "bad", retryable=False)
        for _ in range(3)
    ])
    gateway = RetryingGateway(inner, max_attempts=3)

    with pytest.raises(StructuredCallError):
        gateway.parse(
            model="grader",
            system_prompt="s",
            user_prompt="u",
            response_model=Pass2Assessment,
            max_output_tokens=100,
        )

    assert len(inner.calls) == 3


def test_available_routes_excludes_prerequisites_that_are_not_met():
    """실측: P1이 seed 없이 anchored를 25/50건 선택해 전량 실패했다.

    모델이 추론해야 할 것을 코드가 미리 계산해 목록으로 준다. 걸러지는 건
    내용 판단이 필요 없는 것들뿐이다.
    """
    from rd2.source_generation.pipeline import available_routes

    legal_target = _target()

    # seed가 없으면 anchored는 목록에 없다.
    without_seed = available_routes(
        target=legal_target, sensitive_seed=None, has_synthetic_generator=True
    )
    assert GenerationRoute.ANCHORED not in without_seed

    with_seed = available_routes(
        target=legal_target, sensitive_seed="민감 seed", has_synthetic_generator=True
    )
    assert GenerationRoute.ANCHORED in with_seed

    # 합성 실행기가 없으면 fully_synthetic도 목록에 없다.
    no_generator = available_routes(
        target=legal_target, sensitive_seed=None, has_synthetic_generator=False
    )
    assert GenerationRoute.FULLY_SYNTHETIC not in no_generator

    # 제6호는 비식별화 구현 전까지 span_seeded가 막혀 있다.
    clause6 = GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=SubclauseKey.PERSONNEL_PII,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )
    assert GenerationRoute.SPAN_SEEDED not in available_routes(
        target=clause6, sensitive_seed="seed", has_synthetic_generator=True
    )

    # 행정상태 단독 target은 administrative_augmented로만 성립한다.
    admin_only = available_routes(
        target=_admin_only_target(), sensitive_seed="seed", has_synthetic_generator=True
    )
    assert admin_only == (GenerationRoute.ADMINISTRATIVE_AUGMENTED,)

    # 원문 내용 판단이 필요한 route는 코드가 미리 배제하지 않는다.
    assert GenerationRoute.SOURCE_ALIGNED in without_seed


def test_generation_plan_tells_the_model_which_routes_are_available():
    gateway = FakeGateway([_pass1(), _pass2()])
    _run(gateway)

    plan = gateway.calls[0]["user_prompt"]
    assert "available_routes" in plan
    assert "목록에 없는 route는" in plan
    # seed를 안 넘긴 실행이므로 anchored가 목록에서 빠져 있어야 한다.
    payload = json.loads(plan.split("[GENERATION PLAN]\n")[1].split("\n\n[ASSESSMENT")[0])
    assert "anchored" not in payload["available_routes"]["routes"]
    assert "source_aligned" in payload["available_routes"]["routes"]


def test_confidential_clauses_are_not_derived_from_public_sources():
    """제1~4호는 공개 원문에서 파생시키지 않고 완전 생성으로 보낸다.

    span_seeded와 anchored는 공개(O) 원문을 재료로 C 목표를 만드는 경로다.
    기밀·국방·외교·수사 내용은 공개 문서에 애초에 없으므로 근거 없는
    counterfactual이 된다.
    """
    from rd2.source_generation.pipeline import CONFIDENTIAL_CLAUSES, available_routes

    for clause, subclause in (
        (ClauseNumber.CLAUSE_1, SubclauseKey.LEGAL_SECRET),
        (ClauseNumber.CLAUSE_2, SubclauseKey.SECURITY_DEFENSE),
        (ClauseNumber.CLAUSE_3, SubclauseKey.LIFE_BODY),
        (ClauseNumber.CLAUSE_4, SubclauseKey.PROSECUTION),
    ):
        target = GenerationTarget(
            classification=TargetClassification.C,
            clause_no=clause,
            subclause_key=subclause,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )
        routes = available_routes(
            target=target, sensitive_seed="민감 seed", has_synthetic_generator=True
        )
        assert GenerationRoute.SPAN_SEEDED not in routes, clause
        assert GenerationRoute.ANCHORED not in routes, clause
        assert GenerationRoute.FULLY_SYNTHETIC in routes, clause
        # 원문이 진짜로 C/S인 경우는 여전히 성립하므로 남겨둔다.
        assert GenerationRoute.SOURCE_ALIGNED in routes, clause

    # 제5~8호는 그대로 공개 원문을 재료로 쓸 수 있다.
    for clause, subclause in (
        (ClauseNumber.CLAUSE_5, SubclauseKey.BID_CONTRACT),
        (ClauseNumber.CLAUSE_7, SubclauseKey.UNIT_COST),
        (ClauseNumber.CLAUSE_8, SubclauseKey.CORNERING),
    ):
        target = GenerationTarget(
            classification=TargetClassification.S,
            clause_no=clause,
            subclause_key=subclause,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )
        routes = available_routes(
            target=target, sensitive_seed="민감 seed", has_synthetic_generator=True
        )
        assert GenerationRoute.SPAN_SEEDED in routes, clause
        assert GenerationRoute.ANCHORED in routes, clause

    assert len(CONFIDENTIAL_CLAUSES) == 4
