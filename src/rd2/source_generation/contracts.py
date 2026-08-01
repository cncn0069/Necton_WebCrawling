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
    DocumentForm,
    SubclauseKey,
    clause_of_subclause,
    expected_classification,
    subclause_belongs_to_clause,
)

CONTRACT_SCHEMA_VERSION = "2.2.0"

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


def condense_whitespace(value: str) -> str:
    """공백을 모두 제거한 비교용 형태. 전각 공백(``\\u3000``)도 함께 제거된다."""

    return "".join(value.split())


def _condense_with_offsets(value: str) -> tuple[str, tuple[int, ...]]:
    """공백을 제거한 문자열과, 각 글자의 원본 인덱스를 함께 돌려준다.

    원본 인덱스를 보존하므로 공백을 무시해 찾은 위치를 **원문 좌표로** 되돌릴
    수 있다. 관대해진 것은 비교 방식이고 반환하는 위치는 여전히 원문 기준이다.
    """

    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(value):
        if not char.isspace():
            chars.append(char)
            offsets.append(index)
    return "".join(chars), tuple(offsets)


def _truncate(value: str, limit: int = 80) -> str:
    """오류 메시지에 넣을 인용문. 길면 앞부분만 남긴다."""

    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


def _prefix_hint(needle: str, haystack: str) -> str:
    """인용문이 어디까지 맞았는지 알려주는 한 마디.

    앞부분은 맞는데 뒤가 안 맞으면 block 경계를 넘어 인용했거나 뒤를 지어낸
    것이고, 앞부분조차 없으면 아예 다른 block을 가리킨 것이다. 이 둘은
    고치는 방법이 다르므로 오류에서 구분되어야 한다.
    """

    for length in (24, 16, 10):
        if len(needle) <= length:
            continue
        if needle[:length] in haystack:
            return (
                f" (앞 {length}자는 이 block에 있다 — 그 뒤가 어긋난다. "
                "block 경계를 넘겼거나 뒷부분을 바꿔 썼을 수 있다)"
            )
    return " (앞부분도 이 block에 없다 — 다른 block이거나 지어낸 문장이다)"


class EvidenceSpan(ContractModel):
    """모델이 **무엇을** 인용했는지만 담는다. **어디인지**는 코드가 찾는다.

    이전 계약은 ``start``/``end`` 문자 오프셋을 모델에게 요구했다. 실측 결과
    모델은 인용문 자체는 정확히 고르면서 오프셋은 거의 항상 틀렸다 — 3글자
    ``"요약문"``에 ``end=9``(UTF-8 바이트 수)를 반환하는 식이다. LLM은 글자를
    셀 수 없고 한글 멀티바이트에서 특히 그렇다.

    오프셋은 어차피 코드가 ``locate_in``으로 다시 계산했고 그 값을 읽는
    downstream도 없었다. 그래서 모델에게 묻지 않는다 — 못 하는 일을 시켜
    출력 토큰을 쓰고 틀릴 기회만 주는 계약이었다.

    같은 이유로 **공백의 완전 일치도 요구하지 않는다.** 실측에서 classifier가
    PDF 표를 인용할 때 글자는 모두 맞히면서 칸 사이 공백 개수만 달라
    ``str.find``에 걸리지 않았다. 표에서 뽑은 불규칙한 공백을 한 칸도 틀리지
    않게 재현하라는 것은 글자 수를 세라는 요구와 같은 부류다.

    근거가 실재해야 한다는 보안 속성은 그대로다 — 글자는 빠짐없이 같은 순서로
    있어야 하고, 하나라도 다르거나 요약·바꿔쓰기가 있으면 여전히 실패한다.
    풀어준 것은 공백뿐이다.
    """

    block_id: NonEmptyText
    quote: NonEmptyText

    def locate_in(self, text: str) -> int:
        """block text에서 인용문의 시작 위치를 원문 좌표로 찾는다.

        공백은 무시하고 비교한다(``EvidenceSpan`` 참고). 같은 인용문이 두 번
        이상 나오면 임의의 occurrence를 고르지 않고 실패시킨다 — 어느 쪽을
        가리키는지 모르는 근거는 근거가 아니다. 모델은 더 긴 고유 인용문을
        반환해야 한다.
        """

        needle = condense_whitespace(self.quote)
        if not needle:
            raise ValueError(
                f"evidence quote for block {self.block_id!r} has no visible characters"
            )
        haystack, offsets = _condense_with_offsets(text)
        start = haystack.find(needle)
        if start < 0:
            # 실측(2026-08-01): 오류가 block ID만 말해서 모델이 무엇을 인용했는지
            # 알 수 없었다. 문장을 지어낸 것인지, block 경계를 넘어 인용한 것인지,
            # 다른 block을 가리킨 것인지 구분이 안 돼 원인을 추측만 했다.
            # 인용문과, 앞부분이라도 걸리는 지점을 함께 남긴다.
            raise ValueError(
                f"evidence quote not found in block {self.block_id!r}: "
                f"{_truncate(self.quote)!r}"
                f"{_prefix_hint(needle, haystack)}"
            )
        if haystack.find(needle, start + 1) >= 0:
            raise ValueError(
                f"evidence quote is ambiguous in block {self.block_id!r}; "
                f"return a longer unique quote: {_truncate(self.quote)!r}"
            )
        return offsets[start]


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


