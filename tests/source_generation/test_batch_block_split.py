"""개행 없는 본문을 block으로 나누는 배치 스크립트의 분할 규칙.

실측(2026-08-01, seoul_opengov 10건): **9건이 개행 0개**였다. 그래서 문서 전체가
``p1:b0`` 한 덩어리로 들어갔고, evidence 인용은 그 block 안에서 유일해야 하므로
(``EvidenceSpan.locate_in``) 같은 문구가 여러 번 나오는 공문에서 판별 단계가
``evidence_invalid``로 죽었다. 18752는 5회 실행 중 2회가 이 지점에서 멈췄다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from rd2.source_generation.contracts import EvidenceSpan, condense_whitespace

ROOT = Path(__file__).resolve().parents[2]


def _batch_module():
    spec = importlib.util.spec_from_file_location(
        "run_source_generation_batch",
        ROOT / "scripts" / "run_source_generation_batch.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: 실제 실패한 원문(seoul_opengov-18752)의 구조를 그대로 옮긴 축약본 —
#: 개행이 없고, 번호 항목이 이어지며, 같은 문구가 본문과 시행문 머리에 두 번 나온다.
RUN_ON_BODY = (
    "문 내용 1. 서울특별시 품질시험소 품질지도과-829호(2026.06.15.) 관련입니다. "
    "2. 광운대역 물류부지 개발사업-상업용지 현장에 대해 점검을 실시하고 그 결과를 "
    "붙임과 같이 알려드립니다. 3. 시공사는 점검결과에 대하여 조속히 시정하여 그 "
    "결과를 2026.8.20.(목) 까지 우리 시험소로 제출하여 주시기 바랍니다. "
    "붙임 1. 점검 결과 1부. 2. 주요 점검사진 1부. 끝. "
    "제목 광운대역 물류부지 개발사업-상업용지 점검 결과 알림 시행 품질지도과-1002 "
    "( 2026. 7. 20. ) 접수 우 06763 서울특별시 서초구 태봉로 131"
)


def test_run_on_body_becomes_multiple_blocks():
    module = _batch_module()
    snapshot = module._snapshot_from_body("doc-1", "seoul_opengov", RUN_ON_BODY)
    assert snapshot is not None

    blocks = [block for page in snapshot.pages for block in page.blocks]
    assert len(blocks) > 1, "개행이 없어도 한 덩어리로 두지 않는다"
    assert all(block.text.strip() for block in blocks)


def test_split_preserves_every_character():
    """나누기만 하고 내용은 건드리지 않는다.

    공백은 ``condense_whitespace`` 기준으로 비교한다 — 경계에서 ``strip``이
    지우는 것은 공백뿐이고, evidence 대조도 같은 기준을 쓴다.
    """

    module = _batch_module()
    snapshot = module._snapshot_from_body("doc-1", "seoul_opengov", RUN_ON_BODY)
    joined = "".join(
        block.text for page in snapshot.pages for block in page.blocks
    )
    assert condense_whitespace(joined) == condense_whitespace(RUN_ON_BODY)


def test_repeated_phrase_becomes_quotable_in_a_single_block():
    """분할의 목적 자체를 고정한다 — 반복 문구가 block 단위로는 유일해진다."""

    module = _batch_module()
    snapshot = module._snapshot_from_body("doc-1", "seoul_opengov", RUN_ON_BODY)
    blocks = [block for page in snapshot.pages for block in page.blocks]

    phrase = "광운대역 물류부지 개발사업"
    assert RUN_ON_BODY.count(phrase) > 1, "픽스처가 반복 문구를 담고 있어야 한다"

    quotable = []
    for block in blocks:
        try:
            EvidenceSpan(block_id=block.block_id, quote=phrase).locate_in(block.text)
        except ValueError:
            continue
        quotable.append(block.block_id)
    assert quotable, "나눈 뒤에는 그 문구를 유일하게 인용할 수 있는 block이 있어야 한다"


def test_short_fragments_are_merged_back():
    """경계 규칙은 문맥을 못 본다 — 날짜 "2026. 7. 20."의 " 7. "이 번호 항목으로
    걸려 "2026." / "7." 같은 조각이 생겼다. 홀로 서지 못하는 조각은 근거로
    인용할 수도 없으므로 앞 block에 도로 붙인다.
    """

    module = _batch_module()
    snapshot = module._snapshot_from_body("doc-1", "seoul_opengov", RUN_ON_BODY)
    blocks = [block for page in snapshot.pages for block in page.blocks]

    assert all(
        len(block.text) >= module.MIN_BLOCK_CHARS for block in blocks
    ), [block.text for block in blocks if len(block.text) < module.MIN_BLOCK_CHARS]


def test_bodies_with_newlines_keep_their_own_split():
    """개행이 있는 본문은 기존 두 분기가 처리하므로 이 경로를 타지 않는다."""

    module = _batch_module()
    body = "첫 문단입니다.\n\n둘째 문단입니다.\n\n셋째 문단입니다."
    snapshot = module._snapshot_from_body("doc-2", "seoul_opengov", body)
    blocks = [block for page in snapshot.pages for block in page.blocks]

    assert [block.text for block in blocks] == [
        "첫 문단입니다.",
        "둘째 문단입니다.",
        "셋째 문단입니다.",
    ]


@pytest.mark.parametrize("body", ["", "   ", "\n\n"])
def test_blank_body_returns_no_snapshot(body):
    module = _batch_module()
    assert module._snapshot_from_body("doc-3", "seoul_opengov", body) is None
