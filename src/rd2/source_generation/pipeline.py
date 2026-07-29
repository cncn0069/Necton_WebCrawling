"""서로 다른 모델을 쓰는 Pass 1 생성 + blind Pass 2 채점 pipeline."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from dataclasses import dataclass
from datetime import date
from typing import Generic, Protocol, TypeVar, cast

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

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import ClauseNumber
from rd2.source_generation.administrative import (
    administrative_status_generation_requirements,
    validate_status_date_coherence,
)
from rd2.source_generation.contracts import (
    CallReceipt,
    DocumentPipelineResult,
    DocumentSelection,
    FailureCode,
    FailureStage,
    AssessmentScope,
    GenerationProvenance,
    GenerationRoute,
    GenerationMode,
    GenerationTarget,
    GradeComparison,
    effective_classification,
    GeneratedDocumentIR,
    Pass1Result,
    Pass2Assessment,
    ParagraphBlock,
    SourceDocumentSnapshot,
    StageFailure,
    TokenUsage,
)
from rd2.source_generation.document_select import (
    DocumentSelectionError,
    SelectionConfig,
    render_selected_source,
)
from rd2.source_generation.evidence import validate_evidence_quotes
from rd2.source_generation.legacy_synthetic import (
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.prompts import (
    PromptBundle,
    build_prompt_bundle,
    render_pass1_user_prompt,
    render_pass2_user_prompt,
)

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
    generator_model: str
    grader_model: str
    max_pass1_output_tokens: int = 8_000
    max_pass2_output_tokens: int = 4_000
    #: '오늘'에 해당하는 기준일. 주면 P1에 전달하고 날짜 모순을 검증한다.
    reference_date: date | None = None

    def __post_init__(self) -> None:
        if not self.generator_model.strip() or not self.grader_model.strip():
            raise ValueError("generator_model and grader_model must not be blank")
        if self.max_pass1_output_tokens < 1 or self.max_pass2_output_tokens < 1:
            raise ValueError("max output tokens must be positive")


@dataclass(frozen=True)
class Pass1Execution:
    result: Pass1Result | None = None
    receipt: CallReceipt | None = None
    provenance: GenerationProvenance | None = None
    failure: StageFailure | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and self.result is None:
            raise ValueError("Pass 1 receipt requires a Pass 1 result")
        if self.provenance is not None and self.result is None:
            raise ValueError("Pass 1 provenance requires a Pass 1 result")
        if self.failure is None and (
            self.result is None or self.receipt is None or self.provenance is None
        ):
            raise ValueError(
                "successful Pass 1 execution requires result, receipt, and provenance"
            )
        if self.failure is not None and self.failure.stage not in {
            FailureStage.MANIFEST,
            FailureStage.SELECTION,
            FailureStage.PASS1,
        }:
            raise ValueError("Pass 1 execution has an invalid failure stage")

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class Pass2Execution:
    assessment: Pass2Assessment | None = None
    receipt: CallReceipt | None = None
    comparison: GradeComparison | None = None
    failure: StageFailure | None = None

    def __post_init__(self) -> None:
        if self.receipt is not None and self.assessment is None:
            raise ValueError("Pass 2 receipt requires a Pass 2 assessment")
        if self.comparison is not None and self.assessment is None:
            raise ValueError("Pass 2 comparison requires a Pass 2 assessment")
        if self.failure is None and (
            self.assessment is None
            or self.receipt is None
            or self.comparison is None
        ):
            raise ValueError(
                "successful Pass 2 execution requires assessment, receipt, and comparison"
            )
        if self.failure is not None and self.failure.stage != FailureStage.PASS2:
            raise ValueError("Pass 2 execution requires a Pass 2 failure")

    @property
    def succeeded(self) -> bool:
        return self.failure is None


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


def _selected_source_resolver(
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
):
    selected_ids = set(selection.selected_block_ids)

    def resolve(block_id: str) -> str:
        if block_id not in selected_ids:
            raise ValueError(f"source evidence references unselected block {block_id!r}")
        return snapshot.block_text(block_id)

    return resolve


#: 공개 원문에서 파생시키지 않는 조항. 원문 기반 경로는 제5~8호와 행정상태에
#: 집중하고, 기밀 셀은 완전 생성으로 보낸다(2026-07-29 범위 결정).
CONFIDENTIAL_CLAUSES: frozenset[ClauseNumber] = frozenset(
    {
        ClauseNumber.CLAUSE_1,
        ClauseNumber.CLAUSE_2,
        ClauseNumber.CLAUSE_3,
        ClauseNumber.CLAUSE_4,
    }
)


def available_routes(
    *,
    target: GenerationTarget,
    sensitive_seed: str | None,
    has_synthetic_generator: bool,
) -> tuple[GenerationRoute, ...]:
    """이번 호출의 **입력만 보고** 성립 가능한 route를 계산한다.

    실측에서 P1은 ``[SENSITIVE SEED] (없음)``을 보고도 50건 중 25건에서
    ``anchored``를 선택했다. anchored는 별도 seed가 전제 조건인데 그 전제를
    확인하지 않은 것이다. 결과는 전량 route validation 실패였다.

    프롬프트에 조건을 적어두는 것만으로는 부족하다는 뜻이므로, 모델이 추론해야
    할 것을 코드가 미리 계산해 목록으로 건넨다. 여기서 걸러지는 것은 모델의
    판단이 필요 없는 것들 — seed가 실제로 왔는가, 합성 실행기가 연결돼 있는가,
    target이 행정상태 단독인가 — 뿐이다. 원문이 C/S인지, 민감 span이 있는지
    같은 **내용 판단은 여전히 P1의 몫**이라 목록에 남겨둔다.
    """

    routes: list[GenerationRoute] = []
    admin_only = target.clause_no is None

    if admin_only:
        # 법적 조항 없는 행정상태 단독 target은 이 route로만 성립한다.
        return (GenerationRoute.ADMINISTRATIVE_AUGMENTED,)

    # source가 C/S인지는 P1이 원문을 보고 판단한다. 원문이 진짜로 C/S인
    # 경우에만 도달하므로 어느 조항에서든 남겨둔다.
    routes.append(GenerationRoute.SOURCE_ALIGNED)

    if target.clause_no in CONFIDENTIAL_CLAUSES:
        # 공개 원문에서 기밀(제1~4호)을 파생시키지 않는다.
        #
        # span_seeded와 anchored는 공개(O) 원문을 재료로 C 목표를 만드는
        # 경로다. 기밀·국방·외교·생명·수사·재판 내용은 공개 문서에 애초에
        # 없으므로, 이 경로들은 근거 없는 counterfactual이 되거나 원문을
        # 형식적으로만 붙여 놓는 결과가 된다. 실측에서도 P1은 공개 원문에
        # C 목표를 받으면 대부분 no_usable_public_source로 물러섰다.
        # 기밀 셀은 처음부터 완전 생성하는 편이 정직하고 품질도 낫다.
        if has_synthetic_generator:
            routes.append(GenerationRoute.FULLY_SYNTHETIC)
        return tuple(routes)

    # 제5~8호는 공개 원문에 업무 맥락과 민감 span이 실제로 존재할 수 있다.
    # 제6호는 비식별화 구현 전까지 span_seeded를 막아 두었다.
    if target.clause_no != ClauseNumber.CLAUSE_6:
        routes.append(GenerationRoute.SPAN_SEEDED)
    if sensitive_seed:
        routes.append(GenerationRoute.ANCHORED)
    if has_synthetic_generator:
        routes.append(GenerationRoute.FULLY_SYNTHETIC)
    return tuple(routes)


def _generation_plan_json(
    counterfactual_target: GenerationTarget,
    *,
    reference_date: date | None = None,
    routes: tuple[GenerationRoute, ...] = (),
) -> str:
    payload = {
        "when_source_is_c_or_s": {
            "generation_mode": GenerationMode.SOURCE_ALIGNED.value,
            "instruction": "분류한 source C/S label과 정확히 같은 target을 사용한다.",
        },
        "when_source_is_o": {
            "suggested_target": counterfactual_target.model_dump(mode="json"),
            "instruction": (
                "제안 target을 우선 검토하되 source evidence와 taxonomy상 더 적합한 "
                "유효한 C/S target이 있으면 법적 target만 변경할 수 있다. "
                "administrative_statuses는 고정값이므로 변경하지 않는다."
            ),
        },
        "administrative_status_generation_requirements": (
            administrative_status_generation_requirements(
                counterfactual_target.administrative_statuses,
                reference_date=reference_date,
            )
        ),
        "available_routes": {
            "routes": [route.value for route in routes],
            "instruction": (
                "이 목록에 없는 route는 이번 입력에서 전제 조건이 성립하지 "
                "않으므로 선택하지 않는다. 목록 안에서 실제 원문 근거에 맞는 "
                "route를 고른다."
            ),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assessment_scope(selection: DocumentSelection) -> AssessmentScope:
    if selection.truncated:
        return AssessmentScope.SELECTED_VIEW_ONLY
    return AssessmentScope.FULL_DOCUMENT


def _canonicalize_pass1_evidence(
    result: Pass1Result,
    *,
    block_text,
) -> Pass1Result:
    source_classification = result.source_classification.model_copy(
        update={
            "evidence_spans": validate_evidence_quotes(
                result.source_classification.evidence_spans,
                block_text,
            )
        }
    )
    source_suitability = result.source_suitability.model_copy(
        update={
            "evidence_spans": validate_evidence_quotes(
                result.source_suitability.evidence_spans,
                block_text,
            )
        }
    )
    return Pass1Result.model_validate(
        {
            **result.model_dump(
                mode="python",
                exclude={"source_classification", "source_suitability"},
                exclude_computed_fields=True,
            ),
            "source_classification": source_classification,
            "source_suitability": source_suitability,
        }
    )


def _canonicalize_pass2_evidence(
    assessment: Pass2Assessment,
    *,
    block_text,
) -> Pass2Assessment:
    return Pass2Assessment.model_validate(
        {
            **assessment.model_dump(
                mode="python",
                exclude={"evidence_spans"},
                exclude_computed_fields=True,
            ),
            "evidence_spans": validate_evidence_quotes(
                assessment.evidence_spans,
                block_text,
            ),
        }
    )


def _discard_source_seeing_generated_document(
    result: Pass1Result,
) -> Pass1Result:
    """fully_synthetic 실패 결과에도 P1의 source-derived 초안을 남기지 않는다."""

    return Pass1Result.model_validate(
        {
            **result.model_dump(
                mode="python",
                exclude={"generated_document"},
                exclude_computed_fields=True,
            ),
            "generated_document": GeneratedDocumentIR(
                title="완전 합성 문서 생성 대기",
                blocks=(
                    ParagraphBlock(
                        block_id="g1",
                        text="원문 비사용 합성 생성이 완료되지 않았습니다.",
                    ),
                ),
            ),
        }
    )


def _build_provenance(
    *,
    result: Pass1Result,
    requested_target: GenerationTarget,
    selection: DocumentSelection,
    sensitive_seed: str | None,
    fully_synthetic_context: FullySyntheticContext | None,
) -> GenerationProvenance:
    route = result.generation_route
    seed_hash = None
    if route == GenerationRoute.ANCHORED:
        if sensitive_seed is None or not sensitive_seed.strip():
            raise ValueError("anchored route requires a separately provided sensitive seed")
        seed_hash = sha256(sensitive_seed.encode("utf-8")).hexdigest()
    uses_source_evidence = route != GenerationRoute.FULLY_SYNTHETIC
    return GenerationProvenance(
        generation_route=route,
        source_evidence_level=result.source_suitability.evidence_level,
        reason_code=result.source_suitability.reason_code,
        requested_target=requested_target,
        final_target=result.generation_target,
        selection_sha256=selection.selection_sha256,
        uses_source_evidence=uses_source_evidence,
        validated_evidence_spans=result.source_suitability.evidence_spans,
        sensitive_seed_sha256=seed_hash,
        synthetic_scenario_id=(
            fully_synthetic_context.scenario_id
            if route == GenerationRoute.FULLY_SYNTHETIC
            and fully_synthetic_context is not None
            else None
        ),
    )


def _compare_grade(pass1: Pass1Result, pass2: Pass2Assessment) -> GradeComparison:
    target = pass1.generation_target
    return GradeComparison(
        document_form_match=(
            pass2.document_form == pass1.source_classification.document_form
        ),
        classification_match=(
            effective_classification(
                pass2.classification, target.administrative_statuses
            ).value
            == target.classification.value
        ),
        clause_match=(pass2.clause_no == target.clause_no),
        subclause_match=(pass2.subclause_key == target.subclause_key),
    )


def execute_pass1(
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
) -> Pass1Execution:
    """문서 하나를 처리한다. 실패는 예외 대신 typed partial result로 반환한다."""

    if config.generator_model == config.grader_model:
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.PASS1,
                code=FailureCode.MODEL_CONFIGURATION_INVALID,
                message="generator_model and grader_model must be different",
            ),
        )
    if counterfactual_target.generation_mode != GenerationMode.COUNTERFACTUAL:
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.MANIFEST,
                code=FailureCode.MANIFEST_INVALID,
                message="O fallback target requires generation_mode=counterfactual",
            ),
        )

    resolved_selection_config = selection_config or SelectionConfig()
    resolved_prompt_bundle = prompt_bundle or build_prompt_bundle(
        resolved_selection_config
    )
    if resolved_prompt_bundle.selection_config != resolved_selection_config:
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.MANIFEST,
                code=FailureCode.MODEL_CONFIGURATION_INVALID,
                message="prompt bundle selection config does not match pipeline config",
            ),
        )

    try:
        source_document = render_selected_source(
            snapshot,
            selection,
            resolved_selection_config,
        )
    except DocumentSelectionError as exc:
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.SELECTION,
                code=exc.code,
                message=str(exc),
            ),
        )

    pass1_prompt = resolved_prompt_bundle.definition("pass1")
    try:
        pass1_call = gateway.parse(
            model=config.generator_model,
            system_prompt=pass1_prompt.system_prompt,
            user_prompt=render_pass1_user_prompt(
                source_document,
                generation_plan=_generation_plan_json(
                    counterfactual_target,
                    reference_date=config.reference_date,
                    routes=available_routes(
                        target=counterfactual_target,
                        sensitive_seed=sensitive_seed,
                        has_synthetic_generator=fully_synthetic_generator is not None,
                    ),
                ),
                assessment_scope=_assessment_scope(selection).value,
                sensitive_seed=sensitive_seed,
            ),
            response_model=Pass1Result,
            max_output_tokens=config.max_pass1_output_tokens,
        )
    except StructuredCallError as exc:
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.PASS1,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - gateway boundary must isolate one document
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.PASS1,
                code=FailureCode.SDK_ERROR,
                message=f"{type(exc).__name__}: structured-output gateway failed",
            ),
        )

    if not isinstance(pass1_call.parsed, Pass1Result):
        return Pass1Execution(
            failure=_failure(
                stage=FailureStage.PASS1,
                code=FailureCode.STRUCTURED_OUTPUT_INVALID,
                message="Pass 1 gateway returned the wrong contract type",
            ),
        )
    pass1_result = cast(Pass1Result, pass1_call.parsed)
    pass1_receipt = _receipt(
        stage=FailureStage.PASS1,
        model_id=config.generator_model,
        call=pass1_call,
    )
    try:
        pass1_result = _canonicalize_pass1_evidence(
            pass1_result,
            block_text=_selected_source_resolver(snapshot, selection),
        )
        pass1_result.source_classification.validate_evidence_against(
            _selected_source_resolver(snapshot, selection)
        )
        pass1_result.source_suitability.validate_evidence_against(
            _selected_source_resolver(snapshot, selection)
        )
        expected_scope = _assessment_scope(selection)
        if pass1_result.source_suitability.assessment_scope != expected_scope:
            raise ValueError(
                "source suitability assessment_scope does not match document selection"
            )
        if config.reference_date is not None:
            # '아직 오지 않았다'는 상태가 지난 날짜로 쓰여 있으면 그 문서는
            # 스스로와 모순된다. forbidden_phrases는 고정 문구만 보고
            # 날짜는 안 보므로 여기서 따로 확인한다.
            validate_status_date_coherence(
                pass1_result.generated_document.body_text,
                pass1_result.generation_target.administrative_statuses,
                reference_date=config.reference_date,
            )
    except ValueError as exc:
        return Pass1Execution(
            result=pass1_result,
            receipt=pass1_receipt,
            failure=_failure(
                stage=FailureStage.PASS1,
                code=FailureCode.EVIDENCE_INVALID,
                message=str(exc),
            ),
        )

    if (
        pass1_result.generation_target.administrative_statuses
        != counterfactual_target.administrative_statuses
    ):
        return Pass1Execution(
            result=pass1_result,
            receipt=pass1_receipt,
            failure=_failure(
                stage=FailureStage.PASS1,
                code=FailureCode.ROUTE_INVALID,
                message=(
                    "Pass 1 must preserve the requested administrative statuses "
                    "exactly and in order"
                ),
            ),
        )

    if pass1_result.generation_route == GenerationRoute.FULLY_SYNTHETIC:
        pass1_result = _discard_source_seeing_generated_document(pass1_result)
        if fully_synthetic_generator is None or fully_synthetic_context is None:
            return Pass1Execution(
                result=pass1_result,
                receipt=pass1_receipt,
                failure=_failure(
                    stage=FailureStage.PASS1,
                    code=FailureCode.ROUTE_INVALID,
                    message=(
                        "fully_synthetic route requires a source-free generator "
                        "and execution context"
                    ),
                ),
            )
        try:
            generated_document = fully_synthetic_generator.generate(
                target=pass1_result.generation_target,
                context=fully_synthetic_context,
            )
            pass1_result = Pass1Result.model_validate(
                {
                    **pass1_result.model_dump(
                        mode="python",
                        exclude={"generated_document"},
                        exclude_computed_fields=True,
                    ),
                    "generated_document": generated_document,
                }
            )
        except ValueError as exc:
            return Pass1Execution(
                result=pass1_result,
                receipt=pass1_receipt,
                failure=_failure(
                    stage=FailureStage.PASS1,
                    code=FailureCode.ROUTE_INVALID,
                    message=f"fully_synthetic generator rejected the route: {exc}",
                ),
            )
        except Exception as exc:  # noqa: BLE001 - generator boundary isolates one document
            return Pass1Execution(
                result=pass1_result,
                receipt=pass1_receipt,
                failure=_failure(
                    stage=FailureStage.PASS1,
                    code=FailureCode.SDK_ERROR,
                    message=(
                        f"{type(exc).__name__}: fully_synthetic generator failed"
                    ),
                ),
            )

    try:
        provenance = _build_provenance(
            result=pass1_result,
            requested_target=counterfactual_target,
            selection=selection,
            sensitive_seed=sensitive_seed,
            fully_synthetic_context=fully_synthetic_context,
        )
    except ValueError as exc:
        return Pass1Execution(
            result=pass1_result,
            receipt=pass1_receipt,
            failure=_failure(
                stage=FailureStage.PASS1,
                code=FailureCode.ROUTE_INVALID,
                message=str(exc),
            ),
        )

    return Pass1Execution(
        result=pass1_result,
        receipt=pass1_receipt,
        provenance=provenance,
    )


def execute_pass2(
    *,
    pass1_result: Pass1Result,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
) -> Pass2Execution:
    """Pass 2만 실행한다. 생성 IR 외의 Pass 1 판단은 입력하지 않는다."""

    if config.generator_model == config.grader_model:
        return Pass2Execution(
            failure=_failure(
                stage=FailureStage.PASS2,
                code=FailureCode.MODEL_CONFIGURATION_INVALID,
                message="generator_model and grader_model must be different",
            )
        )
    resolved_selection_config = selection_config or SelectionConfig()
    resolved_prompt_bundle = prompt_bundle or build_prompt_bundle(
        resolved_selection_config
    )
    if resolved_prompt_bundle.selection_config != resolved_selection_config:
        return Pass2Execution(
            failure=_failure(
                stage=FailureStage.PASS2,
                code=FailureCode.MODEL_CONFIGURATION_INVALID,
                message="prompt bundle selection config does not match pipeline config",
            )
        )

    pass2_prompt = resolved_prompt_bundle.definition("pass2")
    generated_ir_json = pass1_result.generated_document.model_dump_json(
        exclude_computed_fields=True
    )
    try:
        pass2_call = gateway.parse(
            model=config.grader_model,
            system_prompt=pass2_prompt.system_prompt,
            user_prompt=render_pass2_user_prompt(generated_ir_json),
            response_model=Pass2Assessment,
            max_output_tokens=config.max_pass2_output_tokens,
        )
    except StructuredCallError as exc:
        return Pass2Execution(
            failure=_failure(
                stage=FailureStage.PASS2,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - gateway boundary must isolate one document
        return Pass2Execution(
            failure=_failure(
                stage=FailureStage.PASS2,
                code=FailureCode.SDK_ERROR,
                message=f"{type(exc).__name__}: structured-output gateway failed",
            ),
        )

    if not isinstance(pass2_call.parsed, Pass2Assessment):
        return Pass2Execution(
            failure=_failure(
                stage=FailureStage.PASS2,
                code=FailureCode.STRUCTURED_OUTPUT_INVALID,
                message="Pass 2 gateway returned the wrong contract type",
            ),
        )
    pass2_assessment = cast(Pass2Assessment, pass2_call.parsed)
    pass2_receipt = _receipt(
        stage=FailureStage.PASS2,
        model_id=config.grader_model,
        call=pass2_call,
    )
    try:
        pass2_assessment = _canonicalize_pass2_evidence(
            pass2_assessment,
            block_text=pass1_result.generated_document.block_text,
        )
        pass2_assessment.validate_against_document(pass1_result.generated_document)
    except ValueError as exc:
        return Pass2Execution(
            assessment=pass2_assessment,
            receipt=pass2_receipt,
            failure=_failure(
                stage=FailureStage.PASS2,
                code=FailureCode.EVIDENCE_INVALID,
                message=str(exc),
            ),
        )

    return Pass2Execution(
        assessment=pass2_assessment,
        receipt=pass2_receipt,
        comparison=_compare_grade(pass1_result, pass2_assessment),
    )


def run_two_pass(
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
    """문서 하나를 처리한다. 실패는 예외 대신 typed partial result로 반환한다."""

    pass1 = execute_pass1(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=counterfactual_target,
        gateway=gateway,
        config=config,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
        sensitive_seed=sensitive_seed,
        fully_synthetic_generator=fully_synthetic_generator,
        fully_synthetic_context=fully_synthetic_context,
    )
    if pass1.failure is not None:
        return DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            pass1_result=pass1.result,
            pass1_receipt=pass1.receipt,
            failure=pass1.failure,
        )
    assert pass1.result is not None
    assert pass1.receipt is not None
    assert pass1.provenance is not None

    pass2 = execute_pass2(
        pass1_result=pass1.result,
        gateway=gateway,
        config=config,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
    )
    if pass2.failure is not None:
        return DocumentPipelineResult(
            source_document_id=snapshot.source_document_id,
            pass1_result=pass1.result,
            pass2_assessment=pass2.assessment,
            pass1_receipt=pass1.receipt,
            pass2_receipt=pass2.receipt,
            generation_provenance=pass1.provenance,
            failure=pass2.failure,
        )
    assert pass2.assessment is not None
    assert pass2.receipt is not None
    assert pass2.comparison is not None
    return DocumentPipelineResult(
        source_document_id=snapshot.source_document_id,
        pass1_result=pass1.result,
        pass2_assessment=pass2.assessment,
        pass1_receipt=pass1.receipt,
        pass2_receipt=pass2.receipt,
        generation_provenance=pass1.provenance,
        comparison=pass2.comparison,
    )
