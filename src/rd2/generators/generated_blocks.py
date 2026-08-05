"""렌더러가 보는 block 계약과 그 평탄화 규칙.

``generated_document_pipeline``에서 떼어냈다. 그 모듈이 이 block들을 쓰고,
``verbatim_rendering``도 평탄화 규칙 하나를 쓴다. 규칙이 pipeline 안에 있던
동안에는 verbatim이 pipeline을 되받아야 했고, pipeline은 이미 verbatim을
import하고 있어 순환이었다 — 함수 안에 숨긴 지연 import로 실행만 되게 해 둔
상태였다.

계약과 그것을 쓰는 orchestration을 갈라 두면 순환이 사라진다. 둘 다 이 모듈을
아래로 볼 뿐, 서로를 보지 않는다.

**소스 계약의 block과 다른 물건이다.** ``source_generation.contracts``의 block은
생성기가 채우는 것이고, 여기 것은 렌더러가 과거·counterfactual payload까지
살려야 해서 ``extra="allow"``로 느슨하다. 평탄화 규칙만 같아야 한다 — 이 값이
content_sha256이 되므로 두 표현이 갈리면 같은 문서가 서로 다른 해시를 갖는다.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rd2.source_generation.contracts import render_bullet_item
from rd2.source_generation.header_fields import DRAFT_BLANK_HEADER_KEYS


class ContractModel(BaseModel):
    # v2 계약의 호환 가능한 메타데이터 확장은 보존하되, kind별 필수 내용과
    # 표 형태는 아래 모델에서 계속 엄격하게 검증한다.
    model_config = ConfigDict(extra="allow")


def non_empty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class ParagraphBlock(ContractModel):
    kind: Literal["paragraph"]
    block_id: str
    text: str

    @field_validator("block_id", "text")
    @classmethod
    def _validate_non_empty(cls, value: str, info: Any) -> str:
        return non_empty(value, info.field_name)


class KeyValueEntry(ContractModel):
    key: str
    value: str

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return non_empty(value, "key")

    @model_validator(mode="after")
    def _blank_value_is_only_for_draft_header_fields(self) -> "KeyValueEntry":
        # 렌더러는 과거·counterfactual payload도 살려야 하므로 target의 초안 여부를
        # 다시 판정하지 않는다. 원문에 없을 수 있는 두 표제부 값만 구조적으로
        # 허용하고, 확정문서 적합성은 생성 단계의 check_document_form이 진단한다.
        if not self.value.strip() and self.key not in DRAFT_BLANK_HEADER_KEYS:
            raise ValueError(
                "blank key-value is only allowed for draft document number/date"
            )
        return self


class KeyValueBlock(ContractModel):
    kind: Literal["key_value"]
    block_id: str
    entries: list[KeyValueEntry]

    @field_validator("block_id")
    @classmethod
    def _validate_block_id(cls, value: str) -> str:
        return non_empty(value, "block_id")

    @field_validator("entries")
    @classmethod
    def _validate_entries(cls, value: list[KeyValueEntry]) -> list[KeyValueEntry]:
        if not value:
            raise ValueError("entries must not be empty")
        return value


class BulletListBlock(ContractModel):
    kind: Literal["bullet_list"]
    block_id: str
    items: list[str]

    @field_validator("block_id")
    @classmethod
    def _validate_block_id(cls, value: str) -> str:
        return non_empty(value, "block_id")

    @field_validator("items")
    @classmethod
    def _validate_items(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("items must not be empty")
        for item in value:
            non_empty(item, "items")
        return value


class TableBlock(ContractModel):
    kind: Literal["table"]
    block_id: str
    columns: list[str]
    rows: list[list[str]]

    @field_validator("block_id")
    @classmethod
    def _validate_block_id(cls, value: str) -> str:
        return non_empty(value, "block_id")

    @field_validator("columns")
    @classmethod
    def _validate_columns(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("columns must not be empty")
        for column in value:
            non_empty(column, "columns")
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


class AttachmentReferenceBlock(ContractModel):
    kind: Literal["attachment_reference"]
    block_id: str
    attachment_id: str
    label: str
    description: str | None = None

    @field_validator("block_id", "attachment_id", "label")
    @classmethod
    def _validate_non_empty(cls, value: str, info: Any) -> str:
        return non_empty(value, info.field_name)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        return None if value is None else non_empty(value, "description")

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
            # 소스 계약의 ``BulletListBlock.render_text``와 같은 규칙이어야 한다 —
            # 이 값이 content_sha256이 되므로 두 표현이 갈리면 같은 문서가 서로
            # 다른 해시를 갖는다.
            chunks.append("\n".join(render_bullet_item(item) for item in block.items))
        elif isinstance(block, TableBlock):
            rows = ["\t".join(block.columns)]
            rows.extend("\t".join(row) for row in block.rows)
            chunks.append("\n".join(rows))
        elif isinstance(block, AttachmentReferenceBlock):
            chunks.append(block.render_text())
        else:
            raise TypeError(f"Unsupported generated block: {type(block)!r}")
    return "\n\n".join(chunks)
