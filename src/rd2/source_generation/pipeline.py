"""유형 판별기 → 결정론적 플래너 → 생성기 → blind 정합성 판별기."""

from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from dataclasses import dataclass
from datetime import date
from typing import Callable, Generic, Protocol, TypeVar, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    InternalServerError,
    LengthFinishReasonError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    clause_of_subclause,
    expected_classification,
)
from rd2.source_generation.administrative import (
    administrative_status_generation_requirements,
    validate_status_date_coherence,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    CallReceipt,
    ConsistencyAssessment,
    ConsistencyComparison,
    DocumentPipelineResult,
    DocumentSelection,
    EvidenceSpan,
    FailureCode,
    FailureStage,
    GeneratedDocumentIR,
    GenerationArtifact,
    GenerationMode,
    GenerationPlan,
    GenerationProvenance,
    GenerationRoute,
    GenerationTarget,
    PLANNER_POLICY_VERSION,
    RepairCode,
    SensitiveConsistencyAssessment,
    SensitiveMonitorDecision,
    SensitivePipelineStatus,
    SensitiveVerdict,
    SourceAssessment,
    SourceDocumentSnapshot,
    SourceEvidenceLevel,
    StageFailure,
    TargetClassification,
    TokenUsage,
    document_form_matches,
    effective_classification,
)
from rd2.source_generation.document_select import (
    DocumentSelectionError,
    SelectionConfig,
    render_full_source,
    render_selected_source,
)
from rd2.source_generation.document_form_compatibility import (
    FORM_SUBCLAUSE_COMPATIBILITY_VERSION,
    FormSubclauseCompatibility,
    form_subclause_compatibility,
)
from rd2.source_generation.evidence import validate_evidence_quotes
from rd2.source_generation.legacy_synthetic import (
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.seed_assembly import (
    SEED_ASSEMBLY_VERSION,
    build_sensitive_seed,
)
from rd2.source_generation.prompts import (
    PromptBundle,
    build_prompt_bundle,
    render_classifier_user_prompt,
    render_generator_user_prompt,
    render_validator_user_prompt,
)
from rd2.source_generation.sensitive_policy import validate_sensitive_assessment

ParsedT = TypeVar("ParsedT", bound=BaseModel)


@dataclass(frozen=True)
class StructuredCall(Generic[ParsedT]):
    parsed: ParsedT
    response_id: str
    request_id: str | None = None
    token_usage: TokenUsage | None = None


class StructuredCallError(RuntimeError):
    def __init__(
        self,
        code: FailureCode,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StructuredOutputGateway(Protocol):
    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ParsedT],
        max_output_tokens: int,
    ) -> StructuredCall[ParsedT]: ...


class OpenAIResponsesGateway:
    """OpenAI SDK retry만 사용하는 동기 structured-output adapter."""

    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ParsedT],
        max_output_tokens: int,
    ) -> StructuredCall[ParsedT]:
        try:
            response = self._client.responses.parse(
                model=model,
                instructions=system_prompt,
                input=user_prompt,
                text_format=response_model,
                max_output_tokens=max_output_tokens,
                store=False,
                truncation="disabled",
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError) as exc:
            raise StructuredCallError(
                FailureCode.SDK_ERROR,
                f"{type(exc).__name__}: OpenAI SDK retries exhausted",
                retryable=True,
            ) from exc
        except APIStatusError as exc:
            raise StructuredCallError(
                FailureCode.SDK_ERROR,
                f"{type(exc).__name__}: OpenAI request rejected",
                retryable=False,
            ) from exc
        except ValidationError as exc:
            raise StructuredCallError(
                FailureCode.STRUCTURED_OUTPUT_INVALID,
                (
                    "OpenAI response did not satisfy the Pydantic contract: "
                    f"{_validation_error_summary(exc)}"
                ),
                retryable=False,
            ) from exc
        except LengthFinishReasonError as exc:
            raise StructuredCallError(
                FailureCode.STRUCTURED_OUTPUT_INVALID,
                "OpenAI structured output exceeded max_output_tokens",
                retryable=False,
            ) from exc
        except ContentFilterFinishReasonError as exc:
            raise StructuredCallError(
                FailureCode.MODEL_REFUSAL,
                "OpenAI content filter stopped the structured-output request",
                retryable=False,
            ) from exc
        except OpenAIError as exc:
            raise StructuredCallError(
                FailureCode.SDK_ERROR,
                f"{type(exc).__name__}: OpenAI SDK call failed",
                retryable=False,
            ) from exc

        # Streaming/background mode는 쓰지 않지만, SDK parsing이 terminal status보다
        # 먼저 일어나는 경계 사례를 막기 위해 completed를 별도로 확인한다.
        if response.status != "completed":
            raise StructuredCallError(
                FailureCode.STRUCTURED_OUTPUT_INVALID,
                f"OpenAI response ended with status={response.status!r}",
                retryable=False,
            )

        parsed = response.output_parsed
        if parsed is None:
            if _response_has_refusal(response):
                raise StructuredCallError(
                    FailureCode.MODEL_REFUSAL,
                    "OpenAI model refused the structured-output request",
                    retryable=False,
                )
            raise StructuredCallError(
                FailureCode.MODEL_RESPONSE_EMPTY,
                "OpenAI response contained no parsed structured output",
                retryable=False,
            )
        if not isinstance(parsed, response_model):
            raise StructuredCallError(
                FailureCode.STRUCTURED_OUTPUT_INVALID,
                (
                    f"OpenAI parsed {type(parsed).__name__}; "
                    f"expected {response_model.__name__}"
                ),
                retryable=False,
            )

        usage = None
        if response.usage is not None:
            usage = TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                total_tokens=response.usage.total_tokens,
            )
        return StructuredCall(
            parsed=parsed,
            response_id=response.id,
            request_id=getattr(response, "_request_id", None),
            token_usage=usage,
        )


#: 재시도가 의미 있는 실패. 모델이 확률적으로 자기모순 응답을 낼 때만 해당한다.
#:
#: 20건 fixture를 동일 조건으로 3회 돌린 결과 3회 모두 실패하는 케이스가
#: 0건이었다 — 모든 실패가 실행마다 뒤집혔다. 즉 이 실패들은 결정론적 버그가
#: 아니라 확률적 사건이고, 같은 입력을 한 번 더 보내는 것만으로 상당수가
#: 해소된다. 설정·소스·모델 구성 문제처럼 다시 보내도 같은 결과인 실패는
#: 여기 넣지 않는다.
STOCHASTIC_FAILURE_CODES: frozenset[FailureCode] = frozenset(
    {
        FailureCode.STRUCTURED_OUTPUT_INVALID,
        FailureCode.MODEL_RESPONSE_EMPTY,
        FailureCode.SDK_ERROR,
    }
)


