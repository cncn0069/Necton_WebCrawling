from __future__ import annotations

import pytest
from pydantic import ValidationError

from rd2.source_generation.contracts import (
    FailureCode,
    RelevanceSelectionResponse,
    SelectionMethod,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
)
from rd2.source_generation.document_select import (
    DocumentSelectionError,
    SelectionConfig,
    finalize_relevance_selection,
    prepare_document_selection,
    render_selected_source,
    render_source_blocks,
    validate_selection_hash,
)


def _snapshot(page_count: int, *, source_hash: str = "a" * 64, text_size: int = 20):
    return SourceDocumentSnapshot(
        source_document_id="source-1",
        source="PRISM",
        manifest_key="manifest/source-1",
        source_sha256=source_hash,
        pages=tuple(
            SourcePage(
                page_number=page,
                blocks=(
                    SourceTextBlock(
                        block_id=f"p{page}:b0",
                        text=f"{page}쪽 " + ("가" * text_size),
                    ),
                ),
            )
            for page in range(1, page_count + 1)
        ),
    )


def test_85_pages_selects_full_document_with_stable_hash():
    snapshot = _snapshot(85)
    first = prepare_document_selection(snapshot)
    second = prepare_document_selection(snapshot)

    assert first.requires_relevance is False
    assert first.selection is not None
    assert first.selection.method == SelectionMethod.FULL_DOCUMENT
    assert first.selection.selected_page_numbers == tuple(range(1, 86))
    assert len(first.selection.selected_block_ids) == 85
    assert first.selection.selection_sha256 == second.selection.selection_sha256
    validate_selection_hash(snapshot, first.selection)


def test_86_pages_builds_one_front_limited_relevance_request():
    config = SelectionConfig(front_page_limit=3, front_token_limit=1_000)
    prepared = prepare_document_selection(_snapshot(86), config)

    assert prepared.requires_relevance is True
    assert prepared.relevance_request is not None
    assert prepared.relevance_request.original_page_count == 86
    assert prepared.relevance_request.selection_config_sha256 == config.sha256
    assert prepared.relevance_request.candidate_page_numbers == (1, 2, 3)
    assert tuple(
        block.block_id for block in prepared.relevance_request.candidate_blocks
    ) == ("p1:b0", "p2:b0", "p3:b0")
    assert "[PAGE 1]" in render_source_blocks(
        prepared.relevance_request.candidate_blocks
    )


def test_relevance_response_is_validated_and_normalized_to_source_order():
    config = SelectionConfig(front_page_limit=3)
    snapshot = _snapshot(86)
    request = prepare_document_selection(snapshot, config).relevance_request
    assert request is not None

    selection = finalize_relevance_selection(
        snapshot,
        request,
        RelevanceSelectionResponse(
            selected_block_ids=("p3:b0", "p1:b0"),
            rationale="첫 페이지의 문서 개요와 셋째 페이지의 법적 근거가 중요하다.",
        ),
        config,
    )

    assert selection.selected_block_ids == ("p1:b0", "p3:b0")
    assert selection.selected_page_numbers == (1, 3)
    assert selection.truncated is True
    assert render_selected_source(snapshot, selection, config).index("p1:b0") < (
        render_selected_source(snapshot, selection, config).index("p3:b0")
    )
    validate_selection_hash(snapshot, selection, config)


def test_relevance_response_rejects_empty_duplicate_unknown_and_too_many_ids():
    with pytest.raises(ValidationError):
        RelevanceSelectionResponse(selected_block_ids=(), rationale="없음")
    with pytest.raises(ValidationError, match="must be unique"):
        RelevanceSelectionResponse(
            selected_block_ids=("p1:b0", "p1:b0"),
            rationale="중복",
        )

    snapshot = _snapshot(86)
    config = SelectionConfig(front_page_limit=3, max_selected_blocks=2)
    request = prepare_document_selection(snapshot, config).relevance_request
    assert request is not None

    with pytest.raises(DocumentSelectionError) as unknown:
        finalize_relevance_selection(
            snapshot,
            request,
            RelevanceSelectionResponse(
                selected_block_ids=("not-present",),
                rationale="잘못된 ID",
            ),
            config,
        )
    assert unknown.value.code == FailureCode.SELECTION_INVALID

    with pytest.raises(DocumentSelectionError, match="maximum is 2"):
        finalize_relevance_selection(
            snapshot,
            request,
            RelevanceSelectionResponse(
                selected_block_ids=("p1:b0", "p2:b0", "p3:b0"),
                rationale="너무 많이 선택",
            ),
            config,
        )


def test_oversized_first_block_is_context_overflow_instead_of_split():
    snapshot = _snapshot(86, text_size=1_000)
    config = SelectionConfig(
        front_page_limit=3,
        front_token_limit=10,
        estimated_chars_per_token=1.0,
    )

    with pytest.raises(DocumentSelectionError) as exc:
        prepare_document_selection(snapshot, config)
    assert exc.value.code == FailureCode.CONTEXT_OVERFLOW


def test_source_or_config_mutation_invalidates_selection_hash():
    config = SelectionConfig()
    original = _snapshot(1)
    selection = prepare_document_selection(original, config).selection
    assert selection is not None

    changed_source = _snapshot(1, source_hash="b" * 64)
    with pytest.raises(DocumentSelectionError) as source_error:
        validate_selection_hash(changed_source, selection, config)
    assert source_error.value.code == FailureCode.SOURCE_CHANGED

    changed_config = SelectionConfig(front_token_limit=31_999)
    with pytest.raises(DocumentSelectionError) as config_error:
        validate_selection_hash(original, selection, changed_config)
    assert config_error.value.code == FailureCode.SOURCE_CHANGED


def test_stale_relevance_request_is_rejected_before_response_is_applied():
    original = _snapshot(86)
    config = SelectionConfig(front_page_limit=3)
    request = prepare_document_selection(original, config).relevance_request
    assert request is not None

    changed_extraction = _snapshot(86, text_size=21)
    with pytest.raises(DocumentSelectionError) as extraction_error:
        finalize_relevance_selection(
            changed_extraction,
            request,
            RelevanceSelectionResponse(
                selected_block_ids=("p1:b0",),
                rationale="첫 block",
            ),
            config,
        )
    assert extraction_error.value.code == FailureCode.SOURCE_CHANGED

    changed_config = SelectionConfig(front_page_limit=4)
    with pytest.raises(DocumentSelectionError) as config_error:
        finalize_relevance_selection(
            original,
            request,
            RelevanceSelectionResponse(
                selected_block_ids=("p1:b0",),
                rationale="첫 block",
            ),
            changed_config,
        )
    assert config_error.value.code == FailureCode.SOURCE_CHANGED


def test_snapshot_rejects_missing_page_number():
    with pytest.raises(ValidationError, match="contiguous"):
        SourceDocumentSnapshot(
            source_document_id="source-1",
            source="PRISM",
            manifest_key="manifest/source-1",
            source_sha256="a" * 64,
            pages=(
                SourcePage(
                    page_number=2,
                    blocks=(SourceTextBlock(block_id="p2:b0", text="본문"),),
                ),
            ),
        )