DRAFT_BLANK_HEADER_KEYS: frozenset[str] = frozenset({"문서번호", "시행일자"})


class KeyValueEntry(ContractModel):
    key: NonEmptyText
    value: str

    @model_validator(mode="after")
    def _blank_value_is_only_for_draft_header_fields(self) -> "KeyValueEntry":
        """공란은 초안 표제부의 문서번호·시행일자에서만 표현한다.

        ``KeyValueEntry`` 자체는 생성 목표를 알 수 없으므로 두 표제부 키의
        공란만 구조적으로 표현 가능하게 둔다. 실제로 초안인지, 반대로 확정
        문서인데 공란이 남았는지는 target-aware ``check_document_form``이
        판정한다. 그 밖의 key-value 값은 종전처럼 비어 있을 수 없다.
        """

        if not self.value and self.key not in DRAFT_BLANK_HEADER_KEYS:
            raise ValueError(
                "blank key-value is only allowed for draft document number/date"
            )
        return self


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
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    title: NonEmptyText
    blocks: tuple[DocumentBlock, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _block_ids_must_be_unique(self) -> "GeneratedDocumentIR":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("generated document block IDs must be unique")
        return self

    @model_validator(mode="after")
    def _blank_key_values_must_be_in_the_header(self) -> "GeneratedDocumentIR":
        """공란 표제부 값을 일반 본문 key-value로 오용하지 못하게 한다."""

        for index, block in enumerate(self.blocks):
            if block.kind != "key_value":
                continue
            if any(not entry.value for entry in block.entries) and index != 0:
                raise ValueError(
                    "blank draft header values are only allowed in the first block"
                )
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
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
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
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
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
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    selected_block_ids: tuple[NonEmptyText, ...] = Field(min_length=1)
    rationale: NonEmptyText

    @model_validator(mode="after")
    def _selected_block_ids_must_be_unique(self) -> "RelevanceSelectionResponse":
        if len(self.selected_block_ids) != len(set(self.selected_block_ids)):
            raise ValueError("selected relevance block IDs must be unique")
        return self


class DocumentSelection(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
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
    생성하므로 **필드 순서가 곧 추론 순서**다. 그래서 근거를 판정보다 앞에
    두는 편이 원칙적으로 낫다 — 판정이 먼저 나오면 근거가 사후 정당화가 된다.

    **그러나 이 모델에서는 그 원칙을 적용하지 않는다.** 실측으로 두 번 시도해
    두 번 다 실패했다.

    1. ``evidence_spans``를 ``classification`` 앞에 두자 모델이 span을 먼저
       뱉고 나중에 그 조합을 금지하는 값(O + clause)을 골라 계약 위반.
    2. ``classification``만 앞으로 빼고 ``clause_no``를 근거 뒤에 남기자
       둘 사이가 멀어져 매핑이 표류했다 — "clause 5 does not map to
       classification C".

    ``classification``·``clause_no``·``subclause_key``는 서로를 제약하는
    **한 덩어리**다(C=제1~4호, S=제5~8호, O=둘 다 null, subclause는 clause
    소속). 제약으로 묶인 필드를 떼어놓으면 모델은 앞서 emit한 값을 잊고
    모순을 만든다. 그래서 덩어리를 붙여 앞에 두고 근거를 뒤에 둔다.

    교차 제약이 없는 곳(``AdministrativeStatusFinding``)에서는 근거를 앞에
    두는 원칙을 그대로 유지한다.

    순서를 바꿔도 JSON 구조는 동일하므로 ``CONTRACT_SCHEMA_VERSION``은 올리지
    않는다. 바뀐 것은 산출물의 shape이 아니라 생성 방식이므로
    ``PROMPT_BUNDLE_VERSION``으로 추적하고 journal을 무효화한다.
    """

    document_form: DocumentForm
    other_document_form: NonEmptyText | None = None
    classification: CsoClassification
    clause_no: ClauseNumber | None = None
    subclause_key: SubclauseKey | None = None
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    rationale: NonEmptyText

    @model_validator(mode="after")
    def _classification_must_be_coherent(self) -> "LegalClassification":
        if self.document_form == DocumentForm.OTHER:
            if self.other_document_form is None:
                raise ValueError("other_document_form is required for document_form=other")
        elif self.other_document_form is not None:
            raise ValueError("other_document_form is only allowed for document_form=other")

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
            span.locate_in(block_text(span.block_id))


def document_form_matches(
    left: LegalClassification,
    right: LegalClassification,
) -> bool:
    """문서형식이 같은가. ``other``면 자유텍스트까지 같아야 한다.

    **두 곳이 각자 계산하던 것을 하나로 합친 자리다.**
    ``pipeline._compare_consistency``는 ``other``일 때
    ``other_document_form``까지 비교했고, ``audit_bridge``의
    ``ClassificationAuditArtifact``는 같은 값을 enum 동등성만으로 다시 계산했다.
    양쪽이 ``other``인데 자유텍스트가 다르면 파이프라인은 불일치, 감사는
    일치로 봤고 — 감사 아티팩트는 그 둘이 어긋나면
    ``comparison does not match source/target/validation labels``로 거부하므로
    **그 건이 감사 번들에서 통째로 빠졌다.**

    같은 판정을 두 곳에서 재현하는 한 또 갈라진다. 그래서 계약 쪽에 한 번만
    둔다 — ``expected_classification``·``subclause_belongs_to_clause``와 같은
    자리다.
    """

    if left.document_form != right.document_form:
        return False
    if left.document_form is DocumentForm.OTHER:
        return left.other_document_form == right.other_document_form
    return True


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
    """``evidence_level``이 span 허용 여부를 결정하는 gate라 근거보다 앞에 온다.

    ``no_usable_public_source``는 span을 **금지**하고 나머지 level은 span을
    **요구**한다. level을 뒤에 두면 모델이 span을 먼저 뱉고 나중에
    ``no_usable_public_source``를 골라 스스로 모순되는 응답을 만든다(실측 확인).
    나머지 순서는 ``LegalClassification``의 주석 참고.
    """

    assessment_scope: AssessmentScope
    evidence_level: SourceEvidenceLevel
    evidence_spans: tuple[EvidenceSpan, ...] = ()
    rationale: NonEmptyText
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
            span.locate_in(block_text(span.block_id))


class SourceActorRole(str, Enum):
    PETITIONER = "petitioner"
    REPORTER = "reporter"
    APPLICANT = "applicant"
    JOB_APPLICANT = "job_applicant"
    BENEFICIARY = "beneficiary"
    EMPLOYEE = "employee"
    INVESTIGATION_SUBJECT = "investigation_subject"
    CASE_SUBJECT = "case_subject"
    WITNESS = "witness"
    DISPUTE_PARTY = "dispute_party"
    PUBLIC_OFFICIAL = "public_official"
    CORPORATION = "corporation"
    OTHER_EXTERNAL_PERSON = "other_external_person"
    NONE = "none"


class SourceSlotKind(str, Enum):
    PARAGRAPH = "paragraph"
    TABLE_COLUMN = "table_column"
    KEY_VALUE = "key_value"
    ATTACHMENT = "attachment"


class SourceSlot(ContractModel):
    """생성기가 원문 구조를 보존할 때 재사용할 수 있는 의미상 자리."""

    name: NonEmptyText
    kind: SourceSlotKind
    evidence_span: EvidenceSpan


class SourceAssessment(ContractModel):
    """유형 판별기 LLM의 유일한 출력.

    생성 경로·목표·본문은 포함하지 않는다. 이 계약을 먼저 고정한 뒤
    결정론적 계획기가 ``GenerationPlan``을 만든다.
    """

    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    source_classification: SourceClassification
    source_suitability: SourceSuitability
    business_context: NonEmptyText
    subject_roles: tuple[SourceActorRole, ...] = Field(min_length=1)
    available_slots: tuple[SourceSlot, ...] = Field(min_length=1)
    #: 이 원문에 **가장 가까운** 세부유형 하나. 계획기가 요청 목표를 대체할 때
    #: 쓰는 값이다.
    #:
    #: 이전에는 ``compatible_subclauses[0]``이 그 역할이었다. 두 가지가 문제였다 —
    #: 순서에 의미가 있다는 것을 계약이 검증하지 않아 프롬프트 지시에만 의존했고,
    #: 목록이 비어도(``= ()``) 유효해서 판별기가 "결정하지 않음"을 낼 수 있었다.
    #: 실측 10건에서 3건이 빈 목록을 냈고 그 3건은 계획 단계에서 폐기됐다.
    #: 필수 필드로 두면 그 상태가 계약 단계에서 불가능해진다.
    primary_subclause: SubclauseKey
    #: 왜 그것이 가장 가까운지. 순위 판단의 근거가 어디에도 기록되지 않아
    #: 사후 검증이 불가능했던 것을 남기려는 필드다.
    primary_rationale: NonEmptyText
    #: ``primary_subclause`` 외의 후보. 비어 있어도 된다.
    compatible_subclauses: tuple[SubclauseKey, ...] = ()

    @property
    def candidate_subclauses(self) -> tuple[SubclauseKey, ...]:
        """요청 목표가 이 원문과 호환되는지 볼 때 쓰는 전체 후보 집합."""

        return (self.primary_subclause, *self.compatible_subclauses)

    @model_validator(mode="after")
    def _facts_must_be_unique_and_coherent(self) -> "SourceAssessment":
        if self.source_classification.classification == CsoClassification.C:
            raise ValueError(
                "source assessment supports only S/O for clauses 5-8"
            )
        if len(self.subject_roles) != len(set(self.subject_roles)):
            raise ValueError("source subject roles must be unique")
        slot_keys = [(slot.name, slot.kind) for slot in self.available_slots]
        if len(slot_keys) != len(set(slot_keys)):
            raise ValueError("source slots must be unique by name and kind")
        if self.primary_subclause in self.compatible_subclauses:
            raise ValueError(
                "primary subclause must not repeat in compatible subclauses"
            )
        if len(self.compatible_subclauses) != len(set(self.compatible_subclauses)):
            raise ValueError("compatible subclauses must be unique")
        for subclause in self.candidate_subclauses:
            clause = next(
                (
                    candidate
                    for candidate in ClauseNumber
                    if subclause_belongs_to_clause(candidate, subclause)
                ),
                None,
            )
            if clause is None or expected_classification(clause) != CsoClassification.S:
                raise ValueError(
                    "source assessment only supports subclauses in clauses 5-8"
                )
        return self

    def validate_evidence_against(self, block_text: Callable[[str], str]) -> None:
        self.source_classification.validate_evidence_against(block_text)
        self.source_suitability.validate_evidence_against(block_text)
        for slot in self.available_slots:
            slot.evidence_span.locate_in(block_text(slot.evidence_span.block_id))


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


PLANNER_POLICY_VERSION = "source-generation-planner-v3"


class GenerationPlan(ContractModel):
    """판별 결과와 요청 target을 결합해 코드가 만드는 잠긴 생성 계획."""

    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    requested_target: GenerationTarget
    final_target: GenerationTarget
    generation_route: GenerationRoute
    source_assessment_sha256: Sha256Hex
    source_sha256: Sha256Hex
    selection_sha256: Sha256Hex
    planner_policy_version: Literal["source-generation-planner-v3"] = (
        PLANNER_POLICY_VERSION
    )
    planner_policy_sha256: Sha256Hex

    def validate_against(self, assessment: SourceAssessment) -> None:
        source = assessment.source_classification
        target = self.final_target
        suitability = assessment.source_suitability
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


class RepairCode(str, Enum):
    FORM_MISMATCH = "form_mismatch"
    CLASSIFICATION_MISMATCH = "classification_mismatch"
    CLAUSE_MISMATCH = "clause_mismatch"
    SUBCLAUSE_MISMATCH = "subclause_mismatch"
    MASK_REMAINS = "mask_remains"
    DIRECT_VALUE_MISSING = "direct_value_missing"
    ROLE_INCOMPATIBLE = "role_incompatible"
    EVIDENCE_INVALID = "evidence_invalid"


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


class GenerationArtifact(ContractModel):
    """한 번의 생성 시도와 그 lineage를 보존하는 산출물."""

    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    plan_sha256: Sha256Hex
    generated_document: GeneratedDocumentIR
    attempt_index: int = Field(ge=1)
    parent_generation_sha256: Sha256Hex | None = None
    repair_codes: tuple[RepairCode, ...] = ()
    provenance: GenerationProvenance

    @model_validator(mode="after")
    def _attempt_lineage_must_be_coherent(self) -> "GenerationArtifact":
        if len(self.repair_codes) != len(set(self.repair_codes)):
            raise ValueError("generation repair codes must be unique")
        if self.attempt_index == 1:
            if self.parent_generation_sha256 is not None or self.repair_codes:
                raise ValueError(
                    "first generation attempt cannot have a parent or repair codes"
                )
        elif self.parent_generation_sha256 is None or not self.repair_codes:
            raise ValueError(
                "retry generation attempts require a parent hash and repair codes"
            )
        return self


def effective_classification(
    legal: CsoClassification,
    administrative_statuses: tuple[AdminStatus, ...],
) -> CsoClassification:
    """법적 분류와 **선언된** 행정상태를 합친 최종 민감도.

    행정상태는 blind validator가 본문에서 찾아내는 대상이 아니라 생성계획이 못 박는
    메타데이터다. PDF 렌더러가 결재란을 강제로 그려 그 상태를 문서에
    구성해 넣으므로, 라벨은 판정이 아니라 **구성으로 보장된다.** 결정론적
    코드가 만든 사실을 LLM에게 다시 확인시키는 것은 검증이 아니라 잡음이다.

    실측이 이를 뒷받침한다 — 기존 validator의 행정상태 탐지는 9건 중 4건만 맞았고,
    상태를 본문 산문으로 서술하게 만든 탓에 실제 공문에 없는 문장
    ("최종 결재는 아직 이루어지지 않았습니다")이 생성물에 들어갔다.
    """

    if legal == CsoClassification.C:
        return CsoClassification.C
    if legal == CsoClassification.S or administrative_statuses:
        return CsoClassification.S
    return CsoClassification.O


class NearMissNote(ContractModel):
    """O 판정 시 감찰관이 남기는 지적 — 무엇이 더 있었으면 S였는지.

    생성기가 어디서 미끄러지는지는 지금까지 자유 문장 ``rationale``에만 남아
    사람이 매번 읽어야 했고, 여러 건을 모아 패턴을 보기 어려웠다. 실측
    (2026-08-01)에서 반복된 실패가 그런 종류였다 — 항목명만 쓰고 값을 안 쓴다,
    자료를 요청만 한다, 진행 상태만 서술한다. 세부유형별로 모으면 어느 생성
    규칙을 고쳐야 하는지가 드러난다.

    **진단 전용이다.** 재생성 입력으로 되먹이지 않는다 — 채점자가 생성기에게
    답을 알려주는 경로가 되면 두 판정이 더 이상 독립이 아니게 된다.
    """

    #: 이 문서가 가장 근접했던 세부유형. 판정이 아니라 "굳이 고르자면"이다.
    subclause_key: SubclauseKey
    #: 그 세부유형이 성립하려면 본문에 더 있어야 할 것. 항목명이 아니라
    #: 무슨 값이 없는지를 적는다.
    missing: NonEmptyText
    #: 그 자리를 짚을 수 있으면 block ID. 없으면 null이다.
    block_id: NonEmptyText | None = None

    @field_validator("block_id", mode="before")
    @classmethod
    def _blank_block_id_is_none(cls, value: object) -> object:
        """빈 문자열을 null과 같이 본다.

        프롬프트는 "짚을 수 없으면 null"이라고 말하지만 모델은 빈 문자열을
        낸다. ``NonEmptyText``가 그걸 거부하면 **판정 전체가 버려진다** —
        gateway의 ``ValidationError`` 경로는 재시도가 없다
        (``pipeline.SdkStructuredGateway.parse``). 진단용 칸 하나가 멀쩡한
        O 판정을 죽이는 값은 치를 수 없다.
        """

        if isinstance(value, str) and not value.strip():
            return None
        return value


def _usable_near_miss(
    notes: object,
    classification: object,
) -> object:
    """판정을 죽이지 않고 쓸 수 없는 지적만 걸러 낸다.

    ``near_miss``는 진단 전용이므로(``NearMissNote``) 계약 위반으로 응답을
    버릴 권한이 없다. 버려야 할 것은 지적 하나이지 판정이 아니다. 걸러 내는
    경우는 둘이다.

    1. S 판정에 붙은 지적. "S다"와 "무엇이 부족했다"는 함께 참일 수 없고,
       둘 중 판정이 본체다.
    2. 제5~8호 밖 세부유형. 검증기는 제5~8호 taxonomy만 보지만 스키마의
       ``SubclauseKey`` enum에는 제1~4호가 그대로 남아 있어(같은 이유로
       ``classification=C`` 금지를 프롬프트에 한 줄 남겼다) 고를 수 있다.
       그런 값이 섞이면 세부유형별 집계가 조용히 오염된다.
    """

    if not notes or not isinstance(notes, (list, tuple)):
        return notes
    try:
        if CsoClassification(classification) is not CsoClassification.O:
            return ()
    except ValueError:
        # 판정 값 자체가 이상하면 그건 이쪽이 아니라 분류 검증이 말할 몫이다.
        return notes

    usable = []
    for note in notes:
        if isinstance(note, NearMissNote):
            key: object = note.subclause_key
        elif isinstance(note, dict):
            key = note.get("subclause_key")
            missing = note.get("missing")
            if not isinstance(missing, str) or not missing.strip():
                continue
        else:
            usable.append(note)
            continue
        try:
            clause = clause_of_subclause(SubclauseKey(key))
        except ValueError:
            continue
        if expected_classification(clause) is CsoClassification.S:
            usable.append(note)
    return tuple(usable)


class ConsistencyAssessment(LegalClassification):
    """생성물의 **법적** 분류만 독립 판정한다.

    행정상태는 채점 대상이 아니다 — ``effective_classification()`` 참고.
    """

    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    #: O로 판정했을 때만 채운다. 기본값이 비어 있으므로 과거 산출물도 그대로
    #: 읽힌다 — 계약 버전을 올리지 않는 이유다.
    near_miss: tuple[NearMissNote, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _drop_unusable_near_miss(cls, data: object) -> object:
        """쓸 수 없는 지적은 **버리되 판정은 살린다**(``_usable_near_miss``)."""

        if not isinstance(data, dict) or "near_miss" not in data:
            return data
        usable = _usable_near_miss(data["near_miss"], data.get("classification"))
        if usable is data["near_miss"]:
            return data
        return {**data, "near_miss": usable}

    def validate_against_document(self, document: GeneratedDocumentIR) -> None:
        self.validate_evidence_against(document.block_text)


class SensitiveVerdict(str, Enum):
    ACCEPTED_S = "accepted_s"
    ASSESSED_O = "assessed_o"
    HARD_CASE_REVIEW = "hard_case_review"


class SensitivePipelineStatus(str, Enum):
    ACCEPTED_S = "accepted_s"
    HARD_CASE_REVIEW = "hard_case_review"
    EXCLUDED_AFTER_RETRY = "excluded_after_retry"
    PIPELINE_FAILED = "pipeline_failed"


class SensitiveSubjectRole(str, Enum):
    PETITIONER = "petitioner"
    APPLICANT = "applicant"
    JOB_APPLICANT = "job_applicant"
    BENEFICIARY = "beneficiary"
    EMPLOYEE = "employee"
    INVESTIGATION_SUBJECT = "investigation_subject"
    CASE_SUBJECT = "case_subject"
    OTHER_EXTERNAL_PERSON = "other_external_person"


class SensitiveAttributeKind(str, Enum):
    PHONE = "phone"
    EMAIL = "email"
    ADDRESS = "address"
    NATIONAL_ID = "national_id"
    FOREIGNER_ID = "foreigner_id"
    PASSPORT_ID = "passport_id"
    DRIVER_LICENSE_ID = "driver_license_id"
    ACCOUNT = "account"
    SALARY = "salary"
    HEALTH = "health"
    DISABILITY = "disability"
    WELFARE_CIRCUMSTANCE = "welfare_circumstance"
    APPLICATION_CIRCUMSTANCE = "application_circumstance"
    OTHER_PERSONAL_FACT = "other_personal_fact"
    BUSINESS_IDENTITY = "business_identity"
    BUSINESS_CONTACT = "business_contact"
    PERSONNEL_EVALUATION = "personnel_evaluation"
    DISCIPLINE = "discipline"


class IdentificationStrength(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    MASKED = "masked"


SensitiveClause6Subclause = Literal[
    SubclauseKey.PETITIONER_PII,
    SubclauseKey.PERSONNEL_PII,
    SubclauseKey.WELFARE_PII,
    SubclauseKey.SUBJECT_PII,
]


class SensitiveMonitorAssertion(ContractModel):
    """제6호 감시자가 찾은 하나의 의미상 주체-값 연결.

    모델에게 ``EvidenceSpan`` 세 개를 각각 만들게 하면 같은 ``block_id``를 세 번
    반복하면서 서로 일치시켜야 한다. 감시자 응답에서는 블록 ID를 한 번만 받고,
    저장용 ``SensitiveAssertion``은 파이프라인 코드가 조립한다.
    """

    block_id: NonEmptyText
    subject_role: SensitiveSubjectRole
    subject_quote: NonEmptyText
    attribute_kind: SensitiveAttributeKind
    value_quote: NonEmptyText
    link_quote: NonEmptyText
    identification_strength: IdentificationStrength


class SensitiveMonitorDecision(ContractModel):
    """LLM이 반환하는 제6호 감시자의 최소 의미 판정.

    ``classification``·``clause_no``·``evidence_spans``·``near_miss``는 verdict와
    assertion에서 기계적으로 결정할 수 있으므로 이 계약에 두지 않는다. 이 모델은
    의도적으로 교차 필드 validator도 갖지 않는다. 의미 조합의 검증과 정규화는
    모델 응답을 받은 뒤 파이프라인이 한 번만 수행한다.
    """

    document_form: DocumentForm
    other_document_form: NonEmptyText | None = None
    assertions: tuple[SensitiveMonitorAssertion, ...] = ()
    rationale: NonEmptyText
    verdict: SensitiveVerdict
    subclause_key: SensitiveClause6Subclause | None = None


class SensitiveAssertion(ContractModel):
    """S 판정을 성립시키는 주체-속성-값 연결을 구조화한 내부 근거."""

    subject_role: SensitiveSubjectRole
    subject_span: EvidenceSpan
    attribute_kind: SensitiveAttributeKind
    value_span: EvidenceSpan
    link_span: EvidenceSpan
    identification_strength: IdentificationStrength

    def validate_against_document(self, document: GeneratedDocumentIR) -> None:
        for span in (self.subject_span, self.value_span, self.link_span):
            span.locate_in(document.block_text(span.block_id))
        block_ids = {
            self.subject_span.block_id,
            self.value_span.block_id,
            self.link_span.block_id,
        }
        if len(block_ids) != 1:
            raise ValueError(
                "sensitive assertion subject/value/link spans must use one block"
            )
        # 포함 검사도 공백을 무시한다 — ``EvidenceSpan``과 같은 이유다. link
        # 인용문의 칸 사이 공백만 달라서 탈락하면 판정이 공백 운에 좌우된다.
        link = condense_whitespace(self.link_span.quote)
        if condense_whitespace(self.subject_span.quote) not in link:
            raise ValueError("sensitive assertion link must contain the subject quote")
        if condense_whitespace(self.value_span.quote) not in link:
            raise ValueError("sensitive assertion link must contain the value quote")


class SensitiveConsistencyAssessment(ConsistencyAssessment):
    """원문 참고 S 생성 경로의 blind S/O 관계 판정."""

    sensitivity_verdict: SensitiveVerdict
    assertions: tuple[SensitiveAssertion, ...] = ()

    @model_validator(mode="after")
    def _verdict_must_match_classification(
        self,
    ) -> "SensitiveConsistencyAssessment":
        if self.classification == CsoClassification.C:
            raise ValueError(
                "source-sensitive consistency validation can only classify S or O"
            )
        if self.sensitivity_verdict == SensitiveVerdict.ACCEPTED_S:
            if self.classification != CsoClassification.S:
                raise ValueError("accepted_s requires classification S")
            if self.clause_no != ClauseNumber.CLAUSE_6:
                raise ValueError("accepted_s requires clause 6")
            if not self.assertions:
                raise ValueError("accepted_s requires at least one sensitive assertion")
            if any(
                item.identification_strength != IdentificationStrength.DIRECT
                for item in self.assertions
            ):
                raise ValueError("accepted_s assertions must be directly identifying")
            evidence = {
                (span.block_id, span.quote) for span in self.evidence_spans
            }
            for assertion in self.assertions:
                link = (assertion.link_span.block_id, assertion.link_span.quote)
                if link not in evidence:
                    raise ValueError(
                        "accepted_s evidence_spans must include every assertion link"
                    )
        elif self.sensitivity_verdict == SensitiveVerdict.ASSESSED_O:
            if self.classification != CsoClassification.O:
                raise ValueError("assessed_o requires classification O")
            if self.assertions:
                raise ValueError("assessed_o cannot include sensitive assertions")
        else:
            if self.classification != CsoClassification.O:
                raise ValueError("hard_case_review uses provisional classification O")
            if not self.assertions:
                raise ValueError("hard_case_review requires at least one assertion")
            if all(
                item.identification_strength == IdentificationStrength.DIRECT
                for item in self.assertions
            ):
                raise ValueError(
                    "hard_case_review requires a masked or indirect assertion"
                )
        return self

    def validate_against_document(self, document: GeneratedDocumentIR) -> None:
        super().validate_against_document(document)
        for assertion in self.assertions:
            assertion.validate_against_document(document)


class FailureStage(str, Enum):
    MANIFEST = "manifest"
    SELECTION = "selection"
    CLASSIFICATION = "classification"
    PLANNING = "planning"
    GENERATION = "generation"
    VALIDATION = "validation"
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
    SENSITIVE_ASSERTION_INVALID = "sensitive_assertion_invalid"
    ROUTE_INVALID = "route_invalid"
    SOURCE_INCOMPATIBLE = "source_incompatible"
    CONTRACT_VERSION_CHANGED = "contract_version_changed"
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
    CLASSIFIED = "classified"
    PLANNED = "planned"
    GENERATED = "generated"
    VALIDATED = "validated"
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


class ConsistencyComparison(ContractModel):
    document_form_match: bool
    classification_match: bool
    clause_match: bool
    subclause_match: bool
    subject_role_match: bool = True

    @computed_field
    @property
    def requires_review(self) -> bool:
        return not all(
            (
                self.document_form_match,
                self.classification_match,
                self.clause_match,
                self.subclause_match,
                self.subject_role_match,
            )
        )


class DocumentPipelineResult(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    source_document_id: NonEmptyText
    source_assessment: SourceAssessment | None = None
    generation_plan: GenerationPlan | None = None
    generation_artifact: GenerationArtifact | None = None
    consistency_assessment: (
        SensitiveConsistencyAssessment | ConsistencyAssessment | None
    ) = None
    classification_receipt: CallReceipt | None = None
    generation_receipt: CallReceipt | None = None
    validation_receipt: CallReceipt | None = None
    comparison: ConsistencyComparison | None = None
    failure: StageFailure | None = None

    @model_validator(mode="after")
    def _partial_result_must_be_coherent(self) -> "DocumentPipelineResult":
        if self.generation_plan is not None and self.source_assessment is None:
            raise ValueError("generation plan requires source assessment")
        if self.generation_artifact is not None and self.generation_plan is None:
            raise ValueError("generation artifact requires generation plan")
        if self.consistency_assessment is not None and self.generation_artifact is None:
            raise ValueError(
                "consistency assessment requires a generation artifact"
            )
        if self.classification_receipt is not None and self.source_assessment is None:
            raise ValueError("classification receipt requires source assessment")
        if self.generation_receipt is not None and self.generation_artifact is None:
            raise ValueError("generation receipt requires generation artifact")
        if self.validation_receipt is not None and self.consistency_assessment is None:
            raise ValueError("validation receipt requires consistency assessment")
        if self.comparison is not None:
            if (
                self.generation_plan is None
                or self.consistency_assessment is None
                or self.failure is not None
            ):
                raise ValueError(
                    "comparison requires a successful consistency validation"
                )
        if self.failure is None and (
            self.source_assessment is None
            or self.generation_plan is None
            or self.generation_artifact is None
            or self.consistency_assessment is None
            or self.classification_receipt is None
            or self.validation_receipt is None
            or self.comparison is None
        ):
            raise ValueError(
                "successful pipeline result requires classification, generation, "
                "validation, and comparison"
            )
        if (
            self.failure is None
            and self.generation_plan is not None
            and self.generation_plan.generation_route
            != GenerationRoute.FULLY_SYNTHETIC
            and self.generation_receipt is None
        ):
            raise ValueError(
                "source-referenced generation requires a generation receipt"
            )
        return self

    @computed_field
    @property
    def succeeded(self) -> bool:
        return self.failure is None


class ClassificationStageArtifact(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    source_assessment: SourceAssessment
    receipt: CallReceipt

    @model_validator(mode="after")
    def _receipt_must_be_for_classification(
        self,
    ) -> "ClassificationStageArtifact":
        if self.receipt.stage != FailureStage.CLASSIFICATION:
            raise ValueError(
                "classification artifact requires a classification receipt"
            )
        return self


class PlanningStageArtifact(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    generation_plan: GenerationPlan


class GenerationStageArtifact(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    generation_artifact: GenerationArtifact
    receipt: CallReceipt | None = None

    @model_validator(mode="after")
    def _receipt_must_be_for_generation(self) -> "GenerationStageArtifact":
        if self.receipt is not None and self.receipt.stage != FailureStage.GENERATION:
            raise ValueError("generation artifact requires a generation receipt")
        route = self.generation_artifact.provenance.generation_route
        if route == GenerationRoute.FULLY_SYNTHETIC:
            if self.receipt is not None:
                raise ValueError(
                    "source-free fully synthetic generation cannot have an LLM receipt"
                )
        elif self.receipt is None:
            raise ValueError(
                "source-referenced generation requires a generation receipt"
            )
        return self


class ValidationStageArtifact(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    generated_document_sha256: Sha256Hex
    consistency_assessment: SensitiveConsistencyAssessment | ConsistencyAssessment
    receipt: CallReceipt
    comparison: ConsistencyComparison
    repair_codes: tuple[RepairCode, ...] = ()

    @model_validator(mode="after")
    def _receipt_must_be_for_validation(self) -> "ValidationStageArtifact":
        if self.receipt.stage != FailureStage.VALIDATION:
            raise ValueError("validation artifact requires a validation receipt")
        if len(self.repair_codes) != len(set(self.repair_codes)):
            raise ValueError("validation repair codes must be unique")
        return self


class AuditStageArtifact(ContractModel):
    """정식 audit 산출물을 가리키는 작고 비민감한 journal artifact."""

    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    audit_artifact_path: NonEmptyText
    audit_artifact_sha256: Sha256Hex


class JournalRecord(ContractModel):
    # append-only 상태 전이:
    # classified -> planned -> generated -> validated -> audited
    # 실패 record는 마지막 성공 artifact를 지우지 않으며, resume은 그 다음
    # stage부터 시작한다.
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    run_id: NonEmptyText
    sequence: int = Field(ge=1)
    source_document_id: NonEmptyText
    stage: JournalStage
    status: JournalStatus
    recorded_at: datetime
    source_sha256: Sha256Hex
    selection_sha256: Sha256Hex
    prompt_sha256: Sha256Hex | None = None
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
                JournalStage.CLASSIFIED: FailureStage.CLASSIFICATION,
                JournalStage.PLANNED: FailureStage.PLANNING,
                JournalStage.GENERATED: FailureStage.GENERATION,
                JournalStage.VALIDATED: FailureStage.VALIDATION,
                JournalStage.AUDITED: FailureStage.AUDIT,
            }[self.stage]
            if self.failure.stage != expected_failure_stage:
                raise ValueError(
                    f"journal stage {self.stage.value} requires failure stage "
                    f"{expected_failure_stage.value}"
                )
        if self.stage in {
            JournalStage.CLASSIFIED,
            JournalStage.VALIDATED,
        } and (self.prompt_sha256 is None or self.model_id is None):
            raise ValueError(
                f"{self.stage.value} journal record requires prompt hash and model ID"
            )
        if self.stage == JournalStage.GENERATED and (
            (self.prompt_sha256 is None) != (self.model_id is None)
        ):
            raise ValueError(
                "generated journal record requires both prompt hash and model ID, "
                "or neither for a source-free deterministic generator"
            )
        if (
            self.stage == JournalStage.CLASSIFIED
            and self.upstream_artifact_sha256 is not None
        ):
            raise ValueError(
                "classified journal record cannot have an upstream artifact"
            )
        if (
            self.stage
            in {
                JournalStage.PLANNED,
                JournalStage.GENERATED,
                JournalStage.VALIDATED,
                JournalStage.AUDITED,
            }
            and self.upstream_artifact_sha256 is None
        ):
            raise ValueError(
                f"{self.stage.value} journal record requires an upstream artifact"
            )
        if self.stage in {JournalStage.PLANNED, JournalStage.AUDITED}:
            if self.stage_config_sha256 is None:
                raise ValueError(
                    f"{self.stage.value} journal record requires a stage config hash"
                )
            if self.model_id is not None:
                raise ValueError(
                    f"deterministic {self.stage.value} journal record cannot have "
                    "a model ID"
                )
        return self


class SecurityMode(str, Enum):
    PUBLIC_ONLY = "public_only"
    REDACTED = "redacted"
    EXTERNAL_UNREDACTED_APPROVED = "external_unredacted_approved"


class RunManifest(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    taxonomy_version: Literal["source-generation-taxonomy-v3"] = TAXONOMY_VERSION
    run_id: NonEmptyText
    created_at: datetime
    classifier_model: NonEmptyText
    generator_model: NonEmptyText
    validator_model: NonEmptyText
    relevance_model: NonEmptyText | None = None
    classifier_prompt_sha256: Sha256Hex
    generator_prompt_sha256: Sha256Hex
    validator_prompt_sha256: Sha256Hex
    planner_policy_sha256: Sha256Hex
    selection_config_sha256: Sha256Hex
    security_mode: SecurityMode
    source_document_ids: tuple[NonEmptyText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _models_and_documents_must_be_valid(self) -> "RunManifest":
        if self.validator_model in {
            self.classifier_model,
            self.generator_model,
        }:
            raise ValueError(
                "validator_model must differ from classifier_model and generator_model"
            )
        if len(self.source_document_ids) != len(set(self.source_document_ids)):
            raise ValueError("source_document_ids must be unique")
        return self
