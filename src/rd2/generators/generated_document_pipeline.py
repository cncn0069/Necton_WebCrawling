"""구조화 생성 계약을 문서 유형별 Jinja2 템플릿 PDF로 변환한다.

데이터 흐름::

    pass1 JSON
        -> 계약/실패 검증
        -> blocks와 body_text 무결성 검증
        -> document_type별 템플릿 context
        -> Jinja2 + WeasyPrint
        -> PDF 텍스트 누락 검증 + manifest

``generated_document.blocks``가 내용의 기준이다. ``body_text``는 blocks를
평탄화한 값과 같은지 검증하는 폴백이며, 두 값이 다르면 렌더링하지 않는다.
``generated_document.agency_name``이 있으면 기관명을 그대로 보존한다.
공문 경로는 기관명이 없을 때 범용 공공기관 가상 풀을 사용하고,
연구보고서·보도자료 경로는 빈 기관명을 그대로 보존한다. 그 밖의 문서
메타데이터는 입력 계약에 없으면 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rd2.generators.official_document_rendering import (
    render_official_document_variations,
)
from rd2.generators.research_report_rendering import (
    render_research_report_variations,
)
from rd2.generators.press_release_rendering import (
    PRESS_RELEASE_MAX_PAGES,
    PRESS_RELEASE_WIDE_TABLE_MIN_COLUMNS,
    render_press_release_variations,
)
from rd2.generators.synthetic_approval_stamps import (
    StampProfile,
    StampShape,
    build_stamp_placement,
    generate_synthetic_approval_stamp,
)
from rd2.source_generation.classification_taxonomy import SemanticDocumentType

_CONTRACT_VERSION_RE = re.compile(r"^1\.\d+\.\d+$")
_CONTENT_CONTEXT_KEYS = frozenset(
    {
        "title",
        "intro",
        "sections",
        "details",
        "table",
        "attachments",
        "long_sections",
        "checklist_items",
        "summary_text",
        "source_agency_name",
        "source_agency_category",
        "signers",
        "approval_manifest",
        "administrative_events",
    }
)
_LIST_LABELS = tuple("가나다라마바사아자차카타파하")


class GeneratedDocumentPipelineError(ValueError):
    """생성 계약을 안전하게 렌더링할 수 없을 때 발생한다."""


class FailedGenerationPayloadError(GeneratedDocumentPipelineError):
    """상위 생성 단계가 실패한 레코드를 기본 정책으로 거부한다."""


class GeneratedDocumentContentMismatch(GeneratedDocumentPipelineError):
    """blocks와 body_text의 내용이 서로 다르다."""


class _ContractModel(BaseModel):
    # 1.x 계약의 호환 가능한 메타데이터 확장은 보존하되, kind별 필수 내용과
    # 표 형태는 아래 모델에서 계속 엄격하게 검증한다.
    model_config = ConfigDict(extra="allow")


def _non_empty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class ParagraphBlock(_ContractModel):
    kind: Literal["paragraph"]
    block_id: str
    text: str

    @field_validator("block_id", "text")
    @classmethod
    def _validate_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class KeyValueEntry(_ContractModel):
    key: str
    value: str

    @field_validator("key", "value")
    @classmethod
    def _validate_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class KeyValueBlock(_ContractModel):
    kind: Literal["key_value"]
    block_id: str
    entries: list[KeyValueEntry]

    @field_validator("block_id")
    @classmethod
    def _validate_block_id(cls, value: str) -> str:
        return _non_empty(value, "block_id")

    @field_validator("entries")
    @classmethod
    def _validate_entries(cls, value: list[KeyValueEntry]) -> list[KeyValueEntry]:
        if not value:
            raise ValueError("entries must not be empty")
        return value


class BulletListBlock(_ContractModel):
    kind: Literal["bullet_list"]
    block_id: str
    items: list[str]

    @field_validator("block_id")
    @classmethod
    def _validate_block_id(cls, value: str) -> str:
        return _non_empty(value, "block_id")

    @field_validator("items")
    @classmethod
    def _validate_items(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("items must not be empty")
        for item in value:
            _non_empty(item, "items")
        return value


class TableBlock(_ContractModel):
    kind: Literal["table"]
    block_id: str
    columns: list[str]
    rows: list[list[str]]

    @field_validator("block_id")
    @classmethod
    def _validate_block_id(cls, value: str) -> str:
        return _non_empty(value, "block_id")

    @field_validator("columns")
    @classmethod
    def _validate_columns(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("columns must not be empty")
        for column in value:
            _non_empty(column, "columns")
        return value

    @model_validator(mode="after")
    def _validate_row_widths(self) -> "TableBlock":
        width = len(self.columns)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    f"rows[{index}] has {len(row)} cells; expected {width}"
                )
        return self


class AttachmentReferenceBlock(_ContractModel):
    kind: Literal["attachment_reference"]
    block_id: str
    attachment_id: str
    label: str
    description: str | None = None

    @field_validator("block_id", "attachment_id", "label")
    @classmethod
    def _validate_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        return None if value is None else _non_empty(value, "description")

    def render_text(self) -> str:
        rendered = f"[첨부] {self.label} ({self.attachment_id})"
        if self.description:
            rendered = f"{rendered}: {self.description}"
        return rendered

    def display_text(self) -> str:
        rendered = f"{self.label} ({self.attachment_id})"
        if self.description:
            rendered = f"{rendered}: {self.description}"
        return rendered


GeneratedBlock = Annotated[
    ParagraphBlock
    | KeyValueBlock
    | BulletListBlock
    | TableBlock
    | AttachmentReferenceBlock,
    Field(discriminator="kind"),
]


class SyntheticStampContract(_ContractModel):
    mode: Literal["synthetic"]
    stamp_text: str
    seed: int | None = None
    profile: StampProfile | None = None
    shape: StampShape | None = None

    @field_validator("stamp_text")
    @classmethod
    def _validate_stamp_text(cls, value: str) -> str:
        value = _non_empty(value, "stamp_text")
        if len(re.sub(r"\s+", "", value)) > 16:
            raise ValueError(
                "stamp_text must contain at most 16 non-space characters"
            )
        return value


class ApprovalSlotContract(_ContractModel):
    role: str
    name: str | None = None
    status: Literal["pending", "approved", "rejected", "not_required"]
    approved_at: date | None = None
    stamp: SyntheticStampContract | None = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        return _non_empty(value, "role")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return None if value is None else _non_empty(value, "name")

    @model_validator(mode="after")
    def _validate_approval_state(self) -> "ApprovalSlotContract":
        if self.stamp is not None and self.status != "approved":
            raise ValueError("stamp is only allowed when status is approved")
        if self.approved_at is not None and self.status != "approved":
            raise ValueError(
                "approved_at is only allowed when status is approved"
            )
        return self


class ApprovalLineContract(_ContractModel):
    slots: list[ApprovalSlotContract]

    @field_validator("slots")
    @classmethod
    def _validate_slots(
        cls,
        value: list[ApprovalSlotContract],
    ) -> list[ApprovalSlotContract]:
        if not value:
            raise ValueError("approval_line.slots must not be empty")
        if len(value) > 5:
            raise ValueError("approval_line.slots supports at most 5 entries")
        return value


class AdministrativeEventContract(_ContractModel):
    type: str
    date: date
    text: str

    @field_validator("type", "text")
    @classmethod
    def _validate_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class DocumentMetadataContract(_ContractModel):
    approval_line: ApprovalLineContract | None = None
    administrative_events: list[AdministrativeEventContract] = Field(
        default_factory=list
    )


class GeneratedDocumentContract(_ContractModel):
    contract_version: str
    title: str
    agency_name: str | None = None
    document_metadata: DocumentMetadataContract | None = None
    blocks: list[GeneratedBlock] = Field(default_factory=list)
    body_text: str | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _non_empty(value, "title")

    @field_validator("agency_name")
    @classmethod
    def _validate_agency_name(cls, value: str | None) -> str | None:
        return None if value is None else _non_empty(value, "agency_name")

    @model_validator(mode="after")
    def _validate_unique_block_ids(self) -> "GeneratedDocumentContract":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id values must be unique")
        if not self.blocks and not (self.body_text and self.body_text.strip()):
            raise ValueError("blocks or body_text must contain document content")
        return self


class GenerationReceipt(BaseModel):
    model_config = ConfigDict(extra="allow")

    stage: str | None = None
    model_id: str | None = None
    response_id: str | None = None
    request_id: str | None = None


class GenerationFailure(BaseModel):
    model_config = ConfigDict(extra="allow")

    stage: str | None = None
    code: str | None = None
    retryable: bool | None = None
    message: str | None = None


class SourceClassificationContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_type: SemanticDocumentType

    @field_validator("document_type", mode="before")
    @classmethod
    def _validate_document_type(cls, value: object) -> object:
        if isinstance(value, str):
            return _non_empty(value, "document_type")
        return value


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_version: str
    source_classification: SourceClassificationContract | None = None
    generation_route: str | None = None
    generation_target: dict[str, Any] | None = None
    generated_document: GeneratedDocumentContract


class GenerationEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: GenerationResult
    receipt: GenerationReceipt | None = None
    provenance: Any = None
    failure: GenerationFailure | None = None

    @model_validator(mode="after")
    def _validate_matching_contract_versions(self) -> "GenerationEnvelope":
        result_version = self.result.contract_version
        document_version = self.result.generated_document.contract_version
        if result_version != document_version:
            raise ValueError(
                "result.contract_version and "
                "generated_document.contract_version must match"
            )
        if not _CONTRACT_VERSION_RE.fullmatch(result_version):
            raise ValueError(
                f"Unsupported contract_version {result_version!r}; expected 1.x.x"
            )
        return self


def _normalized_contract_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line
        if is_blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = is_blank
    return "\n".join(normalized)


def blocks_to_body_text(blocks: list[GeneratedBlock]) -> str:
    """구조화 blocks를 계약의 ``body_text`` 표현으로 평탄화한다."""

    chunks: list[str] = []
    for block in blocks:
        if isinstance(block, ParagraphBlock):
            chunks.append(block.text)
        elif isinstance(block, KeyValueBlock):
            chunks.append(
                "\n".join(f"{entry.key}: {entry.value}" for entry in block.entries)
            )
        elif isinstance(block, BulletListBlock):
            chunks.append("\n".join(f"- {item}" for item in block.items))
        elif isinstance(block, TableBlock):
            rows = ["\t".join(block.columns)]
            rows.extend("\t".join(row) for row in block.rows)
            chunks.append("\n".join(rows))
        elif isinstance(block, AttachmentReferenceBlock):
            chunks.append(block.render_text())
        else:
            raise TypeError(f"Unsupported generated block: {type(block)!r}")
    return "\n\n".join(chunks)


def _fallback_paragraph_blocks(body_text: str) -> list[ParagraphBlock]:
    paragraphs = [
        paragraph
        for paragraph in re.split(r"\n[ \t]*\n+", body_text.strip())
        if paragraph.strip()
    ]
    return [
        ParagraphBlock(
            kind="paragraph",
            block_id=f"fallback:{index}",
            text=paragraph,
        )
        for index, paragraph in enumerate(paragraphs, start=1)
    ]


def parse_generation_payload(
    payload: Mapping[str, Any],
    *,
    allow_failed: bool = False,
) -> GenerationEnvelope:
    """입력 계약을 검증하고 렌더링 가능한 envelope를 반환한다."""

    raw_failure = payload.get("failure")
    if raw_failure is not None and not allow_failed:
        code = raw_failure.get("code") if isinstance(raw_failure, Mapping) else None
        message = (
            raw_failure.get("message")
            if isinstance(raw_failure, Mapping)
            else None
        )
        details = ": ".join(part for part in (code, message) if part)
        raise FailedGenerationPayloadError(
            "Generation payload contains failure"
            + (f": {details}" if details else "")
        )

    envelope = GenerationEnvelope.model_validate(payload)
    document = envelope.result.generated_document
    if not document.blocks:
        fallback_blocks = _fallback_paragraph_blocks(document.body_text or "")
        document = document.model_copy(update={"blocks": fallback_blocks})
        result = envelope.result.model_copy(update={"generated_document": document})
        envelope = envelope.model_copy(update={"result": result})

    if document.body_text is not None:
        from_blocks = _normalized_contract_text(blocks_to_body_text(document.blocks))
        declared = _normalized_contract_text(document.body_text)
        if from_blocks != declared:
            raise GeneratedDocumentContentMismatch(
                "generated_document.body_text does not match "
                "generated_document.blocks"
            )
    return envelope


def source_text_atoms(
    document: GeneratedDocumentContract,
    *,
    include_administrative_event_dates: bool = False,
) -> tuple[str, ...]:
    """PDF에 빠짐없이 있어야 하는 원문 단위를 반환한다."""

    atoms: list[str] = [document.agency_name] if document.agency_name else []
    for block in document.blocks:
        if isinstance(block, ParagraphBlock):
            atoms.append(block.text)
        elif isinstance(block, KeyValueBlock):
            for entry in block.entries:
                atoms.extend((entry.key, entry.value))
        elif isinstance(block, BulletListBlock):
            atoms.extend(block.items)
        elif isinstance(block, TableBlock):
            atoms.extend(block.columns)
            for row in block.rows:
                atoms.extend(row)
        elif isinstance(block, AttachmentReferenceBlock):
            atoms.extend((block.attachment_id, block.label))
            if block.description:
                atoms.append(block.description)
        else:
            raise TypeError(f"Unsupported generated block: {type(block)!r}")
    metadata = document.document_metadata
    if metadata and metadata.approval_line:
        for slot in metadata.approval_line.slots:
            atoms.append(slot.role)
            if slot.name:
                atoms.append(slot.name)
            if slot.approved_at:
                atoms.append(slot.approved_at.isoformat())
    if metadata:
        for event in metadata.administrative_events:
            if include_administrative_event_dates:
                atoms.append(event.date.isoformat())
            atoms.append(event.text)
    return tuple(atom for atom in atoms if atom.strip())


def _list_items(items: list[str]) -> list[dict[str, str]]:
    return [
        {
            "label": (
                _LIST_LABELS[index]
                if index < len(_LIST_LABELS)
                else str(index + 1)
            ),
            "text": item,
        }
        for index, item in enumerate(items)
    ]


def _deterministic_seed(envelope: GenerationEnvelope) -> int:
    receipt = envelope.receipt
    stable_id = (
        (receipt.request_id if receipt else None)
        or (receipt.response_id if receipt else None)
        or blocks_to_body_text(envelope.result.generated_document.blocks)
    )
    return int(sha256(stable_id.encode("utf-8")).hexdigest()[:8], 16)


def _derived_stamp_seed(
    document_seed: int,
    slot_index: int,
    stamp_text: str,
) -> int:
    material = f"{document_seed}:{slot_index}:{stamp_text}".encode("utf-8")
    return int(sha256(material).hexdigest()[:8], 16)


def _build_document_metadata_context(
    envelope: GenerationEnvelope,
    *,
    seed: int,
) -> dict[str, Any]:
    """명시된 결재선과 행정 이벤트만 공통 렌더링 context로 변환한다."""

    metadata = envelope.result.generated_document.document_metadata
    administrative_events = [
        {
            "type": event.type,
            "date": event.date.isoformat(),
            "text": event.text,
        }
        for event in (metadata.administrative_events if metadata else [])
    ]
    signers: list[dict[str, Any]] = []
    approval_manifest: list[dict[str, Any]] = []
    approval_line = metadata.approval_line if metadata else None
    for slot_index, slot in enumerate(
        approval_line.slots if approval_line else [],
        start=1,
    ):
        stamp_data_uri = ""
        stamp_parameters: dict[str, object] | None = None
        if slot.stamp is not None:
            stamp_seed = (
                slot.stamp.seed
                if slot.stamp.seed is not None
                else _derived_stamp_seed(
                    seed,
                    slot_index,
                    slot.stamp.stamp_text,
                )
            )
            rendered_stamp = generate_synthetic_approval_stamp(
                slot.stamp.stamp_text,
                seed=stamp_seed,
                profile=slot.stamp.profile,
                shape=slot.stamp.shape,
            )
            stamp_placement = build_stamp_placement(stamp_seed)
            stamp_data_uri = rendered_stamp.data_uri
            stamp_parameters = rendered_stamp.parameters.to_dict()
            stamp_placement_data = stamp_placement.to_dict()
        else:
            stamp_placement_data = None

        approved_at = (
            slot.approved_at.isoformat() if slot.approved_at else ""
        )
        signers.append(
            {
                "role": slot.role,
                "name": slot.name or "",
                "date": approved_at,
                "status": slot.status,
                "stamp_data_uri": stamp_data_uri,
                "stamp_alt": (
                    f"{slot.role} 합성 결재 도장" if stamp_data_uri else ""
                ),
                "stamp_shape": (
                    stamp_parameters["shape"] if stamp_parameters else ""
                ),
                "stamp_placement": stamp_placement_data,
            }
        )
        approval_manifest.append(
            {
                "slot_index": slot_index,
                "role": slot.role,
                "name": slot.name,
                "status": slot.status,
                "approved_at": approved_at or None,
                "stamp": (
                    {
                        "mode": slot.stamp.mode,
                        "stamp_text": slot.stamp.stamp_text,
                        "parameters": stamp_parameters,
                        "placement": stamp_placement_data,
                    }
                    if slot.stamp is not None
                    else None
                ),
            }
        )

    return {
        "signers": signers,
        "approval_manifest": approval_manifest,
        "administrative_events": administrative_events,
    }


def build_template_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """구조화 blocks를 기존 공문 템플릿 공통 context로 변환한다."""

    document = envelope.result.generated_document
    paragraphs = [
        block for block in document.blocks if isinstance(block, ParagraphBlock)
    ]
    intro_block = paragraphs[0] if paragraphs else None
    intro_id = intro_block.block_id if intro_block else None

    sections: list[dict[str, Any]] = []
    details: list[dict[str, str]] = []
    tables: list[TableBlock] = []
    attachments: list[str] = []
    long_sections: list[dict[str, Any]] = []
    checklist_items: list[dict[str, str]] = []
    resolved_seed = seed if seed is not None else _deterministic_seed(envelope)
    metadata_context = _build_document_metadata_context(
        envelope,
        seed=resolved_seed,
    )

    for block in document.blocks:
        if isinstance(block, ParagraphBlock):
            if block.block_id == intro_id:
                continue
            sections.append({"text": block.text, "items": []})
            long_sections.append({"title": "", "items": [block.text]})
            checklist_items.append(
                {
                    "group": "본문",
                    "text": block.text,
                    "owner": "",
                    "status": "",
                }
            )
        elif isinstance(block, KeyValueBlock):
            details.extend(
                {"label": entry.key, "value": entry.value}
                for entry in block.entries
            )
        elif isinstance(block, BulletListBlock):
            sections.append({"text": "", "items": _list_items(block.items)})
            long_sections.append({"title": "", "items": list(block.items)})
            checklist_items.extend(
                {
                    "group": "목록",
                    "text": item,
                    "owner": "",
                    "status": "",
                }
                for item in block.items
            )
        elif isinstance(block, TableBlock):
            tables.append(block)
            checklist_items.append(
                {
                    "group": "표",
                    "text": " / ".join(block.columns),
                    "owner": "",
                    "status": "",
                }
            )
            checklist_items.extend(
                {
                    "group": "표",
                    "text": " / ".join(row),
                    "owner": "",
                    "status": "",
                }
                for row in block.rows
            )
        elif isinstance(block, AttachmentReferenceBlock):
            attachments.append(block.display_text())
        else:
            raise TypeError(f"Unsupported generated block: {type(block)!r}")

    primary_table = {"headers": [], "rows": []}
    if tables:
        primary = tables[0]
        primary_table = {
            "headers": list(primary.columns),
            "rows": [list(row) for row in primary.rows],
        }
        for extra in tables[1:]:
            if extra.columns == primary.columns:
                primary_table["rows"].extend([list(row) for row in extra.rows])
            else:
                flattened_rows = [" / ".join(row) for row in extra.rows]
                sections.append(
                    {
                        "text": " / ".join(extra.columns),
                        "items": _list_items(flattened_rows),
                    }
                )
                long_sections.append(
                    {
                        "title": " / ".join(extra.columns),
                        "items": flattened_rows,
                    }
                )

    for event in metadata_context["administrative_events"]:
        sections.append({"text": event["text"], "items": []})
        long_sections.append({"title": "", "items": [event["text"]]})
        checklist_items.append(
            {
                "group": "행정 처리",
                "text": event["text"],
                "owner": "",
                "status": "",
            }
        )

    return {
        "emblem": "",
        "slogan": "",
        "agency_name": document.agency_name or "",
        "source_agency_name": document.agency_name or "",
        "source_agency_category": (
            "" if document.agency_name else "generic_public"
        ),
        "brand_note": "",
        "recipient": "",
        "via": "",
        "title": document.title,
        "intro": intro_block.text if intro_block else "",
        "sections": sections,
        "long_sections": long_sections,
        "details": details,
        "table": primary_table,
        "attachments": attachments,
        "checklist_items": checklist_items,
        "issuer_title": "",
        "copy_recipients": "",
        "signers": metadata_context["signers"],
        "approval_manifest": metadata_context["approval_manifest"],
        "administrative_events": metadata_context["administrative_events"],
        "document_number": "",
        "issue_date": "",
        "postal_code": "",
        "address": "",
        "website": "",
        "phone": "",
        "fax": "",
        "email": "",
        "venue": "",
        "disclosure": "",
        "footer_note": "",
        "document_kind_label": "",
        "document_kicker": "",
        "details_heading": "",
        "table_heading": "",
        "secondary_heading": "",
        "form_label": "",
        "form_subtitle": "",
        "guide_text": "",
        "summary_text": "",
    }


def build_research_report_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """입력 block 순서를 보존한 연구보고서 전용 context를 만든다."""

    document = envelope.result.generated_document
    ordered_blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        rendered = block.model_dump(mode="json")
        if isinstance(block, TableBlock):
            rendered["column_count"] = len(block.columns)
            rendered["is_wide"] = len(block.columns) >= 8
        else:
            rendered["is_wide"] = False
        ordered_blocks.append(rendered)

    resolved_seed = seed if seed is not None else _deterministic_seed(envelope)
    metadata_context = _build_document_metadata_context(
        envelope,
        seed=resolved_seed,
    )
    title_length = len(re.sub(r"\s+", "", document.title))
    if title_length >= 70:
        title_class = "title-extra-long"
    elif title_length >= 38:
        title_class = "title-long"
    else:
        title_class = ""

    return {
        "document_type_label": "연구보고서",
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "blocks": ordered_blocks,
        "signers": metadata_context["signers"],
        "approval_manifest": metadata_context["approval_manifest"],
        "administrative_events": metadata_context["administrative_events"],
    }


def build_press_release_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """입력 순서를 보존해 보도자료 전용 context를 만든다."""

    document = envelope.result.generated_document
    resolved_seed = seed if seed is not None else _deterministic_seed(envelope)
    metadata_context = _build_document_metadata_context(
        envelope,
        seed=resolved_seed,
    )

    ordered_blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        rendered = block.model_dump(mode="json")
        rendered["is_lead"] = False
        rendered["is_summary"] = False
        rendered["is_footer_details"] = False
        if isinstance(block, TableBlock):
            rendered["column_count"] = len(block.columns)
            rendered["is_wide"] = (
                len(block.columns)
                >= PRESS_RELEASE_WIDE_TABLE_MIN_COLUMNS
            )
        else:
            rendered["is_wide"] = False
        ordered_blocks.append(rendered)

    header_meta: dict[str, Any] | None = None
    if ordered_blocks and ordered_blocks[0]["kind"] == "key_value":
        header_meta = ordered_blocks.pop(0)

    first_paragraph_index = next(
        (
            index
            for index, block in enumerate(ordered_blocks)
            if block["kind"] == "paragraph"
        ),
        None,
    )
    first_summary_index = next(
        (
            index
            for index, block in enumerate(ordered_blocks)
            if block["kind"] == "bullet_list"
        ),
        None,
    )
    last_non_attachment_index = next(
        (
            index
            for index in range(len(ordered_blocks) - 1, -1, -1)
            if ordered_blocks[index]["kind"] != "attachment_reference"
        ),
        None,
    )
    if first_paragraph_index is not None:
        lead = ordered_blocks[first_paragraph_index]
        lead["is_lead"] = True
        lead["lead_class"] = (
            "lead-long"
            if len(re.sub(r"\s+", "", str(lead["text"]))) >= 180
            else ""
        )
    if first_summary_index is not None:
        ordered_blocks[first_summary_index]["is_summary"] = True
    if (
        last_non_attachment_index is not None
        and ordered_blocks[last_non_attachment_index]["kind"] == "key_value"
    ):
        ordered_blocks[last_non_attachment_index][
            "is_footer_details"
        ] = True

    render_items: list[dict[str, Any]] = []
    index = 0
    while index < len(ordered_blocks):
        block = ordered_blocks[index]
        if block["kind"] == "paragraph" and not block["is_lead"]:
            paragraphs: list[dict[str, Any]] = []
            while (
                index < len(ordered_blocks)
                and ordered_blocks[index]["kind"] == "paragraph"
                and not ordered_blocks[index]["is_lead"]
            ):
                paragraphs.append(ordered_blocks[index])
                index += 1
            render_items.append(
                {
                    "kind": "paragraph_group",
                    "blocks": paragraphs,
                }
            )
            continue
        render_items.append(block)
        index += 1

    title_length = len(re.sub(r"\s+", "", document.title))
    if title_length >= 70:
        title_class = "title-extra-long"
    elif title_length >= 38:
        title_class = "title-long"
    else:
        title_class = ""

    return {
        "document_type_label": "보도자료",
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "header_meta": header_meta,
        "render_items": render_items,
        "signers": metadata_context["signers"],
        "approval_manifest": metadata_context["approval_manifest"],
        "administrative_events": metadata_context["administrative_events"],
    }


def render_generation_payload(
    payload: Mapping[str, Any],
    output_dir: Path,
    *,
    allow_failed: bool = False,
    per_template: int = 1,
    base_seed: int | None = None,
    template_slugs: set[str] | None = None,
) -> list[dict[str, object]]:
    """생성 계약 하나를 document_type 전용 템플릿 PDF로 렌더링한다."""

    envelope = parse_generation_payload(payload, allow_failed=allow_failed)
    seed = base_seed if base_seed is not None else _deterministic_seed(envelope)
    document = envelope.result.generated_document
    document_type = (
        envelope.result.source_classification.document_type.value
        if envelope.result.source_classification
        else None
    )
    if document_type == "research_report":
        renderer_family = "research_report"
    elif document_type == "press_release":
        renderer_family = "press_release"
    else:
        renderer_family = "official_document"
    input_metadata = {
        "contract_version": envelope.result.contract_version,
        "document_type": document_type,
        "renderer_family": renderer_family,
        "generation_route": envelope.result.generation_route,
        "generation_target": envelope.result.generation_target,
        "request_id": envelope.receipt.request_id if envelope.receipt else None,
        "response_id": envelope.receipt.response_id if envelope.receipt else None,
        "model_id": envelope.receipt.model_id if envelope.receipt else None,
        "content_sha256": sha256(
            blocks_to_body_text(document.blocks).encode("utf-8")
        ).hexdigest(),
        "rendered_from_failed_input": envelope.failure is not None,
    }
    if document_type == "research_report":
        context = build_research_report_context(envelope, seed=seed)
        manifest = render_research_report_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            template_slugs=template_slugs,
            required_source_texts=source_text_atoms(
                document,
                include_administrative_event_dates=True,
            ),
            max_pages=10,
            input_metadata=input_metadata,
        )
    elif document_type == "press_release":
        context = build_press_release_context(envelope, seed=seed)
        manifest = render_press_release_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            template_slugs=template_slugs,
            required_source_texts=source_text_atoms(
                document,
                include_administrative_event_dates=True,
            ),
            max_pages=PRESS_RELEASE_MAX_PAGES,
            input_metadata=input_metadata,
        )
    else:
        context = build_template_context(envelope, seed=seed)
        manifest = render_official_document_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            identity_seed=seed,
            template_slugs=template_slugs,
            protected_context_keys=_CONTENT_CONTEXT_KEYS,
            required_source_texts=source_text_atoms(document),
            enforce_expected_pages=False,
            reject_legacy_identity=False,
        )

    for entry in manifest:
        entry.setdefault("renderer_family", renderer_family)
        entry["input"] = input_metadata

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