class RetryingGateway:
    """확률적 계약 위반에만 같은 요청을 다시 보내는 gateway decorator.

    설계 문서의 "executor refusal/SDK transient error: 같은 route만 제한
    재시도" 규칙을 따른다 — route나 target을 바꿔 조용히 우회하지 않고,
    **완전히 같은 요청**을 정해진 횟수만큼만 다시 보낸다.
    """

    def __init__(
        self,
        inner: StructuredOutputGateway,
        *,
        max_attempts: int = 2,
        retry_codes: frozenset[FailureCode] = STOCHASTIC_FAILURE_CODES,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._inner = inner
        self._max_attempts = max_attempts
        self._retry_codes = retry_codes
        #: (code, 시도횟수) 기록. 재시도가 조용히 일어나지 않게 남긴다.
        self.retried: list[tuple[FailureCode, int]] = []

    def parse(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ParsedT],
        max_output_tokens: int,
    ) -> StructuredCall[ParsedT]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._inner.parse(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                    max_output_tokens=max_output_tokens,
                )
            except StructuredCallError as exc:
                if exc.code not in self._retry_codes or attempt == self._max_attempts:
                    raise
                self.retried.append((exc.code, attempt))
        raise AssertionError("unreachable")  # pragma: no cover


def _validation_error_summary(exc: ValidationError, *, limit: int = 8) -> str:
    summaries: list[str] = []
    for error in exc.errors(include_input=False, include_url=False)[:limit]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        summaries.append(f"{location}: {error['msg']} [{error['type']}]")
    remaining = exc.error_count() - len(summaries)
    if remaining > 0:
        summaries.append(f"... and {remaining} more validation error(s)")
    return "; ".join(summaries)


def _response_has_refusal(response: object) -> bool:
    for output in getattr(response, "output", ()):
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()):
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def default_openai_gateway(
    *,
    api_key: str | None = None,
    max_attempts: int = 2,
) -> StructuredOutputGateway:
    """기본 gateway는 확률적 계약 위반을 한 번 재시도한다.

    ``max_attempts=1``을 주면 재시도 없이 원래 동작으로 돌아간다.
    """

    resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not resolved_key:
        raise RuntimeError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다 — .env 또는 실행 "
            "환경에 키를 설정하세요."
        )
    gateway = OpenAIResponsesGateway(OpenAI(api_key=resolved_key))
    if max_attempts == 1:
        return gateway
    return RetryingGateway(gateway, max_attempts=max_attempts)


@dataclass(frozen=True)
class PipelineConfig:
    """세 모델의 역할과 출력 한도를 고정하는 실행 설정.

    판별기와 생성기는 같은 모델이어도 되지만, blind 정합성 판별기는 두 모델과
    달라야 한다. 이 조건은 각 실행 함수에서도 typed failure로 다시 확인한다.
    """

    classifier_model: str
    generator_model: str
    validator_model: str
    max_classifier_output_tokens: int = 4_000
    max_generator_output_tokens: int = 8_000
    max_validator_output_tokens: int = 4_000
    source_sensitive_mode: bool = False
    reference_date: date | None = None

    def __post_init__(self) -> None:
        model_ids = (
            self.classifier_model,
            self.generator_model,
            self.validator_model,
        )
        if any(not model_id.strip() for model_id in model_ids):
            raise ValueError("classifier, generator, and validator models must not be blank")
        token_limits = (
            self.max_classifier_output_tokens,
            self.max_generator_output_tokens,
            self.max_validator_output_tokens,
        )
        if any(limit < 1 for limit in token_limits):
            raise ValueError("max output tokens must be positive")


@dataclass(frozen=True)
class ClassificationExecution:
    assessment: SourceAssessment | None = None
    receipt: CallReceipt | None = None
    failure: StageFailure | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and self.assessment is None:
            raise ValueError("classification receipt requires a source assessment")
        if self.failure is None and (self.assessment is None or self.receipt is None):
            raise ValueError(
                "successful classification requires an assessment and receipt"
            )
        if self.failure is not None and self.failure.stage not in {
            FailureStage.MANIFEST,
            FailureStage.SELECTION,
            FailureStage.CLASSIFICATION,
        }:
            raise ValueError("classification execution has an invalid failure stage")

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class GenerationExecution:
    artifact: GenerationArtifact | None = None
    receipt: CallReceipt | None = None
    failure: StageFailure | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and self.artifact is None:
            raise ValueError("generation receipt requires a generation artifact")
        if self.failure is None and self.artifact is None:
            raise ValueError("successful generation requires a generation artifact")
        if self.failure is not None and self.failure.stage not in {
            FailureStage.MANIFEST,
            FailureStage.SELECTION,
            FailureStage.GENERATION,
        }:
            raise ValueError("generation execution has an invalid failure stage")

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class ConsistencyValidationExecution:
    assessment: ConsistencyAssessment | None = None
    receipt: CallReceipt | None = None
    comparison: ConsistencyComparison | None = None
    repair_codes: tuple[RepairCode, ...] = ()
    failure: StageFailure | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and self.assessment is None:
            raise ValueError("validation receipt requires a consistency assessment")
        if self.comparison is not None and self.assessment is None:
            raise ValueError("comparison requires a consistency assessment")
        if self.failure is None and (
            self.assessment is None
            or self.receipt is None
            or self.comparison is None
        ):
            raise ValueError(
                "successful validation requires assessment, receipt, and comparison"
            )
        if self.failure is not None and self.failure.stage not in {
            FailureStage.MANIFEST,
            FailureStage.VALIDATION,
        }:
            raise ValueError("validation execution has an invalid failure stage")
        if len(self.repair_codes) != len(set(self.repair_codes)):
            raise ValueError("validation repair codes must be unique")

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class SourceSensitivePipelineRun:
    """제6호 생성 재시도의 전체 lineage.

    유형 판별과 계획은 한 번만 실행하며, ``attempts``에는 생성·검증 결과만
    차례로 쌓인다.
    """

    status: SensitivePipelineStatus
    attempts: tuple[DocumentPipelineResult, ...]

    def __post_init__(self) -> None:
        if not self.attempts:
            raise ValueError("source-sensitive run requires at least one attempt")

    @property
    def final_result(self) -> DocumentPipelineResult:
        return self.attempts[-1]


