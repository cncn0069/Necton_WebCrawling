"""원본문서 기반 생성 파이프라인의 versioned Pydantic 계약.

OpenAI structured output, 실행 journal, audit bridge가 이 모델에서 파생된
JSON Schema와 validation을 함께 사용한다. 민감 원문 텍스트는 local snapshot
artifact에만 둘 수 있으며 journal에는 hash와 artifact reference만 기록한다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Callable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from rd2.administrative_status import AdminStatus
from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    TAXONOMY_VERSION,
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
    expected_classification,
    subclause_belongs_to_clause,
)

CONTRACT_SCHEMA_VERSION = "1.0.0"

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


class EvidenceSpan(ContractModel):
    block_id: NonEmptyText
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: NonEmptyText

    @model_validator(mode="after")
    def _end_must_follow_start(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class ParagraphBlock(ContractModel):
    kind: Literal["paragraph"] = "paragraph"
    block_id: NonEmptyText
    text: NonEmptyText

    def render_text(self) -> str:
        return self.text


class BulletListBlock(ContractModel):
    kind: Literal["bullet_list"] = "bullet_list"
    block_id: NonEmptyText
    items: tuple[NonEmptyText, ...] = Field(min_length=1)

    def render_text(self) -> str:
        return "\n".join(f"- {item}" for item in self.items)


class KeyValueEntry(ContractModel):
    key: NonEmptyText
    value: NonEmptyText


class KeyValueBlock(ContractModel):
    kind: Literal["key_value"] = "key_value"
    block_id: NonEmptyText
    entries: tuple[KeyValueEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_must_be_unique(self) -> "KeyValueBlock":
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("key-value block keys must be unique")
        return self

    def render_text(self) -> str:
        return "\n".join(f"{entry.key}: {entry.value}" for entry in self.entries)


class TableBlock(ContractModel):
    kind: Literal["table"] = "table"
    block_id: NonEmptyText
    columns: tuple[NonEmptyText, ...] = Field(min_length=1)
    rows: tuple[tuple[str, ...], ...] = Field(min_length=1)

    @field_validator("rows")
    @classmethod
    def _strip_table_cells(cls, rows: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(cell.strip() for cell in row) for row in rows)

    @model_validator(mode="after")
    def _table_shape_must_match(self) -> "TableBlock":
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("table columns must be unique")
        for index, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"table row {index} has {len(row)} cells; expected {len(self.columns)}"
                )
        return self

    def render_text(self) -> str:
        lines = ["\t".join(self.columns)]
        lines.extend("\t".join(row) for row in self.rows)
        return "\n".join(lines)


class AttachmentReferenceBlock(ContractModel):
    kind: Literal["attachment_reference"] = "attachment_reference"
    block_id: NonEmptyText
    attachment_id: NonEmptyText
    label: NonEmptyText
    description: NonEmptyText | None = None

    def render_text(self) -> str:
        rendered = f"[첨부] {self.label} ({self.attachment_id})"
        if self.description:
            rendered = f"{rendered}: {self.description}"
        return rendered


DocumentBlock = (
    ParagraphBlock
    | BulletListBlock
    | KeyValueBlock
    | TableBlock
    | AttachmentReferenceBlock
)


class GeneratedDocumentIR(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    title: NonEmptyText
    blocks: tuple[DocumentBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _block_ids_must_be_unique(self) -> "GeneratedDocumentIR":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("generated document block IDs must be unique")
        return self

    def block_text(self, block_id: str) -> str:
        for block in self.blocks:
            if block.block_id == block_id:
                return block.render_text()
        raise ValueError(f"unknown generated document block_id: {block_id!r}")

    @computed_field
    @property
    def body_text(self) -> str:
        """IR block 순서에서 결정론적으로 파생되는 본문."""

        return "\n\n".join(block.render_text() for block in self.blocks)


class SourceTextBlock(ContractModel):
    block_id: NonEmptyText
    text: NonEmptyText


class SourcePage(ContractModel):
    page_number: int = Field(ge=1)
    blocks: tuple[SourceTextBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _block_ids_must_be_unique(self) -> "SourcePage":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError(f"source page {self.page_number} block IDs must be unique")
        return self


class SourceDocumentSnapshot(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    source_document_id: NonEmptyText
    source: NonEmptyText
    manifest_key: NonEmptyText
    source_sha256: Sha256Hex
    pages: tuple[SourcePage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _page_and_block_ids_must_be_unique(self) -> "SourceDocumentSnapshot":
        page_numbers = [page.page_number for page in self.pages]
        expected_page_numbers = list(range(1, len(self.pages) + 1))
        if page_numbers != expected_page_numbers:
            raise ValueError("source pages must be contiguous and sorted from page 1")
        block_ids = [block.block_id for page in self.pages for block in page.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("source block IDs must be unique across the document")
        return self

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def block_text(self, block_id: str) -> str:
        for page in self.pages:
            for block in page.blocks:
                if block.block_id == block_id:
                    return block.text
        raise ValueError(f"unknown source block_id: {block_id!r}")


class SelectionMethod(str, Enum):
    FULL_DOCUMENT = "full_document"
    FRONT_RELEVANCE = "front_relevance"


class RelevanceCandidateBlock(ContractModel):
    page_number: int = Field(ge=1)
    block_id: NonEmptyText
    text: NonEmptyText


class RelevanceSelectionRequest(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    source_document_id: NonEmptyText
    source_sha256: Sha256Hex
    selection_config_sha256: Sha256Hex
    original_page_count: int = Field(gt=85)
    candidate_page_numbers: tuple[int, ...] = Field(min_length=1)
    candidate_blocks: tuple[RelevanceCandidateBlock, ...] = Field(min_length=1)
    estimated_input_tokens: int = Field(ge=1)

    @model_validator(mode="after")
    def _candidates_must_be_ordered_and_unique(self) -> "RelevanceSelectionRequest":
        page_numbers = tuple(sorted(set(block.page_number for block in self.candidate_blocks)))
        if page_numbers != self.candidate_page_numbers:
            raise ValueError("candidate_page_numbers must match candidate blocks in source order")
        block_ids = [block.block_id for block in self.candidate_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("relevance candidate block IDs must be unique")
        return self


class RelevanceSelectionResponse(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    selected_block_ids: tuple[NonEmptyText, ...] = Field(min_length=1)
    rationale: NonEmptyText

    @model_validator(mode="after")
    def _selected_block_ids_must_be_unique(self) -> "RelevanceSelectionResponse":
        if len(self.selected_block_ids) != len(set(self.selected_block_ids)):
            raise ValueError("selected relevance block IDs must be unique")
        return self


class DocumentSelection(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    policy_version: NonEmptyText
    method: SelectionMethod
    source_sha256: Sha256Hex
    selection_sha256: Sha256Hex
    original_page_count: int = Field(ge=1)
    page_threshold: int = Field(default=85, ge=1)
    selected_page_numbers: tuple[int, ...] = Field(min_length=1)
    selected_block_ids: tuple[NonEmptyText, ...] = Field(min_length=1)
    truncated: bool

    @model_validator(mode="after")
    def _selection_must_match_method(self) -> "DocumentSelection":
        if (
            tuple(sorted(set(self.selected_page_numbers))) != self.selected_page_numbers
            or self.selected_page_numbers[0] < 1
            or self.selected_page_numbers[-1] > self.original_page_count
        ):
            raise ValueError("selected page numbers must be unique, sorted, and in range")
        if len(self.selected_block_ids) != len(set(self.selected_block_ids)):
            raise ValueError("selected block IDs must be unique")
        if self.method == SelectionMethod.FULL_DOCUMENT:
            expected_pages = tuple(range(1, self.original_page_count + 1))
            if self.original_page_count > self.page_threshold:
                raise ValueError("full_document is only allowed at or below page_threshold")
            if self.truncated or self.selected_page_numbers != expected_pages:
                raise ValueError("full_document must select every page without truncation")
        else:
            if self.original_page_count <= self.page_threshold:
                raise ValueError("front_relevance requires a document above page_threshold")
            if not self.truncated:
                raise ValueError("front_relevance must record truncated=true")
        return self


class LegalClassification(ContractModel):
    """근거 → 이유 → 판정 순서로 필드를 선언한다.

    OpenAI strict structured output은 스키마의 property 순서대로 자기회귀
    생성하므로 **필드 순서가 곧 추론 순서**다. 판정을 먼저 두면
    ``evidence_spans``가 판정의 근거가 아니라 이미 내린 답을 뒷받침할 인용구를
    찾는 사후 정당화가 되고, 인용 불일치로 ``EVIDENCE_INVALID`` hard failure가
    늘어난다.

    순서를 바꿔도 JSON 구조는 동일하므로 ``CONTRACT_SCHEMA_VERSION``은 올리지
    않는다. 바뀐 것은 산출물의 shape이 아니라 생성 방식이므로
    ``PROMPT_BUNDLE_VERSION``으로 추적하고 journal을 무효화한다.
    """

    document_type: SemanticDocumentType
    other_document_type: NonEmptyText | None = None
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    rationale: NonEmptyText
    classification: CsoClassification
    clause_no: ClauseNumber | None = None
    subclause_key: SubclauseKey | None = None

    @model_validator(mode="after")
    def _classification_must_be_coherent(self) -> "LegalClassification":
        if self.document_type == SemanticDocumentType.OTHER:
            if self.other_document_type is None:
                raise ValueError("other_document_type is required for document_type=other")
        elif self.other_document_type is not None:
            raise ValueError("other_document_type is only allowed for document_type=other")

        if self.classification == CsoClassification.O:
            if self.clause_no is not None or self.subclause_key is not None:
                raise ValueError("O classification cannot have clause or subclause")
            return self

        if self.clause_no is None or self.subclause_key is None:
            raise ValueError("C/S classification requires clause and subclause")
        if expected_classification(self.clause_no) != self.classification:
            raise ValueError(
                f"clause {self.clause_no.value} does not map to "
                f"classification {self.classification.value}"
            )
        if not subclause_belongs_to_clause(self.clause_no, self.subclause_key):
            raise ValueError(
                f"subclause {self.subclause_key.value!r} does not belong to "
                f"clause {self.clause_no.value}"
            )
        if not self.evidence_spans:
            raise ValueError("C/S classification requires at least one evidence span")
        return self

    def validate_evidence_against(self, block_text: Callable[[str], str]) -> None:
        """``block_text(block_id) -> str`` resolver에 span을 대조한다."""

        for span in self.evidence_spans:
            text = block_text(span.block_id)
            if span.end > len(text):
                raise ValueError(
                    f"evidence span for block {span.block_id!r} ends outside the block"
                )
            actual = text[span.start : span.end]
            if actual != span.quote:
                raise ValueError(
                    f"evidence quote mismatch for block {span.block_id!r} "
                    f"at range {span.start}:{span.end}"
                )


class SourceClassification(LegalClassification):
    def validate_against_snapshot(self, snapshot: SourceDocumentSnapshot) -> None:
        self.validate_evidence_against(snapshot.block_text)


class TargetClassification(str, Enum):
    C = "C"
    S = "S"


class GenerationMode(str, Enum):
    SOURCE_ALIGNED = "source_aligned"
    COUNTERFACTUAL = "counterfactual"


class GenerationRoute(str, Enum):
    SOURCE_ALIGNED = "source_aligned"
    SPAN_SEEDED = "span_seeded"
    ANCHORED = "anchored"
    ADMINISTRATIVE_AUGMENTED = "administrative_augmented"
    FULLY_SYNTHETIC = "fully_synthetic"


class SourceEvidenceLevel(str, Enum):
    DIRECT_LEGAL_EVIDENCE = "direct_legal_evidence"
    DIRECT_SENSITIVE_SPAN = "direct_sensitive_span"
    CONTEXTUAL_ANCHOR_ONLY = "contextual_anchor_only"
    NO_USABLE_PUBLIC_SOURCE = "no_usable_public_source"


class AssessmentScope(str, Enum):
    FULL_DOCUMENT = "full_document"
    SELECTED_VIEW_ONLY = "selected_view_only"


class SourceSuitability(ContractModel):
    """근거 → 이유 → 판정 순서. ``LegalClassification``의 주석 참고."""

    assessment_scope: AssessmentScope
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    rationale: NonEmptyText
    evidence_level: SourceEvidenceLevel
    reason_code: NonEmptyText

    @model_validator(mode="after")
    def _evidence_must_match_level(self) -> "SourceSuitability":
        if self.evidence_level == SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE:
            if self.evidence_spans:
                raise ValueError(
                    "no_usable_public_source cannot include evidence spans"
                )
        elif not self.evidence_spans:
            raise ValueError(f"{self.evidence_level.value} requires evidence spans")
        return self

    def validate_evidence_against(self, block_text: Callable[[str], str]) -> None:
        for span in self.evidence_spans:
            text = block_text(span.block_id)
            if span.end > len(text):
                raise ValueError(
                    f"suitability span for block {span.block_id!r} ends outside the block"
                )
            if text[span.start : span.end] != span.quote:
                raise ValueError(
                    f"suitability quote mismatch for block {span.block_id!r} "
                    f"at range {span.start}:{span.end}"
                )


class GenerationTarget(ContractModel):
    classification: TargetClassification
    clause_no: ClauseNumber | None = None
    subclause_key: SubclauseKey | None = None
    administrative_statuses: tuple[AdminStatus, ...] = ()
    generation_mode: GenerationMode

    @model_validator(mode="after")
    def _target_must_be_coherent(self) -> "GenerationTarget":
        if len(self.administrative_statuses) != len(
            set(self.administrative_statuses)
        ):
            raise ValueError("target administrative statuses must be unique")
        if (self.clause_no is None) != (self.subclause_key is None):
            raise ValueError(
                "target clause_no and subclause_key must be both present or both absent"
            )
        if self.clause_no is None:
            if (
                self.classification != TargetClassification.S
                or not self.administrative_statuses
            ):
                raise ValueError(
                    "target without a legal clause requires S and an "
                    "administrative status"
                )
            return self

        target_classification = CsoClassification(self.classification.value)
        if expected_classification(self.clause_no) != target_classification:
            raise ValueError(
                f"target clause {self.clause_no.value} does not map to "
                f"classification {self.classification.value}"
            )
        if not subclause_belongs_to_clause(self.clause_no, self.subclause_key):
            raise ValueError(
                f"target subclause {self.subclause_key.value!r} does not belong to "
                f"clause {self.clause_no.value}"
            )
        return self


class Pass1Result(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    source_classification: SourceClassification
    source_suitability: SourceSuitability
    generation_route: GenerationRoute
    generation_target: GenerationTarget
    generated_document: GeneratedDocumentIR

    @model_validator(mode="after")
    def _source_target_and_route_must_be_coherent(self) -> "Pass1Result":
        source = self.source_classification
        target = self.generation_target
        suitability = self.source_suitability
        route = self.generation_route

        if route == GenerationRoute.SOURCE_ALIGNED:
            if source.classification == CsoClassification.O:
                raise ValueError("source_aligned route requires a C/S source")
            if suitability.evidence_level != SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE:
                raise ValueError(
                    "source_aligned route requires direct_legal_evidence"
                )
        else:
            if source.classification != CsoClassification.O:
                raise ValueError(f"{route.value} route requires an O source")

        if route == GenerationRoute.SPAN_SEEDED:
            if suitability.evidence_level != SourceEvidenceLevel.DIRECT_SENSITIVE_SPAN:
                raise ValueError(
                    "span_seeded route requires direct_sensitive_span"
                )
            if target.clause_no == ClauseNumber.CLAUSE_6:
                raise ValueError(
                    "clause 6 span_seeded is disabled until de-identification is implemented"
                )
        elif route == GenerationRoute.ANCHORED:
            if suitability.evidence_level != SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY:
                raise ValueError(
                    "anchored route requires contextual_anchor_only"
                )
        elif route == GenerationRoute.ADMINISTRATIVE_AUGMENTED:
            if suitability.evidence_level != SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY:
                raise ValueError(
                    "administrative_augmented route requires "
                    "contextual_anchor_only"
                )
            if (
                target.clause_no is not None
                or not target.administrative_statuses
            ):
                raise ValueError(
                    "administrative_augmented route requires an admin-only target"
                )
        elif route == GenerationRoute.FULLY_SYNTHETIC:
            if suitability.evidence_level != SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE:
                raise ValueError(
                    "fully_synthetic route requires no_usable_public_source"
                )
            if target.clause_no is None:
                raise ValueError(
                    "admin-only target requires administrative_augmented route"
                )

        if source.classification == CsoClassification.O:
            if target.generation_mode != GenerationMode.COUNTERFACTUAL:
                raise ValueError("O source requires generation_mode=counterfactual")
            return self

        if target.generation_mode != GenerationMode.SOURCE_ALIGNED:
            raise ValueError("C/S source requires generation_mode=source_aligned")
        if (
            target.classification.value != source.classification.value
            or target.clause_no != source.clause_no
            or target.subclause_key != source.subclause_key
        ):
            raise ValueError("source_aligned target must exactly match the C/S source label")
        return self


class GenerationProvenance(ContractModel):
    generation_route: GenerationRoute
    source_evidence_level: SourceEvidenceLevel
    reason_code: NonEmptyText
    requested_target: GenerationTarget
    final_target: GenerationTarget
    selection_sha256: Sha256Hex
    uses_source_evidence: bool
    validated_evidence_spans: tuple[EvidenceSpan, ...] = ()
    sensitive_seed_sha256: Sha256Hex | None = None
    synthetic_scenario_id: NonEmptyText | None = None

    @model_validator(mode="after")
    def _route_fields_must_be_coherent(self) -> "GenerationProvenance":
        if self.generation_route == GenerationRoute.FULLY_SYNTHETIC:
            if self.uses_source_evidence or self.validated_evidence_spans:
                raise ValueError(
                    "fully_synthetic provenance cannot use source evidence"
                )
            if self.synthetic_scenario_id is None:
                raise ValueError(
                    "fully_synthetic provenance requires synthetic_scenario_id"
                )
        elif not self.uses_source_evidence or not self.validated_evidence_spans:
            raise ValueError(
                f"{self.generation_route.value} provenance requires source evidence"
            )
        elif self.synthetic_scenario_id is not None:
            raise ValueError(
                "synthetic_scenario_id is only allowed for fully_synthetic provenance"
            )
        if (
            self.generation_route == GenerationRoute.ANCHORED
            and self.sensitive_seed_sha256 is None
        ):
            raise ValueError("anchored provenance requires a sensitive seed hash")
        if (
            self.generation_route != GenerationRoute.ANCHORED
            and self.sensitive_seed_sha256 is not None
        ):
            raise ValueError(
                "sensitive seed hash is only allowed for anchored provenance"
            )
        return self


class AdministrativeStatusFinding(ContractModel):
    """근거 → 이유 → 판정 순서. ``LegalClassification``의 주석 참고."""

    evidence_spans: tuple[EvidenceSpan, ...] = Field(min_length=1)
    rationale: NonEmptyText
    status: AdminStatus

    def validate_evidence_against(self, block_text: Callable[[str], str]) -> None:
        for span in self.evidence_spans:
            text = block_text(span.block_id)
            if span.end > len(text):
                raise ValueError(
                    f"administrative status span for block {span.block_id!r} "
                    "ends outside the block"
                )
            if text[span.start : span.end] != span.quote:
                raise ValueError(
                    f"administrative status quote mismatch for block "
                    f"{span.block_id!r} at range {span.start}:{span.end}"
                )


class Pass2Assessment(LegalClassification):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    administrative_statuses: tuple[AdministrativeStatusFinding, ...] = ()

    @model_validator(mode="after")
    def _administrative_statuses_must_be_unique(self) -> "Pass2Assessment":
        statuses = [finding.status for finding in self.administrative_statuses]
        if len(statuses) != len(set(statuses)):
            raise ValueError("Pass 2 administrative statuses must be unique")
        return self

    @computed_field
    @property
    def effective_classification(self) -> CsoClassification:
        if self.classification == CsoClassification.C:
            return CsoClassification.C
        if (
            self.classification == CsoClassification.S
            or self.administrative_statuses
        ):
            return CsoClassification.S
        return CsoClassification.O

    def validate_against_document(self, document: GeneratedDocumentIR) -> None:
        self.validate_evidence_against(document.block_text)
        for finding in self.administrative_statuses:
            finding.validate_evidence_against(document.block_text)


class FailureStage(str, Enum):
    MANIFEST = "manifest"
    SELECTION = "selection"
    PASS1 = "pass1"
    PASS2 = "pass2"
    AUDIT = "audit"


class FailureCode(str, Enum):
    MANIFEST_INVALID = "manifest_invalid"
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    DOCUMENT_EMPTY = "document_empty"
    SELECTION_INVALID = "selection_invalid"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_CONFIGURATION_INVALID = "model_configuration_invalid"
    MODEL_REFUSAL = "model_refusal"
    MODEL_RESPONSE_EMPTY = "model_response_empty"
    STRUCTURED_OUTPUT_INVALID = "structured_output_invalid"
    SDK_ERROR = "sdk_error"
    EVIDENCE_INVALID = "evidence_invalid"
    ROUTE_INVALID = "route_invalid"
    JOURNAL_CORRUPT = "journal_corrupt"
    WRITER_CONFLICT = "writer_conflict"
    AUDIT_CONTRACT_INVALID = "audit_contract_invalid"
    AUDIT_FAILED = "audit_failed"


class StageFailure(ContractModel):
    stage: FailureStage
    code: FailureCode
    retryable: bool
    message: NonEmptyText


class JournalStage(str, Enum):
    PASS1_GENERATED = "pass1_generated"
    PASS2_GRADED = "pass2_graded"
    AUDITED = "audited"


class JournalStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TokenUsage(ContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _total_must_match(self) -> "TokenUsage":
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class CallReceipt(ContractModel):
    stage: FailureStage
    model_id: NonEmptyText
    response_id: NonEmptyText
    request_id: NonEmptyText | None = None
    token_usage: TokenUsage | None = None


class GradeComparison(ContractModel):
    document_type_match: bool
    classification_match: bool
    clause_match: bool
    subclause_match: bool
    administrative_status_match: bool = True

    @computed_field
    @property
    def requires_review(self) -> bool:
        return not all(
            (
                self.document_type_match,
                self.classification_match,
                self.clause_match,
                self.subclause_match,
                self.administrative_status_match,
            )
        )


class DocumentPipelineResult(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    source_document_id: NonEmptyText
    pass1_result: Pass1Result | None = None
    pass2_assessment: Pass2Assessment | None = None
    pass1_receipt: CallReceipt | None = None
    pass2_receipt: CallReceipt | None = None
    generation_provenance: GenerationProvenance | None = None
    comparison: GradeComparison | None = None
    failure: StageFailure | None = None

    @model_validator(mode="after")
    def _partial_result_must_be_coherent(self) -> "DocumentPipelineResult":
        if self.pass2_assessment is not None and self.pass1_result is None:
            raise ValueError("Pass 2 assessment requires Pass 1 result")
        if self.pass1_receipt is not None and self.pass1_result is None:
            raise ValueError("Pass 1 receipt requires Pass 1 result")
        if self.generation_provenance is not None and self.pass1_result is None:
            raise ValueError("generation provenance requires Pass 1 result")
        if self.pass2_receipt is not None and self.pass2_assessment is None:
            raise ValueError("Pass 2 receipt requires Pass 2 assessment")
        if self.comparison is not None:
            if (
                self.pass1_result is None
                or self.pass2_assessment is None
                or self.failure is not None
            ):
                raise ValueError("comparison requires a successful two-pass result")
        if self.failure is None and (
            self.pass1_result is None
            or self.pass2_assessment is None
            or self.pass1_receipt is None
            or self.pass2_receipt is None
            or self.comparison is None
        ):
            raise ValueError("successful pipeline result requires both passes and receipts")
        return self

    @computed_field
    @property
    def succeeded(self) -> bool:
        return self.failure is None


class Pass1StageArtifact(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    pass1_result: Pass1Result
    receipt: CallReceipt
    generation_provenance: GenerationProvenance | None = None

    @model_validator(mode="after")
    def _receipt_must_be_for_pass1(self) -> "Pass1StageArtifact":
        if self.receipt.stage != FailureStage.PASS1:
            raise ValueError("Pass 1 artifact requires a Pass 1 receipt")
        if self.generation_provenance is not None:
            provenance = self.generation_provenance
            if (
                provenance.generation_route
                != self.pass1_result.generation_route
                or provenance.final_target
                != self.pass1_result.generation_target
            ):
                raise ValueError(
                    "Pass 1 artifact provenance must match its Pass 1 result"
                )
        return self


class Pass2StageArtifact(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    pass2_assessment: Pass2Assessment
    receipt: CallReceipt
    comparison: GradeComparison

    @model_validator(mode="after")
    def _receipt_must_be_for_pass2(self) -> "Pass2StageArtifact":
        if self.receipt.stage != FailureStage.PASS2:
            raise ValueError("Pass 2 artifact requires a Pass 2 receipt")
        return self


class AuditStageArtifact(ContractModel):
    """정식 audit 산출물을 가리키는 작고 비민감한 journal artifact."""

    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    audit_artifact_path: NonEmptyText
    audit_artifact_sha256: Sha256Hex


class JournalRecord(ContractModel):
    # append-only 상태 전이:
    # pass1_generated -> pass2_graded -> audited
    # 실패 record는 마지막 성공 artifact를 지우지 않으며, resume은 그 다음
    # stage부터 시작한다.
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    run_id: NonEmptyText
    sequence: int = Field(ge=1)
    source_document_id: NonEmptyText
    stage: JournalStage
    status: JournalStatus
    recorded_at: datetime
    source_sha256: Sha256Hex
    selection_sha256: Sha256Hex
    prompt_bundle_sha256: Sha256Hex | None = None
    model_id: NonEmptyText | None = None
    artifact_sha256: Sha256Hex | None = None
    artifact_path: NonEmptyText | None = None
    upstream_artifact_sha256: Sha256Hex | None = None
    stage_config_sha256: Sha256Hex | None = None
    token_usage: TokenUsage | None = None
    failure: StageFailure | None = None

    @model_validator(mode="after")
    def _status_payload_must_match(self) -> "JournalRecord":
        if self.status == JournalStatus.SUCCEEDED:
            if (
                self.failure is not None
                or self.artifact_sha256 is None
                or self.artifact_path is None
            ):
                raise ValueError(
                    "successful journal record requires artifact path/hash and no failure"
                )
        else:
            if self.failure is None:
                raise ValueError("failed journal record requires StageFailure")
            expected_failure_stage = {
                JournalStage.PASS1_GENERATED: FailureStage.PASS1,
                JournalStage.PASS2_GRADED: FailureStage.PASS2,
                JournalStage.AUDITED: FailureStage.AUDIT,
            }[self.stage]
            if self.failure.stage != expected_failure_stage:
                raise ValueError(
                    f"journal stage {self.stage.value} requires failure stage "
                    f"{expected_failure_stage.value}"
                )
        if self.stage in {
            JournalStage.PASS1_GENERATED,
            JournalStage.PASS2_GRADED,
        } and (self.prompt_bundle_sha256 is None or self.model_id is None):
            raise ValueError(
                f"{self.stage.value} journal record requires prompt hash and model ID"
            )
        if (
            self.stage == JournalStage.PASS1_GENERATED
            and self.upstream_artifact_sha256 is not None
        ):
            raise ValueError("Pass 1 journal record cannot have an upstream artifact")
        if (
            self.stage in {JournalStage.PASS2_GRADED, JournalStage.AUDITED}
            and self.upstream_artifact_sha256 is None
        ):
            raise ValueError(
                f"{self.stage.value} journal record requires an upstream artifact"
            )
        if self.stage == JournalStage.AUDITED:
            if self.stage_config_sha256 is None:
                raise ValueError("audited journal record requires audit config hash")
            if self.model_id is not None:
                raise ValueError("deterministic audit journal record cannot have model ID")
        return self


class SecurityMode(str, Enum):
    PUBLIC_ONLY = "public_only"
    REDACTED = "redacted"
    EXTERNAL_UNREDACTED_APPROVED = "external_unredacted_approved"


class RunManifest(ContractModel):
    contract_version: Literal["1.0.0"] = CONTRACT_SCHEMA_VERSION
    taxonomy_version: Literal["source-generation-taxonomy-v2"] = TAXONOMY_VERSION
    run_id: NonEmptyText
    created_at: datetime
    generator_model: NonEmptyText
    grader_model: NonEmptyText
    relevance_model: NonEmptyText | None = None
    prompt_bundle_sha256: Sha256Hex
    selection_config_sha256: Sha256Hex
    security_mode: SecurityMode
    source_document_ids: tuple[NonEmptyText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _models_and_documents_must_be_valid(self) -> "RunManifest":
        if self.generator_model == self.grader_model:
            raise ValueError("generator_model and grader_model must be different")
        if len(self.source_document_ids) != len(set(self.source_document_ids)):
            raise ValueError("source_document_ids must be unique")
        return self
