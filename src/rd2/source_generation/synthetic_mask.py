"""마스킹이 없는 원문에 **자리를 만들어** 값을 채우는 2단계 생성.

``mask_restoration``은 사람이 남긴 마스킹 자리를 채운다. 그 route의 원문 보존율은
91%였고, 다른 route는 1%였다(실측 52건 중앙값). 차이는 프롬프트 문구가 아니라
**출력 계약**이다 — 저쪽은 값만 반환하고 이쪽은 문서 전체를 반환한다. 문서를
달라고 하면 모델은 원문을 재타이핑하는 대신 요약한다.

여기서는 마스킹을 우리가 만든다.

    1단계  원문 -> [모델] -> 자리 목록(block_id/mode/want)     출력 수백 토큰
    2단계  코드가 그 자리에 ``[[m1]]`` 표시
           -> [모델] -> 값만                                   출력 수백 토큰
    3단계  코드가 값을 끼워 넣는다. 나머지 block은 손대지 않는다.

모델이 "무엇을 남길지"를 한 번도 결정하지 않는다. 그게 요점이다.

자리를 **block ID로** 받는다. 처음에는 원문 문장을 인용하게 했는데(anchor) 그
문장을 코드가 다시 찾아야 했고, 찾는 일이 계속 빗나갔다 — 두 block에 걸친 인용,
프롬프트 예시를 원문으로 착각, ``감사원`` -> ``감사원의`` 같은 조사 한 글자,
block 머리표까지 포함. 91건 중 19건이 거기서 끝났다.

block은 이미 한 줄 단위다(실측 19,148개의 길이 중앙값 13자, 90%가 47자 이하).
"이 block을 바꿔라"가 "이 문장을 바꿔라"와 사실상 같아서, 굳이 문장을 다시 찾을
이유가 없었다. 사전 조회 한 번으로 끝나고 근사 매칭·좌표 계산·임계값이 전부
사라졌다.

``after``로 넣는 값은 원문 block을 고치지 않고 **새 block으로** 뒤에 붙인다.
원문 문장을 건드리지 않으면서 내용을 더하는 방법이 그것뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from rd2.source_generation.contracts import (
    DocumentBlock,
    GeneratedDocumentIR,
    InsertionMode,
    InsertionPlan,
    MaskFillResponse,
    ParagraphBlock,
    SourceDocumentSnapshot,
)
from rd2.source_generation.mask_restoration import MASK_PATTERN, derive_title

#: 자리 표시. ``mask_restoration``과 같은 모양이라 2단계 프롬프트를 공유한다.
_PLACEHOLDER = "[[{mask_id}]]"


class SyntheticMaskError(ValueError):
    """1단계 계획을 원문 자리로 옮길 수 없다."""


@dataclass(frozen=True)
class PlacedSlot:
    """확인이 끝난 자리 하나."""

    mask_id: str
    block_id: str
    mode: InsertionMode
    want: str


def place_insertion_plan(
    snapshot: SourceDocumentSnapshot,
    plan: InsertionPlan,
) -> tuple[PlacedSlot, ...]:
    """block ID가 원문에 실재하는지 확인한다.

    없는 ID는 **버리지 않고 실패시킨다.** 조용히 건너뛰면 모델이 지어낸 자리로도
    문서가 만들어지고, 그때 어느 자리가 근거 없이 생겼는지 사후에 알 수 없다.
    """

    known = {
        block.block_id for page in snapshot.pages for block in page.blocks
    }
    placed: list[PlacedSlot] = []
    for order, slot in enumerate(plan.slots, start=1):
        if slot.block_id not in known:
            raise SyntheticMaskError(
                f"unknown source block_id: {slot.block_id!r}"
            )
        placed.append(
            PlacedSlot(
                mask_id=f"m{order}",
                block_id=slot.block_id,
                mode=slot.mode,
                want=slot.want,
            )
        )
    return tuple(placed)


def render_slotted_source(
    snapshot: SourceDocumentSnapshot,
    slots: tuple[PlacedSlot, ...],
) -> str:
    """자리를 ``[[m1]]``로 표시한 원문 전체.

    ``after``는 원문 block을 그대로 두고 **그 다음 줄**에 표시를 놓는다 —
    2단계 모델이 앞 문장을 문맥으로 읽을 수 있어야 한다.
    """

    values = {
        slot.mask_id: _PLACEHOLDER.format(mask_id=slot.mask_id) for slot in slots
    }
    lines: list[str] = []
    for block_id, body in _rewrite(snapshot, slots, values):
        lines.append(f"[{block_id}]")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def render_slot_table(slots: tuple[PlacedSlot, ...]) -> str:
    lines = [f"채워야 할 자리 {len(slots)}개:"]
    for slot in slots:
        lines.append(f"- {slot.mask_id} (원하는 값: {slot.want})")
    return "\n".join(lines)


def apply_insertion_fills(
    snapshot: SourceDocumentSnapshot,
    slots: tuple[PlacedSlot, ...],
    response: MaskFillResponse,
    *,
    title: str | None = None,
) -> GeneratedDocumentIR:
    """원문 block을 그대로 옮기고 자리에만 값을 넣는다."""

    fills = {fill.mask_id: fill.value for fill in response.fills}
    expected = {slot.mask_id for slot in slots}
    missing = sorted(expected - set(fills))
    if missing:
        raise SyntheticMaskError(f"fills missing for {', '.join(missing)}")
    unknown = sorted(set(fills) - expected)
    if unknown:
        raise SyntheticMaskError(
            f"fills reference unknown ids: {', '.join(unknown)}"
        )
    for mask_id, value in sorted(fills.items()):
        if MASK_PATTERN.search(value):
            raise SyntheticMaskError(
                f"fill {mask_id} still contains a mask string: {value!r}"
            )

    blocks: list[DocumentBlock] = [
        ParagraphBlock(block_id=block_id, text=body)
        for block_id, body in _rewrite(snapshot, slots, fills)
    ]
    return GeneratedDocumentIR(
        title=title or derive_title(snapshot),
        blocks=tuple(blocks),
    )


def _rewrite(
    snapshot: SourceDocumentSnapshot,
    slots: tuple[PlacedSlot, ...],
    values: dict[str, str],
) -> list[tuple[str, str]]:
    """자리를 ``values``로 채운 ``(block_id, text)`` 목록.

    ``replace``는 그 block 전체를 값으로 바꾸고, ``after``는 원문 block을 그대로
    둔 채 뒤에 새 block을 만든다.
    """

    by_block: dict[str, list[PlacedSlot]] = {}
    for slot in slots:
        by_block.setdefault(slot.block_id, []).append(slot)

    rewritten: list[tuple[str, str]] = []
    for page in snapshot.pages:
        for block in page.blocks:
            here = by_block.get(block.block_id, [])
            replaced = next(
                (item for item in here if item.mode is InsertionMode.REPLACE),
                None,
            )
            body = values[replaced.mask_id] if replaced else block.text
            if body.strip():
                rewritten.append((block.block_id, body))
            for slot in here:
                if slot.mode is InsertionMode.AFTER:
                    rewritten.append(
                        (
                            f"{block.block_id}+{slot.mask_id}",
                            values[slot.mask_id],
                        )
                    )
    return rewritten
