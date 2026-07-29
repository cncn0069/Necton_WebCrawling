"""구조화 생성 계약을 공문 템플릿 PDF로 변환한다.

데이터 흐름::

    pass1 JSON
        -> 계약/실패 검증
        -> blocks와 body_text 무결성 검증
        -> 기존 10종 템플릿 context
        -> Jinja2 + WeasyPrint
        -> PDF 텍스트 누락 검증 + manifest

``generated_document.blocks``가 내용의 기준이다. ``body_text``는 blocks를
평탄화한 값과 같은지 검증하는 폴백이며, 두 값이 다르면 렌더링하지 않는다.
``generated_document.agency_name``이 있으면 기관명을 그대로 보존한다.
기관명이 없으면 특정 직역과 본문이 잘못 결합되지 않도록 범용 공공기관
가상 풀만 사용한다. 그 밖의 문서 메타데이터는 입력 계약에 없으면 생성하지
않는다.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rd2.generators.official_document_rendering import (
    render_official_document_variations,
)

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


GeneratedBlock = Annotated[
    ParagraphBlock | KeyValueBlock | BulletListBlock | TableBlock,
    Field(discriminator="kind"),
]


class GeneratedDocumentContract(_ContractModel):
    contract_version: str
    title: str
    agency_name: str | None = None
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


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_version: str
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
        else:
            rows = ["\t".join(block.columns)]
            rows.extend("\t".join(row) for row in block.rows)
            chunks.append("\n".join(rows))
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


def source_text_atoms(document: GeneratedDocumentContract) -> tuple[str, ...]:
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
        else:
            atoms.extend(block.columns)
            for row in block.rows:
                atoms.extend(row)
    return tuple(dict.fromkeys(atom for atom in atoms if atom.strip()))


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
    long_sections: list[dict[str, Any]] = []
    checklist_items: list[dict[str, str]] = []

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
        else:
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
        "attachments": [],
        "checklist_items": checklist_items,
        "issuer_title": "",
        "copy_recipients": "",
        "signers": [],
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


def render_generation_payload(
    payload: Mapping[str, Any],
    output_dir: Path,
    *,
    allow_failed: bool = False,
    per_template: int = 1,
    base_seed: int | None = None,
    template_slugs: set[str] | None = None,
) -> list[dict[str, object]]:
    """생성 계약 하나를 공문 템플릿 변주 PDF로 렌더링한다."""

    envelope = parse_generation_payload(payload, allow_failed=allow_failed)
    seed = base_seed if base_seed is not None else _deterministic_seed(envelope)
    context = build_template_context(envelope, seed=seed)
    document = envelope.result.generated_document
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

    input_metadata = {
        "contract_version": envelope.result.contract_version,
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
    for entry in manifest:
        entry["input"] = input_metadata

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