class GenerationPlanningError(ValueError):
    """결정론적 계획 단계에서 발생한 typed 오류."""

    def __init__(
        self,
        code: FailureCode,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _failure(
    *,
    stage: FailureStage,
    code: FailureCode,
    message: str,
    retryable: bool = False,
) -> StageFailure:
    return StageFailure(
        stage=stage,
        code=code,
        retryable=retryable,
        message=message,
    )


def _receipt(
    *,
    stage: FailureStage,
    model_id: str,
    call: StructuredCall[BaseModel],
) -> CallReceipt:
    return CallReceipt(
        stage=stage,
        model_id=model_id,
        response_id=call.response_id,
        request_id=call.request_id,
        token_usage=call.token_usage,
    )


def _configuration_failure(
    config: PipelineConfig,
    *,
    stage: FailureStage,
) -> StageFailure | None:
    if config.validator_model in {
        config.classifier_model,
        config.generator_model,
    }:
        return _failure(
            stage=stage,
            code=FailureCode.MODEL_CONFIGURATION_INVALID,
            message=(
                "validator_model must differ from classifier_model and "
                "generator_model"
            ),
        )
    return None


def _resolved_prompt_bundle(
    selection_config: SelectionConfig,
    prompt_bundle: PromptBundle | None,
) -> PromptBundle:
    resolved = prompt_bundle or build_prompt_bundle(selection_config)
    if resolved.selection_config != selection_config:
        raise ValueError(
            "prompt bundle selection config does not match pipeline config"
        )
    return resolved


def _selected_source_resolver(
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
) -> Callable[[str], str]:
    selected_ids = set(selection.selected_block_ids)

    def resolve(block_id: str) -> str:
        if block_id not in selected_ids:
            raise ValueError(
                f"source evidence references unselected block {block_id!r}"
            )
        return snapshot.block_text(block_id)

    return resolve


def _assessment_scope(selection: DocumentSelection) -> AssessmentScope:
    if selection.truncated:
        return AssessmentScope.SELECTED_VIEW_ONLY
    return AssessmentScope.FULL_DOCUMENT


def _model_payload(model: BaseModel) -> dict[str, object]:
    return model.model_dump(
        mode="json",
        exclude_computed_fields=True,
    )


def model_sha256(model: BaseModel) -> str:
    """Pydantic 계약 산출물의 canonical content hash."""

    return canonical_sha256(
        _model_payload(model),
        normalization_version=NORMALIZATION_VERSION,
    )


_PLANNER_POLICY_PAYLOAD = {
    "version": PLANNER_POLICY_VERSION,
    "source_s": {
        "route": GenerationRoute.SOURCE_ALIGNED.value,
        "required_evidence": SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE.value,
    },
    "source_o": {
        SourceEvidenceLevel.DIRECT_SENSITIVE_SPAN.value: (
            GenerationRoute.SPAN_SEEDED.value
        ),
        SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY.value: (
            GenerationRoute.ANCHORED.value
        ),
        SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE.value: (
            GenerationRoute.FULLY_SYNTHETIC.value
        ),
    },
    "admin_only": GenerationRoute.ADMINISTRATIVE_AUGMENTED.value,
    "compatible_subclause_required": True,
    # ``primary_subclause``는 원문에 가장 가까운 세부유형이지 생성 목표가 아니다.
    # 반사실 생성은 원문 판별 결과와 다른 목표를 의도적으로 구현하므로, 두 값이
    # 달라도 요청 목표를 그대로 유지한다.
    "incompatible_request": "preserve_requested_target",
    "form_subclause_compatibility": {
        "version": FORM_SUBCLAUSE_COMPATIBILITY_VERSION,
        "conflict": "substitute_first_non_conflicting_source_candidate",
        "no_candidate": FailureCode.SOURCE_INCOMPATIBLE.value,
    },
    "seed_assembly": SEED_ASSEMBLY_VERSION,
}

PLANNER_POLICY_SHA256 = canonical_sha256(
    _PLANNER_POLICY_PAYLOAD,
    normalization_version=NORMALIZATION_VERSION,
)


def _source_aligned_target(
    assessment: SourceAssessment,
    requested_target: GenerationTarget,
) -> GenerationTarget:
    source = assessment.source_classification
    if source.classification != CsoClassification.S:
        raise GenerationPlanningError(
            FailureCode.SOURCE_INCOMPATIBLE,
            "this pipeline supports only S/O source assessments",
        )
    assert source.clause_no is not None
    assert source.subclause_key is not None
    return GenerationTarget(
        classification=source.classification.value,
        clause_no=source.clause_no,
        subclause_key=source.subclause_key,
        administrative_statuses=requested_target.administrative_statuses,
        generation_mode=GenerationMode.SOURCE_ALIGNED,
    )


def _resolve_form_subclause_conflict(
    *,
    assessment: SourceAssessment,
    target: GenerationTarget,
) -> GenerationTarget:
    """Keep an impossible form/target pair out of the generator prompt.

    For an O-source counterfactual route, the classifier has already ranked the
    subclauses that fit the source context.  Choose the first candidate that is
    not a hard form conflict.  For an S-source route the source subclause is the
    legal evidence itself, so silently changing it would falsify provenance;
    reject that source instead.
    """

    subclause_key = target.subclause_key
    if subclause_key is None:
        return target

    document_form = assessment.source_classification.document_form
    if (
        form_subclause_compatibility(document_form, subclause_key)
        is not FormSubclauseCompatibility.CONFLICT
    ):
        return target

    if assessment.source_classification.classification == CsoClassification.S:
        raise GenerationPlanningError(
            FailureCode.SOURCE_INCOMPATIBLE,
            (
                "source legal target conflicts with its locked document form: "
                f"{document_form.value} × {subclause_key.value}"
            ),
        )

    for candidate in assessment.candidate_subclauses:
        if (
            form_subclause_compatibility(document_form, candidate)
            is FormSubclauseCompatibility.CONFLICT
        ):
            continue
        candidate_clause = clause_of_subclause(candidate)
        return GenerationTarget(
            classification=TargetClassification(
                expected_classification(candidate_clause).value
            ),
            clause_no=candidate_clause,
            subclause_key=candidate,
            administrative_statuses=target.administrative_statuses,
            generation_mode=target.generation_mode,
        )

    raise GenerationPlanningError(
        FailureCode.SOURCE_INCOMPATIBLE,
        (
            "no source-compatible subclause can preserve locked document form "
            f"{document_form.value}; rejected target={subclause_key.value}"
        ),
    )


def build_generation_plan(
    *,
    assessment: SourceAssessment,
    requested_target: GenerationTarget,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    sensitive_seed: str | None = None,
    has_synthetic_generator: bool = False,
) -> GenerationPlan:
    """분류 사실과 실행 가능 조건만으로 생성 목표와 경로를 하나로 잠근다."""

    if requested_target.generation_mode != GenerationMode.COUNTERFACTUAL:
        raise GenerationPlanningError(
            FailureCode.MANIFEST_INVALID,
            "requested fallback target requires generation_mode=counterfactual",
        )
    if assessment.source_classification.classification == CsoClassification.C:
        raise GenerationPlanningError(
            FailureCode.SOURCE_INCOMPATIBLE,
            "clauses 1-4 are outside this S/O pipeline",
        )

    source = assessment.source_classification
    suitability = assessment.source_suitability
    if source.classification == CsoClassification.S:
        route = GenerationRoute.SOURCE_ALIGNED
        final_target = _source_aligned_target(assessment, requested_target)
        final_target = _resolve_form_subclause_conflict(
            assessment=assessment,
            target=final_target,
        )
    else:
        final_target = requested_target
        admin_only = requested_target.clause_no is None

        final_target = _resolve_form_subclause_conflict(
            assessment=assessment,
            target=final_target,
        )

        if admin_only:
            if (
                suitability.evidence_level
                != SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY
            ):
                raise GenerationPlanningError(
                    FailureCode.ROUTE_INVALID,
                    "admin-only generation requires contextual_anchor_only evidence",
                )
            route = GenerationRoute.ADMINISTRATIVE_AUGMENTED
        elif (
            suitability.evidence_level
            == SourceEvidenceLevel.DIRECT_SENSITIVE_SPAN
        ):
            route = GenerationRoute.SPAN_SEEDED
        elif (
            suitability.evidence_level
            == SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY
        ):
            # seed는 호출자가 줄 수도 있고, 없으면 판별 결과와 목표만으로
            # 기계적으로 조립한다(``seed_assembly``). 조립이 항상 가능하므로
            # "seed가 없어서 실패"는 조항 없는 목표에서만 남는다.
            if (sensitive_seed is None or not sensitive_seed.strip()) and (
                build_sensitive_seed(assessment, final_target) is None
            ):
                raise GenerationPlanningError(
                    FailureCode.ROUTE_INVALID,
                    "contextual_anchor_only generation requires a sensitive seed",
                )
            route = GenerationRoute.ANCHORED
        elif (
            suitability.evidence_level
            == SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE
        ):
            if not has_synthetic_generator:
                raise GenerationPlanningError(
                    FailureCode.ROUTE_INVALID,
                    (
                        "no_usable_public_source requires a source-free "
                        "synthetic generator"
                    ),
                )
            route = GenerationRoute.FULLY_SYNTHETIC
        else:
            raise GenerationPlanningError(
                FailureCode.ROUTE_INVALID,
                (
                    "O source cannot use evidence level "
                    f"{suitability.evidence_level.value!r}"
                ),
            )

    plan = GenerationPlan(
        requested_target=requested_target,
        final_target=final_target,
        generation_route=route,
        source_assessment_sha256=model_sha256(assessment),
        source_sha256=snapshot.source_sha256,
        selection_sha256=selection.selection_sha256,
        planner_policy_sha256=PLANNER_POLICY_SHA256,
    )
    try:
        plan.validate_against(assessment)
    except ValueError as exc:
        raise GenerationPlanningError(
            FailureCode.ROUTE_INVALID,
            str(exc),
        ) from exc
    return plan


def _canonicalize_source_assessment(
    assessment: SourceAssessment,
    *,
    block_text: Callable[[str], str],
) -> SourceAssessment:
    source_classification = assessment.source_classification.model_copy(
        update={
            "evidence_spans": validate_evidence_quotes(
                assessment.source_classification.evidence_spans,
                block_text,
            )
        }
    )
    source_suitability = assessment.source_suitability.model_copy(
        update={
            "evidence_spans": validate_evidence_quotes(
                assessment.source_suitability.evidence_spans,
                block_text,
            )
        }
    )
    validated_slot_spans = validate_evidence_quotes(
        (slot.evidence_span for slot in assessment.available_slots),
        block_text,
    )
    available_slots = tuple(
        slot.model_copy(update={"evidence_span": span})
        for slot, span in zip(
            assessment.available_slots,
            validated_slot_spans,
            strict=True,
        )
    )
    canonicalized = SourceAssessment.model_validate(
        {
            **assessment.model_dump(
                mode="python",
                exclude={
                    "source_classification",
                    "source_suitability",
                    "available_slots",
                },
                exclude_computed_fields=True,
            ),
            "source_classification": source_classification,
            "source_suitability": source_suitability,
            "available_slots": available_slots,
        }
    )
    canonicalized.validate_evidence_against(block_text)
    return canonicalized


def execute_classification(
    *,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
) -> ClassificationExecution:
    """선택된 원문만 보고 문서형식과 S/O 원문 사실을 판정한다."""

    configuration_failure = _configuration_failure(
        config,
        stage=FailureStage.CLASSIFICATION,
    )
    if configuration_failure is not None:
        return ClassificationExecution(failure=configuration_failure)

    resolved_selection_config = selection_config or SelectionConfig()
    try:
        resolved_prompt_bundle = _resolved_prompt_bundle(
            resolved_selection_config,
            prompt_bundle,
        )
    except ValueError as exc:
        return ClassificationExecution(
            failure=_failure(
                stage=FailureStage.MANIFEST,
                code=FailureCode.MODEL_CONFIGURATION_INVALID,
                message=str(exc),
            )
        )

    try:
        source_document = render_selected_source(
            snapshot,
            selection,
            resolved_selection_config,
        )
    except DocumentSelectionError as exc:
        return ClassificationExecution(
            failure=_failure(
                stage=FailureStage.SELECTION,
                code=exc.code,
                message=str(exc),
            )
        )

    definition = resolved_prompt_bundle.definition("classifier")
    try:
        call = gateway.parse(
            model=config.classifier_model,
            system_prompt=definition.system_prompt,
            user_prompt=render_classifier_user_prompt(
                source_document,
                assessment_scope=_assessment_scope(selection).value,
            ),
            response_model=SourceAssessment,
            max_output_tokens=config.max_classifier_output_tokens,
        )
    except StructuredCallError as exc:
        return ClassificationExecution(
            failure=_failure(
                stage=FailureStage.CLASSIFICATION,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            )
        )
    except Exception as exc:  # noqa: BLE001 - isolate one source document
        return ClassificationExecution(
            failure=_failure(
                stage=FailureStage.CLASSIFICATION,
                code=FailureCode.SDK_ERROR,
                message=f"{type(exc).__name__}: structured-output gateway failed",
            )
        )

    if not isinstance(call.parsed, SourceAssessment):
        return ClassificationExecution(
            failure=_failure(
                stage=FailureStage.CLASSIFICATION,
                code=FailureCode.STRUCTURED_OUTPUT_INVALID,
                message="classifier gateway returned the wrong contract type",
            )
        )
    assessment = cast(SourceAssessment, call.parsed)
    receipt = _receipt(
        stage=FailureStage.CLASSIFICATION,
        model_id=config.classifier_model,
        call=call,
    )
    try:
        assessment = _canonicalize_source_assessment(
            assessment,
            block_text=_selected_source_resolver(snapshot, selection),
        )
        expected_scope = _assessment_scope(selection)
        if assessment.source_suitability.assessment_scope != expected_scope:
            raise ValueError(
                "source suitability assessment_scope does not match selection"
            )
        if assessment.source_classification.classification == CsoClassification.C:
            raise ValueError(
                "classifier must use only S/O for clauses 5-8 in this pipeline"
            )
    except ValueError as exc:
        return ClassificationExecution(
            assessment=assessment,
            receipt=receipt,
            failure=_failure(
                stage=FailureStage.CLASSIFICATION,
                code=FailureCode.EVIDENCE_INVALID,
                message=str(exc),
            ),
        )

    return ClassificationExecution(
        assessment=assessment,
        receipt=receipt,
    )


def _validate_locked_plan(
    *,
    assessment: SourceAssessment,
    plan: GenerationPlan,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
) -> None:
    if plan.source_assessment_sha256 != model_sha256(assessment):
        raise ValueError("generation plan assessment hash no longer matches")
    if plan.source_sha256 != snapshot.source_sha256:
        raise ValueError("generation plan source hash no longer matches")
    if plan.selection_sha256 != selection.selection_sha256:
        raise ValueError("generation plan selection hash no longer matches")
    if plan.planner_policy_sha256 != PLANNER_POLICY_SHA256:
        raise ValueError("generation plan policy hash no longer matches")
    plan.validate_against(assessment)


def _generation_plan_prompt_json(
    plan: GenerationPlan,
    *,
    reference_date: date | None,
) -> str:
    payload = {
        "locked_plan": _model_payload(plan),
        "administrative_status_generation_requirements": (
            administrative_status_generation_requirements(
                plan.final_target.administrative_statuses,
                reference_date=reference_date,
            )
        ),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_generation_provenance(
    *,
    assessment: SourceAssessment,
    plan: GenerationPlan,
    selection: DocumentSelection,
    sensitive_seed: str | None,
    fully_synthetic_context: FullySyntheticContext | None,
) -> GenerationProvenance:
    route = plan.generation_route
    uses_source_evidence = route != GenerationRoute.FULLY_SYNTHETIC
    seed_hash = None
    if route == GenerationRoute.ANCHORED:
        if sensitive_seed is None or not sensitive_seed.strip():
            raise ValueError("anchored generation requires a sensitive seed")
        seed_hash = sha256(sensitive_seed.encode("utf-8")).hexdigest()

    return GenerationProvenance(
        generation_route=route,
        source_evidence_level=assessment.source_suitability.evidence_level,
        reason_code=assessment.source_suitability.reason_code,
        requested_target=plan.requested_target,
        final_target=plan.final_target,
        selection_sha256=selection.selection_sha256,
        uses_source_evidence=uses_source_evidence,
        validated_evidence_spans=(
            assessment.source_suitability.evidence_spans
            if uses_source_evidence
            else ()
        ),
        sensitive_seed_sha256=seed_hash,
        synthetic_scenario_id=(
            fully_synthetic_context.scenario_id
            if route == GenerationRoute.FULLY_SYNTHETIC
            and fully_synthetic_context is not None
            else None
        ),
    )


def execute_generation(
    *,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    assessment: SourceAssessment,
    plan: GenerationPlan,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
    sensitive_seed: str | None = None,
    fully_synthetic_generator: FullySyntheticDocumentGenerator | None = None,
    fully_synthetic_context: FullySyntheticContext | None = None,
    attempt_index: int = 1,
    parent_generation_sha256: str | None = None,
    repair_codes: tuple[RepairCode, ...] = (),
) -> GenerationExecution:
    """잠긴 계획을 바꾸지 않고 ``GeneratedDocumentIR`` 하나만 만든다."""

    configuration_failure = _configuration_failure(
        config,
        stage=FailureStage.GENERATION,
    )
    if configuration_failure is not None:
        return GenerationExecution(failure=configuration_failure)

    resolved_selection_config = selection_config or SelectionConfig()
    try:
        resolved_prompt_bundle = _resolved_prompt_bundle(
            resolved_selection_config,
            prompt_bundle,
        )
        # 생성기는 전체 원문을 보지만 selection과 source가 중간에 바뀌지
        # 않았는지는 먼저 검증한다.
        render_selected_source(snapshot, selection, resolved_selection_config)
        _validate_locked_plan(
            assessment=assessment,
            plan=plan,
            snapshot=snapshot,
            selection=selection,
        )
        if attempt_index < 1:
            raise ValueError("attempt_index must be at least 1")
        if attempt_index == 1 and (
            parent_generation_sha256 is not None or repair_codes
        ):
            raise ValueError(
                "first generation attempt cannot have parent hash or repair codes"
            )
        if attempt_index > 1 and (
            parent_generation_sha256 is None or not repair_codes
        ):
            raise ValueError(
                "retry generation requires parent hash and repair codes"
            )
    except DocumentSelectionError as exc:
        return GenerationExecution(
            failure=_failure(
                stage=FailureStage.SELECTION,
                code=exc.code,
                message=str(exc),
            )
        )
    except ValueError as exc:
        return GenerationExecution(
            failure=_failure(
                stage=FailureStage.GENERATION,
                code=FailureCode.ROUTE_INVALID,
                message=str(exc),
            )
        )

    generated_document: GeneratedDocumentIR
    receipt: CallReceipt | None = None
    if plan.generation_route == GenerationRoute.FULLY_SYNTHETIC:
        if fully_synthetic_generator is None or fully_synthetic_context is None:
            return GenerationExecution(
                failure=_failure(
                    stage=FailureStage.GENERATION,
                    code=FailureCode.ROUTE_INVALID,
                    message=(
                        "fully_synthetic route requires a source-free generator "
                        "and execution context"
                    ),
                )
            )
        try:
            generated_document = fully_synthetic_generator.generate(
                target=plan.final_target,
                context=fully_synthetic_context,
            )
        except ValueError as exc:
            return GenerationExecution(
                failure=_failure(
                    stage=FailureStage.GENERATION,
                    code=FailureCode.ROUTE_INVALID,
                    message=f"fully_synthetic generator rejected the plan: {exc}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate one generator
            return GenerationExecution(
                failure=_failure(
                    stage=FailureStage.GENERATION,
                    code=FailureCode.SDK_ERROR,
                    message=f"{type(exc).__name__}: fully_synthetic generator failed",
                )
            )
    else:
        sensitive = plan.final_target.clause_no == ClauseNumber.CLAUSE_6
        # document_form은 classifier가 이미 잠갔다(assessment.document_form).
        # 17개 형식을 전부 담은 정적 definition("generator") 대신, 그 하나로
        # 필터링된 정의를 매 호출마다 만든다 — fingerprint가 실제로 보낸
        # 프롬프트와 항상 일치하도록.
        definition = resolved_prompt_bundle.generator_definition_for_form(
            assessment.source_classification.document_form,
            sensitive=sensitive,
            subclause_key=plan.final_target.subclause_key,
        )
        # 호출자가 seed를 주지 않으면 잠긴 판별 결과와 최종 목표만으로 조립한다.
        # 계획 hash가 그 두 입력을 이미 고정하므로 조립 결과도 재현 가능하다.
        if sensitive_seed is None or not sensitive_seed.strip():
            sensitive_seed = build_sensitive_seed(assessment, plan.final_target)
        try:
            call = gateway.parse(
                model=config.generator_model,
                system_prompt=definition.system_prompt,
                user_prompt=render_generator_user_prompt(
                    render_full_source(snapshot),
                    source_assessment=json.dumps(
                        _model_payload(assessment),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    generation_plan=_generation_plan_prompt_json(
                        plan,
                        reference_date=config.reference_date,
                    ),
                    repair_codes=json.dumps(
                        [code.value for code in repair_codes],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    sensitive_seed=sensitive_seed,
                ),
                response_model=GeneratedDocumentIR,
                max_output_tokens=config.max_generator_output_tokens,
            )
        except StructuredCallError as exc:
            return GenerationExecution(
                failure=_failure(
                    stage=FailureStage.GENERATION,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate one source document
            return GenerationExecution(
                failure=_failure(
                    stage=FailureStage.GENERATION,
                    code=FailureCode.SDK_ERROR,
                    message=f"{type(exc).__name__}: structured-output gateway failed",
                )
            )

        if not isinstance(call.parsed, GeneratedDocumentIR):
            return GenerationExecution(
                failure=_failure(
                    stage=FailureStage.GENERATION,
                    code=FailureCode.STRUCTURED_OUTPUT_INVALID,
                    message="generator gateway returned the wrong contract type",
                )
            )
        generated_document = cast(GeneratedDocumentIR, call.parsed)
        receipt = _receipt(
            stage=FailureStage.GENERATION,
            model_id=config.generator_model,
            call=call,
        )

    try:
        provenance = _build_generation_provenance(
            assessment=assessment,
            plan=plan,
            selection=selection,
            sensitive_seed=sensitive_seed,
            fully_synthetic_context=fully_synthetic_context,
        )
        artifact = GenerationArtifact(
            plan_sha256=model_sha256(plan),
            generated_document=generated_document,
            attempt_index=attempt_index,
            parent_generation_sha256=parent_generation_sha256,
            repair_codes=repair_codes,
            provenance=provenance,
        )
        if config.reference_date is not None:
            validate_status_date_coherence(
                generated_document.body_text,
                plan.final_target.administrative_statuses,
                reference_date=config.reference_date,
            )
    except ValueError as exc:
        return GenerationExecution(
            failure=_failure(
                stage=FailureStage.GENERATION,
                code=FailureCode.ROUTE_INVALID,
                message=str(exc),
            )
        )

    return GenerationExecution(
        artifact=artifact,
        receipt=receipt,
    )


def _canonicalize_consistency_assessment(
    assessment: ConsistencyAssessment,
    *,
    document: GeneratedDocumentIR,
) -> ConsistencyAssessment:
    assessment_type = type(assessment)
    canonicalized = assessment_type.model_validate(
        {
            **assessment.model_dump(
                mode="python",
                exclude={"evidence_spans"},
                exclude_computed_fields=True,
            ),
            "evidence_spans": validate_evidence_quotes(
                assessment.evidence_spans,
                document.block_text,
            ),
        }
    )
    canonicalized.validate_against_document(document)
    return canonicalized


def _materialize_sensitive_monitor_decision(
    decision: SensitiveMonitorDecision,
    *,
    assessment: SourceAssessment,
    plan: GenerationPlan,
) -> SensitiveConsistencyAssessment:
    """S/O를 저장 계약으로 감싸되 판정 외 메타데이터는 잠긴 입력에서 가져온다."""

    source_form = assessment.source_classification
    target = plan.final_target
    is_sensitive = decision.classification == CsoClassification.S
    return SensitiveConsistencyAssessment(
        document_form=source_form.document_form,
        other_document_form=source_form.other_document_form,
        classification=decision.classification,
        clause_no=target.clause_no if is_sensitive else None,
        subclause_key=target.subclause_key if is_sensitive else None,
        evidence_spans=(),
        rationale=decision.rationale.strip() or "부가 근거 기록 없음",
        sensitivity_verdict=(
            SensitiveVerdict.ACCEPTED_S
            if is_sensitive
            else SensitiveVerdict.ASSESSED_O
        ),
    )


def _compare_consistency(
    *,
    assessment: SourceAssessment,
    plan: GenerationPlan,
    consistency: ConsistencyAssessment,
) -> ConsistencyComparison:
    source_form = assessment.source_classification
    document_form_match = document_form_matches(consistency, source_form)

    subject_role_match = True
    if isinstance(consistency, SensitiveConsistencyAssessment):
        allowed_roles = {role.value for role in assessment.subject_roles}
        subject_role_match = all(
            assertion.subject_role.value in allowed_roles
            for assertion in consistency.assertions
        )

    target = plan.final_target
    return ConsistencyComparison(
        document_form_match=document_form_match,
        classification_match=(
            effective_classification(
                consistency.classification,
                target.administrative_statuses,
            ).value
            == target.classification.value
        ),
        clause_match=consistency.clause_no == target.clause_no,
        subclause_match=consistency.subclause_key == target.subclause_key,
        subject_role_match=subject_role_match,
    )


_MASK_PATTERN = re.compile(r"(?:\*|○|●|□|X|x){2,}")


def _repair_codes_for_validation(
    *,
    comparison: ConsistencyComparison,
    plan: GenerationPlan,
    artifact: GenerationArtifact,
    assessment: ConsistencyAssessment,
) -> tuple[RepairCode, ...]:
    if isinstance(assessment, SensitiveConsistencyAssessment):
        if assessment.sensitivity_verdict == SensitiveVerdict.ASSESSED_O:
            return (RepairCode.DIRECT_VALUE_MISSING,)
        return ()

    codes: list[RepairCode] = []
    if not comparison.document_form_match:
        codes.append(RepairCode.FORM_MISMATCH)
    if not comparison.classification_match:
        codes.append(RepairCode.CLASSIFICATION_MISMATCH)
    if not comparison.clause_match:
        codes.append(RepairCode.CLAUSE_MISMATCH)
    if not comparison.subclause_match:
        codes.append(RepairCode.SUBCLAUSE_MISMATCH)
    if not comparison.subject_role_match:
        codes.append(RepairCode.ROLE_INCOMPATIBLE)
    if _MASK_PATTERN.search(artifact.generated_document.body_text):
        codes.append(RepairCode.MASK_REMAINS)
    return tuple(dict.fromkeys(codes))


def execute_consistency_validation(
    *,
    assessment: SourceAssessment,
    plan: GenerationPlan,
    artifact: GenerationArtifact,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
) -> ConsistencyValidationExecution:
    """생성 IR만 보여 주고 목표·원문·판별 결과와 독립적으로 채점한다."""

    configuration_failure = _configuration_failure(
        config,
        stage=FailureStage.VALIDATION,
    )
    if configuration_failure is not None:
        return ConsistencyValidationExecution(failure=configuration_failure)

    try:
        if artifact.plan_sha256 != model_sha256(plan):
            raise ValueError("generation artifact plan hash no longer matches")
        plan.validate_against(assessment)
        resolved_prompt_bundle = _resolved_prompt_bundle(
            selection_config or SelectionConfig(),
            prompt_bundle,
        )
        sensitive_contract = plan.final_target.clause_no == ClauseNumber.CLAUSE_6
        if config.source_sensitive_mode and not sensitive_contract:
            raise ValueError(
                "source_sensitive_mode requires a final clause 6 target"
            )
    except ValueError as exc:
        return ConsistencyValidationExecution(
            failure=_failure(
                stage=FailureStage.MANIFEST,
                code=FailureCode.MODEL_CONFIGURATION_INVALID,
                message=str(exc),
            )
        )

    definition = resolved_prompt_bundle.definition(
        "sensitive_validator" if sensitive_contract else "validator"
    )
    response_model = definition.response_model

    try:
        call = gateway.parse(
            model=config.validator_model,
            system_prompt=definition.system_prompt,
            user_prompt=render_validator_user_prompt(
                artifact.generated_document.model_dump_json(
                    exclude_computed_fields=True
                )
            ),
            response_model=response_model,
            max_output_tokens=config.max_validator_output_tokens,
        )
    except StructuredCallError as exc:
        return ConsistencyValidationExecution(
            repair_codes=(RepairCode.EVIDENCE_INVALID,),
            failure=_failure(
                stage=FailureStage.VALIDATION,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - isolate one generated document
        return ConsistencyValidationExecution(
            repair_codes=(RepairCode.EVIDENCE_INVALID,),
            failure=_failure(
                stage=FailureStage.VALIDATION,
                code=FailureCode.SDK_ERROR,
                message=f"{type(exc).__name__}: structured-output gateway failed",
            ),
        )

    # ``SensitiveConsistencyAssessment`` 허용은 새 입력 계약 이전의 테스트·내부
    # gateway와 저장된 호출을 위한 읽기 호환성이다. 실제 OpenAI 호출은 위의
    # ``SensitiveMonitorDecision`` JSON schema만 받을 수 있다.
    legacy_sensitive_result = sensitive_contract and isinstance(
        call.parsed,
        SensitiveConsistencyAssessment,
    )
    if not isinstance(call.parsed, response_model) and not legacy_sensitive_result:
        return ConsistencyValidationExecution(
            repair_codes=(RepairCode.EVIDENCE_INVALID,),
            failure=_failure(
                stage=FailureStage.VALIDATION,
                code=FailureCode.STRUCTURED_OUTPUT_INVALID,
                message="validator gateway returned the wrong contract type",
            ),
        )
    receipt = _receipt(
        stage=FailureStage.VALIDATION,
        model_id=config.validator_model,
        call=call,
    )
    consistency: ConsistencyAssessment | None = None
    try:
        if isinstance(call.parsed, SensitiveMonitorDecision):
            consistency = _materialize_sensitive_monitor_decision(
                call.parsed,
                assessment=assessment,
                plan=plan,
            )
        else:
            consistency = cast(ConsistencyAssessment, call.parsed)
        consistency = _canonicalize_consistency_assessment(
            consistency,
            document=artifact.generated_document,
        )
        if (
            isinstance(consistency, SensitiveConsistencyAssessment)
            and consistency.assertions
        ):
            validate_sensitive_assessment(consistency)
    except ValueError as exc:
        code = (
            FailureCode.SENSITIVE_ASSERTION_INVALID
            if sensitive_contract
            else FailureCode.EVIDENCE_INVALID
        )
        return ConsistencyValidationExecution(
            assessment=consistency,
            receipt=receipt if consistency is not None else None,
            repair_codes=(RepairCode.EVIDENCE_INVALID,),
            failure=_failure(
                stage=FailureStage.VALIDATION,
                code=code,
                message=str(exc),
            ),
        )

    comparison = _compare_consistency(
        assessment=assessment,
        plan=plan,
        consistency=consistency,
    )
    return ConsistencyValidationExecution(
        assessment=consistency,
        receipt=receipt,
        comparison=comparison,
        repair_codes=_repair_codes_for_validation(
            comparison=comparison,
            plan=plan,
            artifact=artifact,
            assessment=consistency,
        ),
    )


def run_three_stage_pipeline(
    *,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    counterfactual_target: GenerationTarget,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
    sensitive_seed: str | None = None,
    fully_synthetic_generator: FullySyntheticDocumentGenerator | None = None,
    fully_synthetic_context: FullySyntheticContext | None = None,
) -> DocumentPipelineResult:
    """문서 하나를 판별 → 계획 → 생성 → blind 검사 순서로 처리한다."""

    classification = execute_classification(
        snapshot=snapshot,
        selection=selection,
        gateway=gateway,
        config=config,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
    )
    if classification.failure is not None:
        return DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            source_assessment=classification.assessment,
            classification_receipt=classification.receipt,
            failure=classification.failure,
        )
    assert classification.assessment is not None
    assert classification.receipt is not None

    try:
        plan = build_generation_plan(
            assessment=classification.assessment,
            requested_target=counterfactual_target,
            snapshot=snapshot,
            selection=selection,
            sensitive_seed=sensitive_seed,
            has_synthetic_generator=(
                fully_synthetic_generator is not None
                and fully_synthetic_context is not None
            ),
        )
    except GenerationPlanningError as exc:
        return DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            source_assessment=classification.assessment,
            classification_receipt=classification.receipt,
            failure=_failure(
                stage=FailureStage.PLANNING,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            ),
        )

    generation = execute_generation(
        snapshot=snapshot,
        selection=selection,
        assessment=classification.assessment,
        plan=plan,
        gateway=gateway,
        config=config,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
        sensitive_seed=sensitive_seed,
        fully_synthetic_generator=fully_synthetic_generator,
        fully_synthetic_context=fully_synthetic_context,
    )
    if generation.failure is not None:
        return DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            source_assessment=classification.assessment,
            generation_plan=plan,
            generation_artifact=generation.artifact,
            classification_receipt=classification.receipt,
            generation_receipt=generation.receipt,
            failure=generation.failure,
        )
    assert generation.artifact is not None

    validation = execute_consistency_validation(
        assessment=classification.assessment,
        plan=plan,
        artifact=generation.artifact,
        gateway=gateway,
        config=config,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
    )
    if validation.failure is not None:
        return DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            source_assessment=classification.assessment,
            generation_plan=plan,
            generation_artifact=generation.artifact,
            consistency_assessment=validation.assessment,
            classification_receipt=classification.receipt,
            generation_receipt=generation.receipt,
            validation_receipt=validation.receipt,
            failure=validation.failure,
        )
    assert validation.assessment is not None
    assert validation.receipt is not None
    assert validation.comparison is not None
    return DocumentPipelineResult(
        source_document_id=snapshot.source_document_id,
        source_assessment=classification.assessment,
        generation_plan=plan,
        generation_artifact=generation.artifact,
        consistency_assessment=validation.assessment,
        classification_receipt=classification.receipt,
        generation_receipt=generation.receipt,
        validation_receipt=validation.receipt,
        comparison=validation.comparison,
    )


def _source_sensitive_terminal_run(
    *,
    status: SensitivePipelineStatus,
    attempts: list[DocumentPipelineResult],
) -> SourceSensitivePipelineRun:
    return SourceSensitivePipelineRun(
        status=status,
        attempts=tuple(attempts),
    )


def run_source_sensitive_pipeline(
    *,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    counterfactual_target: GenerationTarget,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
    sensitive_seed: str | None = None,
    fully_synthetic_generator: FullySyntheticDocumentGenerator | None = None,
    fully_synthetic_context: FullySyntheticContext | None = None,
    max_generation_attempts: int = 2,
) -> SourceSensitivePipelineRun:
    """제6호 결과가 O이면 판별·계획은 유지하고 생성 단계만 한 번 다시 실행한다."""

    if max_generation_attempts < 1:
        raise ValueError("max_generation_attempts must be at least 1")

    attempts: list[DocumentPipelineResult] = []
    classification = execute_classification(
        snapshot=snapshot,
        selection=selection,
        gateway=gateway,
        config=config,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
    )
    if classification.failure is not None:
        attempts.append(
            DocumentPipelineResult(
                source_document_id=snapshot.source_document_id,
                source_assessment=classification.assessment,
                classification_receipt=classification.receipt,
                failure=classification.failure,
            )
        )
        return _source_sensitive_terminal_run(
            status=SensitivePipelineStatus.PIPELINE_FAILED,
            attempts=attempts,
        )
    assert classification.assessment is not None
    assert classification.receipt is not None

    try:
        plan = build_generation_plan(
            assessment=classification.assessment,
            requested_target=counterfactual_target,
            snapshot=snapshot,
            selection=selection,
            sensitive_seed=sensitive_seed,
            has_synthetic_generator=(
                fully_synthetic_generator is not None
                and fully_synthetic_context is not None
            ),
        )
        if plan.final_target.clause_no != ClauseNumber.CLAUSE_6:
            raise GenerationPlanningError(
                FailureCode.SOURCE_INCOMPATIBLE,
                "source-sensitive pipeline requires a final clause 6 target",
            )
    except GenerationPlanningError as exc:
        attempts.append(
            DocumentPipelineResult(
                source_document_id=snapshot.source_document_id,
                source_assessment=classification.assessment,
                classification_receipt=classification.receipt,
                failure=_failure(
                    stage=FailureStage.PLANNING,
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                ),
            )
        )
        return _source_sensitive_terminal_run(
            status=SensitivePipelineStatus.PIPELINE_FAILED,
            attempts=attempts,
        )

    parent_generation_sha256: str | None = None
    next_repair_codes: tuple[RepairCode, ...] = ()
    for attempt_index in range(1, max_generation_attempts + 1):
        generation = execute_generation(
            snapshot=snapshot,
            selection=selection,
            assessment=classification.assessment,
            plan=plan,
            gateway=gateway,
            config=config,
            selection_config=selection_config,
            prompt_bundle=prompt_bundle,
            sensitive_seed=sensitive_seed,
            fully_synthetic_generator=fully_synthetic_generator,
            fully_synthetic_context=fully_synthetic_context,
            attempt_index=attempt_index,
            parent_generation_sha256=parent_generation_sha256,
            repair_codes=next_repair_codes,
        )
        if generation.failure is not None:
            attempts.append(
                DocumentPipelineResult(
                    source_document_id=snapshot.source_document_id,
                    source_assessment=classification.assessment,
                    generation_plan=plan,
                    generation_artifact=generation.artifact,
                    classification_receipt=classification.receipt,
                    generation_receipt=generation.receipt,
                    failure=generation.failure,
                )
            )
            return _source_sensitive_terminal_run(
                status=SensitivePipelineStatus.PIPELINE_FAILED,
                attempts=attempts,
            )
        assert generation.artifact is not None

        validation = execute_consistency_validation(
            assessment=classification.assessment,
            plan=plan,
            artifact=generation.artifact,
            gateway=gateway,
            config=config,
            selection_config=selection_config,
            prompt_bundle=prompt_bundle,
        )
        if validation.failure is not None:
            attempts.append(
                DocumentPipelineResult(
                    source_document_id=snapshot.source_document_id,
                    source_assessment=classification.assessment,
                    generation_plan=plan,
                    generation_artifact=generation.artifact,
                    consistency_assessment=validation.assessment,
                    classification_receipt=classification.receipt,
                    generation_receipt=generation.receipt,
                    validation_receipt=validation.receipt,
                    failure=validation.failure,
                )
            )
            return _source_sensitive_terminal_run(
                status=SensitivePipelineStatus.PIPELINE_FAILED,
                attempts=attempts,
            )

        assert isinstance(
            validation.assessment,
            SensitiveConsistencyAssessment,
        )
        assert validation.receipt is not None
        assert validation.comparison is not None
        result = DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            source_assessment=classification.assessment,
            generation_plan=plan,
            generation_artifact=generation.artifact,
            consistency_assessment=validation.assessment,
            classification_receipt=classification.receipt,
            generation_receipt=generation.receipt,
            validation_receipt=validation.receipt,
            comparison=validation.comparison,
        )
        attempts.append(result)

        verdict = validation.assessment.sensitivity_verdict
        if verdict == SensitiveVerdict.ACCEPTED_S:
            return _source_sensitive_terminal_run(
                status=SensitivePipelineStatus.ACCEPTED_S,
                attempts=attempts,
            )
        if verdict == SensitiveVerdict.HARD_CASE_REVIEW:
            return _source_sensitive_terminal_run(
                status=SensitivePipelineStatus.HARD_CASE_REVIEW,
                attempts=attempts,
            )
        if attempt_index < max_generation_attempts:
            parent_generation_sha256 = model_sha256(generation.artifact)
            next_repair_codes = validation.repair_codes
            if not next_repair_codes:
                next_repair_codes = (RepairCode.DIRECT_VALUE_MISSING,)
            continue
        return _source_sensitive_terminal_run(
            status=SensitivePipelineStatus.EXCLUDED_AFTER_RETRY,
            attempts=attempts,
        )

    raise AssertionError("unreachable")  # pragma: no cover
