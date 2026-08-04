"""구조화 생성 계약을 문서 유형별 Jinja2 템플릿 PDF로 변환한다.

데이터 흐름::

    generation artifact JSON
        -> 계약/실패 검증
        -> blocks와 body_text 무결성 검증
        -> document_type별 템플릿 context
        -> Jinja2 + WeasyPrint
        -> PDF 텍스트 누락 검증 + manifest

``generated_document.blocks``가 내용의 기준이다. ``body_text``는 blocks를
평탄화한 값과 같은지 검증하는 폴백이며, 두 값이 다르면 렌더링하지 않는다.
``generated_document.agency_name``이 있으면 기관명을 그대로 보존한다.
공문 경로는 기관명이 없을 때 범용 공공기관 가상 풀을 사용한다.
연구보고서·보도자료·공고 계열 경로는 빈 기관명을 그대로 보존한다. 그 밖의
문서 메타데이터는 입력 계약에 없으면 생성하지 않는다.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rd2.generators.guide_rendering import render_guide_variations
from rd2.generators.interpretation_compilation_rendering import (
    render_interpretation_compilation_variations,
)
from rd2.generators.administrative_rule_rendering import (
    render_administrative_rule_variations,
)
from rd2.generators.document_security_marking import (
    apply_security_marking_to_manifest,
    resolve_security_marking,
)
from rd2.generators.official_document_rendering import (
    render_official_document_variations,
)
from rd2.generators.paged_output import finalize_manifest_page_limits
from rd2.generators.verbatim_rendering import render_verbatim_document
from rd2.generators.notice_rendering import render_notice_variations
from rd2.generators.meeting_minutes_rendering import (
    render_meeting_minutes_variations,
)
from rd2.generators.research_report_rendering import (
    render_research_report_variations,
)
from rd2.generators.status_report_rendering import (
    render_status_report_variations,
)
from rd2.generators.press_release_rendering import (
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
from rd2.generators.generated_blocks import (
    AttachmentReferenceBlock,
    BulletListBlock,
    ContractModel as _ContractModel,
    GeneratedBlock,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    TableBlock,
    non_empty as _non_empty,
    blocks_to_body_text,
)

#: 메이저 버전 2대만 받는다. ``GeneratedDocumentContract``는 렌더러가 필요한 IR
#: 부분만 호환 계약으로 다시 선언해 소스 계약의 마이너 변경을 자유롭게 받는다.
#: ``generation_target``도 과거 산출물의 부가 메타데이터를 보존하는 느슨한 계약을
#: 유지하고, 보안표지 후처리에서 필요한 분류와 군사기밀 등급만 검증한다.
#: v1 같은 실제 구조 변경만 여기서 막는다.
_CONTRACT_VERSION_RE = re.compile(r"^2\.\d+\.\d+$")
GENERATED_DOCUMENT_MAX_PAGES = 12
_UNTRUNCATED_RENDER_PAGE_BUDGET = 1_000
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
_NOTICE_DOCUMENT_TYPE_LABELS = {
    "bid_notice": "입찰공고",
    "bid_renotice": "입찰재공고",
    "pre_spec_notice": "사전규격공개",
    "public_offering": "공모",
    "notice": "공고",
}
_NOTICE_DOCUMENT_TYPES = frozenset(_NOTICE_DOCUMENT_TYPE_LABELS)
_ADMINISTRATIVE_RULE_LABELS = {
    SemanticDocumentType.DIRECTIVE: "훈령",
    SemanticDocumentType.REGULATION: "예규",
    SemanticDocumentType.NOTIFICATION: "고시",
}
_ADMINISTRATIVE_RULE_TYPES = frozenset(
    document_type.value for document_type in _ADMINISTRATIVE_RULE_LABELS
)
_ARTICLE_RE = re.compile(
    r"^(제\s*\d+\s*조(?:\([^)]*\))?)\s*(.*)$",
    re.DOTALL,
)
_CHAPTER_RE = re.compile(r"^제\s*\d+\s*장(?:\s|$)")

#: 이 route의 산출물만 템플릿 조립을 건너뛴다. 문자열로 두는 것은 이 모듈이
#: ``rd2.source_generation.contracts``를 import하지 않기 때문이다 — 계약을
#: 다시 선언해 느슨하게 검증하는 것이 이 모듈의 기존 방침이다.
MASK_RESTORATION_ROUTE = "mask_restoration"

#: 원문을 그대로 옮기는 생성 방식은 route로만 구분되지 않는다. 합성 마스킹은
#: ``anchored``·``span_seeded`` 같은 기존 route 위에서 도는 **생성 방식**이라
#: route만 보면 템플릿 조립으로 흘러가고, 원문 block이 200개 넘어 공문 템플릿이
#: 받지 못한다(실측 91건 중 렌더 실패 23건). 그래서 생성 쪽이 payload에
#: 표시를 남기고 여기서는 그 표시를 함께 본다.
VERBATIM_RENDER_KEY = "verbatim_render"


def _is_verbatim(envelope: "GenerationEnvelope") -> bool:
    if envelope.result.generation_route == MASK_RESTORATION_ROUTE:
        return True
    return bool(getattr(envelope.result, VERBATIM_RENDER_KEY, False))


def renderer_family_for_document_type(document_type: str | None) -> str:
    """분류값을 실제 렌더러 계열로 변환한다."""

    if document_type == "research_report":
        return "research_report"
    if document_type == "press_release":
        return "press_release"
    if document_type in _ADMINISTRATIVE_RULE_TYPES:
        return "administrative_rule"
    if document_type == SemanticDocumentType.INTERPRETATION_COMPILATION.value:
        return "interpretation_compilation"
    if document_type == SemanticDocumentType.GUIDE.value:
        return "guide"
    if document_type == "status_report":
        return "status_report"
    if document_type == "meeting_minutes":
        return "meeting_minutes"
    if document_type in _NOTICE_DOCUMENT_TYPES:
        return "notice"
    return "official_document"


class GeneratedDocumentPipelineError(ValueError):
    """생성 계약을 안전하게 렌더링할 수 없을 때 발생한다."""


class FailedGenerationPayloadError(GeneratedDocumentPipelineError):
    """상위 생성 단계가 실패한 레코드를 기본 정책으로 거부한다."""


class GeneratedDocumentContentMismatch(GeneratedDocumentPipelineError):
    """blocks와 body_text의 내용이 서로 다르다."""


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
        for index, block in enumerate(self.blocks):
            if not isinstance(block, KeyValueBlock):
                continue
            if (
                any(not entry.value.strip() for entry in block.entries)
                and index != 0
            ):
                raise ValueError(
                    "blank draft header values are only allowed in the first block"
                )
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
                f"Unsupported contract_version {result_version!r}; expected 2.x.x"
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


def _preview_text(value: str, *, limit: int) -> str:
    normalized = value.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "…"


def _needs_official_continuation(
    document: GeneratedDocumentContract,
) -> bool:
    text_characters = sum(len(value) for value in source_text_atoms(document))
    return (
        len(document.blocks) > 12
        or text_characters > 6_000
        or any(
            isinstance(block, ParagraphBlock) and len(block.text) > 1_500
            for block in document.blocks
        )
        or any(
            isinstance(block, TableBlock) and len(block.rows) > 18
            for block in document.blocks
        )
    )


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

    pagination_continuation = _needs_official_continuation(document)
    source_blocks = [
        block.model_dump(mode="json") for block in document.blocks
    ]
    intro = intro_block.text if intro_block else ""
    if pagination_continuation:
        intro = _preview_text(intro, limit=320)
        sections = [
            {
                "text": _preview_text(str(section["text"]), limit=280),
                "items": [
                    {
                        "label": str(item["label"]),
                        "text": _preview_text(str(item["text"]), limit=180),
                    }
                    for item in section["items"][:4]
                ],
            }
            for section in sections[:2]
        ]
        long_sections = [
            {
                "title": _preview_text(str(section["title"]), limit=80),
                "items": [
                    _preview_text(str(item), limit=220)
                    for item in section["items"][:4]
                ],
            }
            for section in long_sections[:4]
        ]
        details = [
            {
                "label": _preview_text(str(detail["label"]), limit=40),
                "value": _preview_text(str(detail["value"]), limit=100),
            }
            for detail in details[:6]
        ]
        primary_table = {
            "headers": [
                _preview_text(str(header), limit=40)
                for header in primary_table["headers"]
            ],
            "rows": [
                [_preview_text(str(cell), limit=80) for cell in row]
                for row in primary_table["rows"][:8]
            ],
        }
        attachments = [
            _preview_text(str(attachment), limit=120)
            for attachment in attachments[:4]
        ]
        checklist_items = [
            {
                **item,
                "text": _preview_text(str(item["text"]), limit=160),
            }
            for item in checklist_items[:10]
        ]

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
        "intro": intro,
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
        "pagination_continuation": pagination_continuation,
        "source_blocks": source_blocks,
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


def build_administrative_rule_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """행정규칙 block 순서를 유지한 렌더링 context를 만든다."""

    document = envelope.result.generated_document
    source_classification = envelope.result.source_classification
    if source_classification is None:
        raise ValueError("Administrative rule rendering requires document_type")
    document_type = source_classification.document_type
    if document_type not in _ADMINISTRATIVE_RULE_LABELS:
        raise ValueError(f"Unsupported administrative rule type: {document_type}")

    blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        if isinstance(block, ParagraphBlock):
            style_class = ""
            label = ""
            content = block.text
            article_match = _ARTICLE_RE.match(block.text)
            if _CHAPTER_RE.match(block.text):
                style_class = "is-chapter"
            elif block.text.strip().startswith("부칙"):
                style_class = "is-supplement"
            elif article_match:
                style_class = "is-article"
                label = article_match.group(1)
                content = article_match.group(2)
            blocks.append(
                {
                    "kind": block.kind,
                    "block_id": block.block_id,
                    "style_class": style_class,
                    "label": label,
                    "content": content,
                }
            )
        elif isinstance(block, KeyValueBlock):
            blocks.append(
                {
                    "kind": block.kind,
                    "block_id": block.block_id,
                    "entries": [
                        {"key": entry.key, "value": entry.value}
                        for entry in block.entries
                    ],
                }
            )
        elif isinstance(block, BulletListBlock):
            blocks.append(
                {
                    "kind": block.kind,
                    "block_id": block.block_id,
                    "items": list(block.items),
                }
            )
        elif isinstance(block, TableBlock):
            blocks.append(
                {
                    "kind": block.kind,
                    "block_id": block.block_id,
                    "columns": list(block.columns),
                    "rows": [list(row) for row in block.rows],
                    "is_wide": len(block.columns) >= 7,
                }
            )
        elif isinstance(block, AttachmentReferenceBlock):
            blocks.append(
                {
                    "kind": block.kind,
                    "block_id": block.block_id,
                    "attachment_id": block.attachment_id,
                    "label": block.label,
                    "description": block.description or "",
                }
            )
        else:
            raise TypeError(f"Unsupported generated block: {type(block)!r}")

    metadata_context = build_template_context(envelope, seed=seed)
    return {
        "document_type_label": _ADMINISTRATIVE_RULE_LABELS[document_type],
        "title": document.title,
        "agency_name": document.agency_name or "",
        "blocks": blocks,
        "signers": metadata_context["signers"],
        "administrative_events": metadata_context["administrative_events"],
    }


def build_interpretation_compilation_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """공문과 같은 5종 block을 순서 그대로 질의회시집 context로 만든다."""

    document = envelope.result.generated_document
    blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        rendered = block.model_dump(mode="json")
        rendered["is_wide"] = (
            isinstance(block, TableBlock) and len(block.columns) >= 7
        )
        blocks.append(rendered)

    metadata_context = build_template_context(envelope, seed=seed)
    title_length = len(re.sub(r"\s+", "", document.title))
    if title_length >= 70:
        title_class = "title-extra-long"
    elif title_length >= 38:
        title_class = "title-long"
    else:
        title_class = ""

    return {
        "document_type_label": "질의회시집",
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "blocks": blocks,
        "signers": metadata_context["signers"],
        "administrative_events": metadata_context["administrative_events"],
    }


def build_guide_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """공문과 같은 5종 block을 순서 그대로 guide context로 만든다."""

    document = envelope.result.generated_document
    blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        rendered = block.model_dump(mode="json")
        rendered["is_wide"] = (
            isinstance(block, TableBlock) and len(block.columns) >= 7
        )
        blocks.append(rendered)

    metadata_context = build_template_context(envelope, seed=seed)
    title_length = len(re.sub(r"\s+", "", document.title))
    if title_length >= 70:
        title_class = "title-extra-long"
    elif title_length >= 38:
        title_class = "title-long"
    else:
        title_class = ""

    return {
        "document_type_label": "GUIDE",
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "blocks": blocks,
        "signers": metadata_context["signers"],
        "administrative_events": metadata_context["administrative_events"],
    }


def build_status_report_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """공문과 같은 5종 block을 순서 그대로 현황보고 context로 만든다."""

    document = envelope.result.generated_document
    ordered_blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        rendered = block.model_dump(mode="json")
        if isinstance(block, TableBlock):
            rendered["column_count"] = len(block.columns)
            rendered["is_wide"] = len(block.columns) >= 7
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
        "document_type_label": "현황·통계자료",
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "blocks": ordered_blocks,
        "signers": metadata_context["signers"],
        "approval_manifest": metadata_context["approval_manifest"],
        "administrative_events": metadata_context["administrative_events"],
    }


def build_meeting_minutes_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """공통 5종 block을 순서 그대로 독립 회의록 context로 만든다."""

    document = envelope.result.generated_document
    ordered_blocks: list[dict[str, Any]] = []
    for block in document.blocks:
        rendered = block.model_dump(mode="json")
        if isinstance(block, TableBlock):
            rendered["column_count"] = len(block.columns)
            rendered["is_wide"] = len(block.columns) >= 7
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
        "document_type_label": "회의록",
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "blocks": ordered_blocks,
        "has_wide_blocks": any(
            block["is_wide"]
            for block in ordered_blocks
        ),
        "signers": metadata_context["signers"],
        "approval_manifest": metadata_context["approval_manifest"],
        "administrative_events": metadata_context["administrative_events"],
    }


def build_notice_context(
    envelope: GenerationEnvelope,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """입력 block 순서와 값만 보존한 공고 계열 전용 context를 만든다."""

    document_type = (
        envelope.result.source_classification.document_type.value
        if envelope.result.source_classification
        else None
    )
    if document_type not in _NOTICE_DOCUMENT_TYPES:
        raise ValueError(
            "notice context requires one of: "
            + ", ".join(sorted(_NOTICE_DOCUMENT_TYPES))
        )

    ordered_blocks: list[dict[str, Any]] = []
    for block in envelope.result.generated_document.blocks:
        rendered = block.model_dump(mode="json")
        if isinstance(block, TableBlock):
            rendered["column_count"] = len(block.columns)
        ordered_blocks.append(rendered)

    document = envelope.result.generated_document
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
        "document_type_label": _NOTICE_DOCUMENT_TYPE_LABELS[document_type],
        "title": document.title,
        "title_class": title_class,
        "agency_name": document.agency_name or "",
        "blocks": ordered_blocks,
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
    variation_offset: int = 0,
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
    document_type_enum = (
        envelope.result.source_classification.document_type
        if envelope.result.source_classification
        else None
    )
    content_sha256 = sha256(
        blocks_to_body_text(document.blocks).encode("utf-8")
    ).hexdigest()
    generation_target = (
        dict(envelope.result.generation_target)
        if envelope.result.generation_target is not None
        else None
    )
    if generation_target is not None:
        generation_target.setdefault("military_secret_grade", None)
    security_marking = resolve_security_marking(
        generation_target,
        agency_name=document.agency_name,
    )
    if _is_verbatim(envelope):
        # 원문을 그대로 옮긴 산출물이다 — 어느 템플릿 가족에도 속하지 않는다.
        # 자세한 이유는 ``verbatim_rendering`` 모듈 docstring에 있다.
        renderer_family = "verbatim"
    else:
        renderer_family = renderer_family_for_document_type(document_type)
    input_metadata = {
        "contract_version": envelope.result.contract_version,
        "document_type": document_type,
        "renderer_family": renderer_family,
        "generation_route": envelope.result.generation_route,
        "generation_target": generation_target,
        "request_id": envelope.receipt.request_id if envelope.receipt else None,
        "response_id": envelope.receipt.response_id if envelope.receipt else None,
        "model_id": envelope.receipt.model_id if envelope.receipt else None,
        "content_sha256": content_sha256,
        "rendered_from_failed_input": envelope.failure is not None,
    }
    if _is_verbatim(envelope):
        # 템플릿 조립을 건너뛴다 — mask_restoration 산출물은 기관명 행·수신란·
        # 결재선 푸터가 이미 들어 있어 템플릿에 부으면 레터헤드가 두 번 생기고
        # 자리 없는 원문 block이 빠져 missing source text로 떨어진다.
        manifest = [
            render_verbatim_document(
                document,
                output_dir,
                required_source_texts=(),
            )
        ]
    elif document_type == "research_report":
        context = build_research_report_context(envelope, seed=seed)
        manifest = render_research_report_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
            input_metadata=input_metadata,
        )
    elif document_type == "press_release":
        context = build_press_release_context(envelope, seed=seed)
        manifest = render_press_release_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
            input_metadata=input_metadata,
        )
    elif document_type_enum in _ADMINISTRATIVE_RULE_LABELS:
        context = build_administrative_rule_context(envelope, seed=seed)
        manifest = render_administrative_rule_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
        )
    elif document_type_enum == SemanticDocumentType.INTERPRETATION_COMPILATION:
        context = build_interpretation_compilation_context(envelope, seed=seed)
        manifest = render_interpretation_compilation_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
        )
    elif document_type_enum == SemanticDocumentType.GUIDE:
        context = build_guide_context(envelope, seed=seed)
        manifest = render_guide_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
        )
    elif document_type == "status_report":
        context = build_status_report_context(envelope, seed=seed)
        manifest = render_status_report_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
            input_metadata=input_metadata,
        )
    elif document_type == "meeting_minutes":
        context = build_meeting_minutes_context(envelope, seed=seed)
        manifest = render_meeting_minutes_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
            input_metadata=input_metadata,
        )
    elif document_type in _NOTICE_DOCUMENT_TYPES:
        context = build_notice_context(envelope, seed=seed)
        manifest = render_notice_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            template_slugs=template_slugs,
            required_source_texts=(),
            max_pages=_UNTRUNCATED_RENDER_PAGE_BUDGET,
            input_metadata=input_metadata,
        )
    else:
        context = build_template_context(envelope, seed=seed)
        manifest = render_official_document_variations(
            context,
            output_dir,
            per_template=per_template,
            base_seed=seed,
            variation_offset=variation_offset,
            identity_seed=seed,
            template_slugs=template_slugs,
            protected_context_keys=_CONTENT_CONTEXT_KEYS,
            required_source_texts=(),
            enforce_expected_pages=False,
            reject_legacy_identity=False,
        )

    include_administrative_event_dates = renderer_family in {
        "meeting_minutes",
        "notice",
        "press_release",
        "research_report",
        "status_report",
    }
    required_source_texts = (
        document.title,
        *source_text_atoms(
            document,
            include_administrative_event_dates=(
                include_administrative_event_dates
            ),
        ),
    )
    try:
        finalize_manifest_page_limits(
            manifest,
            max_pages=GENERATED_DOCUMENT_MAX_PAGES,
            render_page_budget=_UNTRUNCATED_RENDER_PAGE_BUDGET,
            required_source_texts=required_source_texts,
        )

        apply_security_marking_to_manifest(
            manifest,
            target=generation_target,
            agency_name=document.agency_name,
            content_sha256=content_sha256,
        )

        # 보안표지는 위에서 status="ok"인 산출물에 먼저 적용한다. 게시 상한을
        # 넘겨 뒷페이지를 의도적으로 버린 결과는 그 뒤에 별도 성공 상태로
        # 바꿔, 렌더 누락 없이 만들어진 사실과 최종 원문 전체 보존을 구분한다.
        for entry in manifest:
            if entry.get("status") == "ok" and entry.get("truncated"):
                entry["status"] = "ok_truncated"
    except Exception:
        if security_marking is not None:
            for entry in manifest:
                for key in ("pdf", "html"):
                    artifact_path = entry.get(key)
                    if isinstance(artifact_path, str) and artifact_path:
                        Path(artifact_path).unlink(missing_ok=True)
        raise

    for entry in manifest:
        entry.setdefault("renderer_family", renderer_family)
        entry["input"] = input_metadata

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
