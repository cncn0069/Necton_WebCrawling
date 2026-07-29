"""실제 라벨이 붙은 원문으로 분류 정확도를 재는 held-out 평가 harness.

**왜 필요한가.** 파이프라인이 내놓는 유일한 품질 숫자는 생성 문서에 대한
P1↔P2 일치율이다. 그런데 P1 프롬프트는 "독립 채점자가 GeneratedDocumentIR만
읽어도 목표를 판단할 수 있을 만큼" 쓰라고 명시적으로 지시한다 — 즉 일치율을
직접 최적화 대상으로 삼고 있다. 그래서 높은 일치율은 "라벨이 타당하다"가
아니라 "탐지 가능한 신호를 잘 심었다"를 뜻할 수 있고, 산출물이 RD-1 학습
데이터인 이상 그건 실제보다 쉬운 표본을 만든다는 뜻이다.

이 모듈은 그 숫자를 맥락에 놓을 대조군을 만든다.

    A. 실제 라벨이 붙은 원문에 대한 분류 정확도   <- 여기서 측정
    B. 생성 문서에 대한 P1<->P2 일치율            <- audit_bridge

``B``가 ``A``보다 크게 높으면 그 차이가 곧 생성 문서가 실제보다 쉬운 정도다.

**설계 결정 두 가지.**

1. 두 모델 모두 **P2(blind grading) 프롬프트**로 분류한다. 프롬프트를 고정해
   모델 변수만 남기고, 무엇보다 일치율을 맥락에 놓으려면 그 일치율을 만든
   당사자인 P2의 프롬프트로 재야 사과 대 사과 비교가 된다. P1 프롬프트는 본문
   생성까지 하므로 분류만 필요한 여기서는 버릴 토큰을 사게 된다.
2. 실제 원문 snapshot을 ``GeneratedDocumentIR``로 변환해 넣는다. P2가 보는
   **직렬화 형식은 그대로 두고 내용만 진짜로** 바꿔야, 정확도 차이가 형식
   차이가 아니라 난이도 차이로 읽힌다.

**비용.** 실제 LLM 호출은 호출자가 gateway를 넘길 때만 일어난다. 이 모듈은
gateway를 주입받으므로 테스트는 한 번도 외부로 나가지 않는다.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Sequence

from pydantic import Field, computed_field, model_validator

from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    BOUNDARY_PAIRS,
    TAXONOMY_VERSION,
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
    expected_classification,
    subclause_belongs_to_clause,
)
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    CallReceipt,
    ContractModel,
    FailureCode,
    FailureStage,
    GeneratedDocumentIR,
    NonEmptyText,
    ParagraphBlock,
    Pass2Assessment,
    Sha256Hex,
    SourceDocumentSnapshot,
    StageFailure,
)
from rd2.source_generation.evidence import (
    EvidenceResolutionError,
    validate_evidence_quotes,
)
from rd2.source_generation.pipeline import (
    PipelineConfig,
    StructuredCallError,
    StructuredOutputGateway,
)
from rd2.source_generation.prompts import (
    PromptBundle,
    build_prompt_bundle,
    render_pass2_user_prompt,
)

HOLDOUT_POLICY_VERSION = "holdout-classification-eval-v1"

#: O 문서에는 세부조항이 없다. confusion matrix에서 그 자리를 표시하는 값.
NO_SUBCLAUSE = "__none__"


class HoldoutEvalError(ValueError):
    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class HoldoutCase(ContractModel):
    """실제 원문 하나와 그 문서의 **확인된** 정답 라벨.

    라벨은 모델이 아니라 원문 자체(PRISM 공개제한근거 등)에서 온 것이어야
    한다. 모델이 붙인 라벨을 정답으로 쓰면 이 평가는 자기 자신을 채점한다.

    **라벨 세밀도는 축마다 다를 수 있다.** 실측 결과 코퍼스의 C/S 라벨은
    정보공개법 '호'까지만 있고 24개 세부조항 정답은 어디에도 없다. 그래서
    C/S 사례라도 ``subclause_key``를 비워둘 수 있고, 그 경우 세부조항 축은
    채점에서 제외된다 — 정답이 없는 축을 0점으로 세면 정확도가 거짓으로
    낮아진다. O 사례의 ``subclause_key=None``은 '모른다'가 아니라 '없다'가
    정답이므로 채점 대상이다.
    """

    case_id: NonEmptyText
    source_document_id: NonEmptyText
    source_sha256: Sha256Hex
    document_type: SemanticDocumentType
    other_document_type: NonEmptyText | None = None
    classification: CsoClassification
    clause_no: ClauseNumber | None = None
    subclause_key: SubclauseKey | None = None

    @model_validator(mode="after")
    def _label_must_be_coherent(self) -> "HoldoutCase":
        if self.document_type == SemanticDocumentType.OTHER:
            if self.other_document_type is None:
                raise ValueError("other_document_type is required for document_type=other")
        elif self.other_document_type is not None:
            raise ValueError("other_document_type is only allowed for document_type=other")

        if self.classification == CsoClassification.O:
            if self.clause_no is not None or self.subclause_key is not None:
                raise ValueError("O holdout case cannot have clause or subclause")
            return self

        if self.clause_no is None:
            raise ValueError("C/S holdout case requires a clause")
        if expected_classification(self.clause_no) != self.classification:
            raise ValueError(
                f"clause {self.clause_no.value} does not map to "
                f"classification {self.classification.value}"
            )
        # subclause_key는 선택이다 — 없으면 세부조항 축을 채점하지 않는다.
        if self.subclause_key is not None and not subclause_belongs_to_clause(
            self.clause_no, self.subclause_key
        ):
            raise ValueError(
                f"subclause {self.subclause_key.value!r} does not belong to "
                f"clause {self.clause_no.value}"
            )
        return self

    @property
    def has_subclause_label(self) -> bool:
        """세부조항 축을 채점할 수 있는가.

        O는 '세부조항 없음'이 정답이라 채점 대상이고, C/S인데 비어 있으면
        정답을 모르는 것이라 채점 대상이 아니다.
        """

        return (
            self.classification == CsoClassification.O
            or self.subclause_key is not None
        )

    @property
    def stratum(self) -> str:
        """층화 표본 추출과 confusion matrix가 함께 쓰는 셀 키."""

        if self.classification == CsoClassification.O:
            return CsoClassification.O.value
        if self.subclause_key is None:
            # 세부조항 정답이 없으면 호 단위까지만 층을 나눈다.
            return f"clause{self.clause_no.value}"
        return self.subclause_key.value


class HoldoutManifest(ContractModel):
    """고정 seed와 hash로 재현되는 평가셋.

    ``excluded_document_ids``는 생성 입력·prompt 예시로 이미 쓴 문서다. 겹치면
    평가가 학습셋을 채점하게 되므로 계약 수준에서 막는다.
    """

    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    policy_version: Literal["holdout-classification-eval-v1"] = HOLDOUT_POLICY_VERSION
    taxonomy_version: Literal["source-generation-taxonomy-v2"] = TAXONOMY_VERSION
    manifest_id: NonEmptyText
    created_at: datetime
    seed: int = Field(ge=0)
    cases: tuple[HoldoutCase, ...] = Field(min_length=1)
    excluded_document_ids: tuple[NonEmptyText, ...] = ()

    @model_validator(mode="after")
    def _cases_must_be_unique_and_held_out(self) -> "HoldoutManifest":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("holdout case IDs must be unique")
        document_ids = [case.source_document_id for case in self.cases]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("holdout source document IDs must be unique")
        leaked = sorted(set(document_ids) & set(self.excluded_document_ids))
        if leaked:
            raise ValueError(
                f"holdout manifest overlaps generation inputs: {leaked}"
            )
        return self

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "taxonomy_version": self.taxonomy_version,
            "manifest_id": self.manifest_id,
            "seed": self.seed,
            "cases": [
                {
                    "case_id": case.case_id,
                    "source_document_id": case.source_document_id,
                    "source_sha256": case.source_sha256,
                    "document_type": case.document_type.value,
                    "classification": case.classification.value,
                    "clause_no": case.clause_no.value if case.clause_no else None,
                    "subclause_key": (
                        case.subclause_key.value if case.subclause_key else None
                    ),
                }
                for case in self.cases
            ],
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            self.fingerprint_payload(),
            normalization_version=NORMALIZATION_VERSION,
        )

    def stratum_counts(self) -> Mapping[str, int]:
        return dict(Counter(case.stratum for case in self.cases))


class CaseOutcome(ContractModel):
    """한 모델이 한 사례를 분류한 결과. 실패도 산출물의 일부로 남긴다."""

    case_id: NonEmptyText
    model_id: NonEmptyText
    assessment: Pass2Assessment | None = None
    receipt: CallReceipt | None = None
    failure: StageFailure | None = None

    @model_validator(mode="after")
    def _outcome_must_be_coherent(self) -> "CaseOutcome":
        if (self.assessment is None) == (self.failure is None):
            raise ValueError("case outcome requires exactly one of assessment/failure")
        if self.receipt is not None and self.assessment is None:
            raise ValueError("case receipt requires an assessment")
        return self

    @computed_field
    @property
    def succeeded(self) -> bool:
        return self.failure is None


class AxisAccuracy(ContractModel):
    """축별 정확도.

    **축마다 분모가 다르다.** 세부조항 정답이 없는 사례는 세부조항 축의
    분모에서 빠진다. 정답 없는 축을 오답으로 세면 정확도가 거짓으로 낮아지고,
    반대로 분모에 넣고 맞은 걸로 세면 거짓으로 높아진다.
    """

    scored: int = Field(ge=0)
    document_type_correct: int = Field(ge=0)
    classification_correct: int = Field(ge=0)
    clause_correct: int = Field(ge=0)
    subclause_scored: int = Field(ge=0)
    subclause_correct: int = Field(ge=0)

    @model_validator(mode="after")
    def _counts_must_fit(self) -> "AxisAccuracy":
        for name, value in (
            ("document_type_correct", self.document_type_correct),
            ("classification_correct", self.classification_correct),
            ("clause_correct", self.clause_correct),
            ("subclause_scored", self.subclause_scored),
        ):
            if value > self.scored:
                raise ValueError(f"{name} cannot exceed scored cases")
        if self.subclause_correct > self.subclause_scored:
            raise ValueError("subclause_correct cannot exceed subclause_scored")
        return self

    @staticmethod
    def _rate(correct: int, total: int) -> float:
        return (correct / total) if total else 0.0

    @computed_field
    @property
    def document_type_accuracy(self) -> float:
        return self._rate(self.document_type_correct, self.scored)

    @computed_field
    @property
    def classification_accuracy(self) -> float:
        return self._rate(self.classification_correct, self.scored)

    @computed_field
    @property
    def clause_accuracy(self) -> float:
        return self._rate(self.clause_correct, self.scored)

    @computed_field
    @property
    def subclause_accuracy(self) -> float:
        return self._rate(self.subclause_correct, self.subclause_scored)


class ConfusionCell(ContractModel):
    true_subclause: NonEmptyText
    predicted_subclause: NonEmptyText
    count: int = Field(gt=0)


class BoundaryPairResult(ContractModel):
    """설계가 지목한 혼동 쌍에서 실제로 얼마나 섞였는지."""

    left: NonEmptyText
    right: NonEmptyText
    left_total: int = Field(ge=0)
    right_total: int = Field(ge=0)
    left_predicted_as_right: int = Field(ge=0)
    right_predicted_as_left: int = Field(ge=0)

    @computed_field
    @property
    def confusions(self) -> int:
        return self.left_predicted_as_right + self.right_predicted_as_left


class OverFlagging(ContractModel):
    """진짜 공개(O) 문서를 C/S로 잘못 찍은 비율.

    실제 C/S 문서는 비공개라 본문을 갖고 있지 않으므로 재현율은 이 코퍼스로
    잴 수 없다. 반대 방향인 오탐은 공개 문서만으로 측정 가능하고, 값이 높으면
    채점자가 근거 없이 민감 판정을 남발한다는 뜻이라 그 자체로 신호다.
    """

    open_scored: int = Field(ge=0)
    flagged_c_or_s: int = Field(ge=0)

    @model_validator(mode="after")
    def _flagged_must_fit(self) -> "OverFlagging":
        if self.flagged_c_or_s > self.open_scored:
            raise ValueError("flagged_c_or_s cannot exceed open_scored")
        return self

    @computed_field
    @property
    def over_flagging_rate(self) -> float:
        return (self.flagged_c_or_s / self.open_scored) if self.open_scored else 0.0


class ModelEvalResult(ContractModel):
    model_id: NonEmptyText
    accuracy: AxisAccuracy
    over_flagging: OverFlagging
    confusion: tuple[ConfusionCell, ...] = ()
    boundary_pairs: tuple[BoundaryPairResult, ...] = ()
    failed_case_ids: tuple[NonEmptyText, ...] = ()


class HoldoutEvalReport(ContractModel):
    """평가 결과 + 무효화 판단에 필요한 모든 지문.

    ``taxonomy_version``이나 ``prompt_bundle_sha256``이 바뀌면 이 리포트는
    낡은 것이다 — 세부조항의 의미나 판정 지침이 달라졌기 때문이다.
    """

    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    policy_version: Literal["holdout-classification-eval-v1"] = HOLDOUT_POLICY_VERSION
    manifest_sha256: Sha256Hex
    taxonomy_version: NonEmptyText
    prompt_bundle_sha256: Sha256Hex
    generated_at: datetime
    stratum_counts: Mapping[str, int]
    results: tuple[ModelEvalResult, ...] = Field(min_length=1)

    def is_stale_against(self, prompt_bundle: PromptBundle) -> bool:
        return (
            self.taxonomy_version != prompt_bundle.taxonomy_version
            or self.prompt_bundle_sha256 != prompt_bundle.sha256
        )


@dataclass(frozen=True)
class HoldoutEvalConfig:
    max_output_tokens: int = 4_000

    def __post_init__(self) -> None:
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


def snapshot_to_document_ir(
    snapshot: SourceDocumentSnapshot,
    *,
    title: str,
) -> GeneratedDocumentIR:
    """실제 원문을 P2가 보는 직렬화 형식으로 감싼다.

    block ID를 그대로 보존하므로 P2가 반환한 evidence span을 원문에 대해
    검증할 수 있다. 형식만 맞추고 내용은 손대지 않는 것이 요점이다 — 요약하거나
    자르면 난이도가 달라져 비교가 무의미해진다.
    """

    blocks = tuple(
        ParagraphBlock(block_id=block.block_id, text=block.text)
        for page in snapshot.pages
        for block in page.blocks
    )
    if not blocks:
        raise HoldoutEvalError(
            FailureCode.DOCUMENT_EMPTY,
            f"holdout snapshot {snapshot.source_document_id!r} has no blocks",
        )
    return GeneratedDocumentIR(title=title, blocks=blocks)


def build_stratified_manifest(
    candidates: Sequence[HoldoutCase],
    *,
    manifest_id: str,
    created_at: datetime,
    seed: int,
    per_stratum: int,
    excluded_document_ids: Sequence[str] = (),
) -> HoldoutManifest:
    """층별 상한을 두고 결정론적으로 표본을 뽑는다.

    희소 층(제1호·제8호 등)은 후보가 ``per_stratum``보다 적으면 있는 만큼 전부
    가져간다 — 희소하다는 이유로 층 자체가 사라지면 그 조항의 정확도를 영영
    못 잰다. 같은 ``seed``와 같은 후보 집합이면 항상 같은 표본이 나온다.
    """

    if per_stratum < 1:
        raise ValueError("per_stratum must be at least 1")

    excluded = set(excluded_document_ids)
    eligible = [
        case for case in candidates if case.source_document_id not in excluded
    ]
    if not eligible:
        raise HoldoutEvalError(
            FailureCode.MANIFEST_INVALID,
            "no holdout candidates remain after removing generation inputs",
        )

    by_stratum: dict[str, list[HoldoutCase]] = {}
    for case in eligible:
        by_stratum.setdefault(case.stratum, []).append(case)

    selected: list[HoldoutCase] = []
    for stratum in sorted(by_stratum):
        # case_id로 먼저 정렬해 입력 순서가 표본에 영향을 주지 않게 한다.
        pool = sorted(by_stratum[stratum], key=lambda case: case.case_id)
        if len(pool) <= per_stratum:
            selected.extend(pool)
            continue
        rng = random.Random(f"{seed}:{stratum}")
        selected.extend(rng.sample(pool, per_stratum))

    selected.sort(key=lambda case: case.case_id)
    return HoldoutManifest(
        manifest_id=manifest_id,
        created_at=created_at,
        seed=seed,
        cases=tuple(selected),
        excluded_document_ids=tuple(sorted(excluded)),
    )


def _predicted_stratum(assessment: Pass2Assessment) -> str:
    if assessment.subclause_key is None:
        return CsoClassification.O.value
    return assessment.subclause_key.value


def _score_axes(
    pairs: Sequence[tuple[HoldoutCase, Pass2Assessment]],
) -> AxisAccuracy:
    document_type = classification = clause = 0
    subclause_scored = subclause_correct = 0
    for case, assessment in pairs:
        if case.document_type == assessment.document_type:
            document_type += 1
        if case.classification == assessment.classification:
            classification += 1
        if case.clause_no == assessment.clause_no:
            clause += 1
        if case.has_subclause_label:
            subclause_scored += 1
            if case.subclause_key == assessment.subclause_key:
                subclause_correct += 1
    return AxisAccuracy(
        scored=len(pairs),
        document_type_correct=document_type,
        classification_correct=classification,
        clause_correct=clause,
        subclause_scored=subclause_scored,
        subclause_correct=subclause_correct,
    )


def _build_confusion(
    pairs: Sequence[tuple[HoldoutCase, Pass2Assessment]],
) -> tuple[ConfusionCell, ...]:
    counter = Counter(
        (case.stratum, _predicted_stratum(assessment)) for case, assessment in pairs
    )
    return tuple(
        ConfusionCell(
            true_subclause=true_key,
            predicted_subclause=predicted_key,
            count=count,
        )
        for (true_key, predicted_key), count in sorted(counter.items())
    )


def _build_boundary_pairs(
    pairs: Sequence[tuple[HoldoutCase, Pass2Assessment]],
) -> tuple[BoundaryPairResult, ...]:
    results: list[BoundaryPairResult] = []
    for left, right in BOUNDARY_PAIRS:
        left_total = right_total = left_as_right = right_as_left = 0
        for case, assessment in pairs:
            predicted = _predicted_stratum(assessment)
            if case.stratum == left.value:
                left_total += 1
                if predicted == right.value:
                    left_as_right += 1
            elif case.stratum == right.value:
                right_total += 1
                if predicted == left.value:
                    right_as_left += 1
        results.append(
            BoundaryPairResult(
                left=left.value,
                right=right.value,
                left_total=left_total,
                right_total=right_total,
                left_predicted_as_right=left_as_right,
                right_predicted_as_left=right_as_left,
            )
        )
    return tuple(results)


def classify_case(
    case: HoldoutCase,
    document: GeneratedDocumentIR,
    gateway: StructuredOutputGateway,
    *,
    model_id: str,
    prompt_bundle: PromptBundle,
    config: HoldoutEvalConfig,
) -> CaseOutcome:
    """한 사례를 P2 프롬프트로 분류한다. evidence span까지 원문에 대조한다."""

    definition = prompt_bundle.definition("pass2")
    try:
        call = gateway.parse(
            model=model_id,
            system_prompt=definition.system_prompt,
            user_prompt=render_pass2_user_prompt(
                document.model_dump_json(indent=2)
            ),
            response_model=Pass2Assessment,
            max_output_tokens=config.max_output_tokens,
        )
    except StructuredCallError as exc:
        return CaseOutcome(
            case_id=case.case_id,
            model_id=model_id,
            failure=StageFailure(
                stage=FailureStage.PASS2,
                code=exc.code,
                retryable=exc.retryable,
                message=str(exc),
            ),
        )

    assessment = call.parsed
    try:
        # 파이프라인(execute_pass1/2)과 같은 검증 경로를 쓴다. 인용문이 실제로
        # 존재하고 유일한지만 보며, 문자 위치는 코드가 찾는다.
        validate_evidence_quotes(assessment.evidence_spans, document.block_text)
    except (EvidenceResolutionError, ValueError) as exc:
        return CaseOutcome(
            case_id=case.case_id,
            model_id=model_id,
            failure=StageFailure(
                stage=FailureStage.PASS2,
                code=FailureCode.EVIDENCE_INVALID,
                retryable=False,
                message=str(exc),
            ),
        )

    return CaseOutcome(
        case_id=case.case_id,
        model_id=model_id,
        assessment=assessment,
        receipt=CallReceipt(
            stage=FailureStage.PASS2,
            model_id=model_id,
            response_id=call.response_id,
            request_id=call.request_id,
            token_usage=call.token_usage,
        ),
    )


def summarize_outcomes(
    manifest: HoldoutManifest,
    outcomes: Sequence[CaseOutcome],
    *,
    model_id: str,
) -> ModelEvalResult:
    """한 모델의 결과를 축별 정확도·confusion·경계쌍으로 집계한다."""

    cases_by_id = {case.case_id: case for case in manifest.cases}
    pairs: list[tuple[HoldoutCase, Pass2Assessment]] = []
    failed: list[str] = []
    for outcome in outcomes:
        if outcome.model_id != model_id:
            continue
        case = cases_by_id.get(outcome.case_id)
        if case is None:
            raise HoldoutEvalError(
                FailureCode.MANIFEST_INVALID,
                f"outcome references unknown case {outcome.case_id!r}",
            )
        if outcome.assessment is None:
            failed.append(outcome.case_id)
            continue
        pairs.append((case, outcome.assessment))

    open_pairs = [
        (case, assessment)
        for case, assessment in pairs
        if case.classification == CsoClassification.O
    ]
    return ModelEvalResult(
        model_id=model_id,
        accuracy=_score_axes(pairs),
        over_flagging=OverFlagging(
            open_scored=len(open_pairs),
            flagged_c_or_s=sum(
                1
                for _, assessment in open_pairs
                if assessment.classification != CsoClassification.O
            ),
        ),
        confusion=_build_confusion(pairs),
        boundary_pairs=_build_boundary_pairs(pairs),
        failed_case_ids=tuple(sorted(failed)),
    )


def run_holdout_eval(
    manifest: HoldoutManifest,
    snapshots: Mapping[str, SourceDocumentSnapshot],
    titles: Mapping[str, str],
    gateway: StructuredOutputGateway,
    *,
    pipeline_config: PipelineConfig,
    generated_at: datetime,
    prompt_bundle: PromptBundle | None = None,
    config: HoldoutEvalConfig | None = None,
) -> HoldoutEvalReport:
    """생성 모델과 채점 모델 각각으로 held-out 전량을 분류하고 집계한다.

    호출 수는 ``len(cases) * 2``다. 실제 LLM을 쓰는 gateway를 넘기면 그만큼
    과금되므로, 호출자가 명시적으로 opt-in하도록 CLI에서 플래그로 가둔다.
    """

    resolved_bundle = prompt_bundle or build_prompt_bundle()
    resolved_config = config or HoldoutEvalConfig()

    if resolved_bundle.taxonomy_version != manifest.taxonomy_version:
        raise HoldoutEvalError(
            FailureCode.MANIFEST_INVALID,
            "manifest taxonomy version does not match the prompt bundle",
        )

    models = (pipeline_config.generator_model, pipeline_config.grader_model)
    outcomes: list[CaseOutcome] = []
    for case in manifest.cases:
        snapshot = snapshots.get(case.source_document_id)
        if snapshot is None:
            raise HoldoutEvalError(
                FailureCode.SOURCE_MISSING,
                f"no snapshot for holdout document {case.source_document_id!r}",
            )
        if snapshot.source_sha256 != case.source_sha256:
            raise HoldoutEvalError(
                FailureCode.SOURCE_CHANGED,
                f"holdout document {case.source_document_id!r} changed since sampling",
            )
        document = snapshot_to_document_ir(
            snapshot,
            title=titles.get(case.source_document_id, case.source_document_id),
        )
        for model_id in models:
            outcomes.append(
                classify_case(
                    case,
                    document,
                    gateway,
                    model_id=model_id,
                    prompt_bundle=resolved_bundle,
                    config=resolved_config,
                )
            )

    return HoldoutEvalReport(
        manifest_sha256=manifest.sha256,
        taxonomy_version=resolved_bundle.taxonomy_version,
        prompt_bundle_sha256=resolved_bundle.sha256,
        generated_at=generated_at,
        stratum_counts=manifest.stratum_counts(),
        results=tuple(
            summarize_outcomes(manifest, outcomes, model_id=model_id)
            for model_id in models
        ),
    )
