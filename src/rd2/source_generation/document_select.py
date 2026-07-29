"""85/86페이지 경계와 block 보존을 담당하는 순수 문서 선택 계층."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.source_generation.contracts import (
    DocumentSelection,
    FailureCode,
    RelevanceCandidateBlock,
    RelevanceSelectionRequest,
    RelevanceSelectionResponse,
    SelectionMethod,
    SourceDocumentSnapshot,
)

SELECTION_POLICY_VERSION = "front-relevance-v1"


class DocumentSelectionError(ValueError):
    def __init__(self, code: FailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SelectionConfig:
    """선택 정책 자체도 hash 입력이 되는 불변 설정."""

    policy_version: str = SELECTION_POLICY_VERSION
    page_threshold: int = 85
    front_page_limit: int = 20
    front_token_limit: int = 32_000
    estimated_chars_per_token: float = 2.0
    max_selected_blocks: int = 40

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be blank")
        if self.page_threshold < 1:
            raise ValueError("page_threshold must be at least 1")
        if not 1 <= self.front_page_limit <= self.page_threshold:
            raise ValueError("front_page_limit must be between 1 and page_threshold")
        if self.front_token_limit < 1:
            raise ValueError("front_token_limit must be at least 1")
        if self.estimated_chars_per_token <= 0:
            raise ValueError("estimated_chars_per_token must be positive")
        if self.max_selected_blocks < 1:
            raise ValueError("max_selected_blocks must be at least 1")

    def fingerprint_payload(self) -> dict[str, object]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            self.fingerprint_payload(),
            normalization_version=NORMALIZATION_VERSION,
        )


@dataclass(frozen=True)
class PreparedSelection:
    """full selection 또는 relevance 요청 중 정확히 하나를 가진다."""

    selection: DocumentSelection | None = None
    relevance_request: RelevanceSelectionRequest | None = None

    def __post_init__(self) -> None:
        if (self.selection is None) == (self.relevance_request is None):
            raise ValueError("prepared selection requires exactly one result kind")

    @property
    def requires_relevance(self) -> bool:
        return self.relevance_request is not None


def estimate_tokens(text: str, *, chars_per_token: float) -> int:
    """토크나이저 의존성 없이 보수적인 입력량을 재현 가능하게 추정한다."""

    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def render_source_blocks(blocks: tuple[RelevanceCandidateBlock, ...]) -> str:
    """LLM 입력에서 page/block 경계를 명시적으로 보존한다."""

    rendered: list[str] = []
    current_page: int | None = None
    for block in blocks:
        if block.page_number != current_page:
            rendered.append(f"[PAGE {block.page_number}]")
            current_page = block.page_number
        rendered.append(f"[BLOCK {block.block_id}]\n{block.text}")
    return "\n\n".join(rendered)


def _all_candidate_blocks(
    snapshot: SourceDocumentSnapshot,
) -> tuple[RelevanceCandidateBlock, ...]:
    return tuple(
        RelevanceCandidateBlock(
            page_number=page.page_number,
            block_id=block.block_id,
            text=block.text,
        )
        for page in snapshot.pages
        for block in page.blocks
    )


def _selection_sha256(
    snapshot: SourceDocumentSnapshot,
    *,
    config: SelectionConfig,
    method: SelectionMethod,
    blocks: tuple[RelevanceCandidateBlock, ...],
) -> str:
    payload = {
        "source_document_id": snapshot.source_document_id,
        "source_sha256": snapshot.source_sha256,
        "selection_config": config.fingerprint_payload(),
        "method": method.value,
        "selected_blocks": [
            {
                "page_number": block.page_number,
                "block_id": block.block_id,
                "text": block.text,
            }
            for block in blocks
        ],
    }
    return canonical_sha256(payload, normalization_version=NORMALIZATION_VERSION)


def _full_document_selection(
    snapshot: SourceDocumentSnapshot,
    config: SelectionConfig,
) -> DocumentSelection:
    blocks = _all_candidate_blocks(snapshot)
    return DocumentSelection(
        policy_version=config.policy_version,
        method=SelectionMethod.FULL_DOCUMENT,
        source_sha256=snapshot.source_sha256,
        selection_sha256=_selection_sha256(
            snapshot,
            config=config,
            method=SelectionMethod.FULL_DOCUMENT,
            blocks=blocks,
        ),
        original_page_count=snapshot.page_count,
        page_threshold=config.page_threshold,
        selected_page_numbers=tuple(page.page_number for page in snapshot.pages),
        selected_block_ids=tuple(block.block_id for block in blocks),
        truncated=False,
    )


def _front_relevance_request(
    snapshot: SourceDocumentSnapshot,
    config: SelectionConfig,
) -> RelevanceSelectionRequest:
    candidates: list[RelevanceCandidateBlock] = []
    estimated_tokens = 0

    for page in snapshot.pages[: config.front_page_limit]:
        for source_block in page.blocks:
            block = RelevanceCandidateBlock(
                page_number=page.page_number,
                block_id=source_block.block_id,
                text=source_block.text,
            )
            trial_candidates = (*candidates, block)
            trial_tokens = estimate_tokens(
                render_source_blocks(trial_candidates),
                chars_per_token=config.estimated_chars_per_token,
            )
            if not candidates and trial_tokens > config.front_token_limit:
                raise DocumentSelectionError(
                    FailureCode.CONTEXT_OVERFLOW,
                    (
                        f"first source block {block.block_id!r} needs about "
                        f"{trial_tokens} tokens, above front_token_limit="
                        f"{config.front_token_limit}"
                    ),
                )
            if trial_tokens > config.front_token_limit:
                break
            candidates.append(block)
            estimated_tokens = trial_tokens
        else:
            continue
        break

    if not candidates:
        raise DocumentSelectionError(
            FailureCode.SELECTION_INVALID,
            "front relevance input has no candidate blocks",
        )

    return RelevanceSelectionRequest(
        source_document_id=snapshot.source_document_id,
        source_sha256=snapshot.source_sha256,
        selection_config_sha256=config.sha256,
        original_page_count=snapshot.page_count,
        candidate_page_numbers=tuple(
            sorted({block.page_number for block in candidates})
        ),
        candidate_blocks=tuple(candidates),
        estimated_input_tokens=estimated_tokens,
    )


def prepare_document_selection(
    snapshot: SourceDocumentSnapshot,
    config: SelectionConfig | None = None,
) -> PreparedSelection:
    config = config or SelectionConfig()
    if snapshot.page_count <= config.page_threshold:
        return PreparedSelection(selection=_full_document_selection(snapshot, config))
    return PreparedSelection(
        relevance_request=_front_relevance_request(snapshot, config)
    )


def finalize_relevance_selection(
    snapshot: SourceDocumentSnapshot,
    request: RelevanceSelectionRequest,
    response: RelevanceSelectionResponse,
    config: SelectionConfig | None = None,
) -> DocumentSelection:
    """relevance ID를 검증하고 source order로 정규화해 최종 snapshot을 고정한다."""

    config = config or SelectionConfig()
    if request.source_document_id != snapshot.source_document_id:
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "relevance request source_document_id does not match snapshot",
        )
    if request.source_sha256 != snapshot.source_sha256:
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "relevance request source_sha256 does not match snapshot",
        )
    if request.selection_config_sha256 != config.sha256:
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "relevance request selection config no longer matches",
        )
    expected_request = _front_relevance_request(snapshot, config)
    if request != expected_request:
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "relevance request no longer matches the current extracted snapshot",
        )
    if len(response.selected_block_ids) > config.max_selected_blocks:
        raise DocumentSelectionError(
            FailureCode.SELECTION_INVALID,
            (
                f"relevance response selected {len(response.selected_block_ids)} blocks; "
                f"maximum is {config.max_selected_blocks}"
            ),
        )

    requested_ids = set(response.selected_block_ids)
    candidate_by_id = {block.block_id: block for block in request.candidate_blocks}
    unknown_ids = sorted(requested_ids - set(candidate_by_id))
    if unknown_ids:
        raise DocumentSelectionError(
            FailureCode.SELECTION_INVALID,
            f"relevance response contains unknown block IDs: {unknown_ids}",
        )

    selected_blocks = tuple(
        block for block in request.candidate_blocks if block.block_id in requested_ids
    )
    if not selected_blocks:
        raise DocumentSelectionError(
            FailureCode.SELECTION_INVALID,
            "relevance response selected no usable blocks",
        )

    return DocumentSelection(
        policy_version=config.policy_version,
        method=SelectionMethod.FRONT_RELEVANCE,
        source_sha256=snapshot.source_sha256,
        selection_sha256=_selection_sha256(
            snapshot,
            config=config,
            method=SelectionMethod.FRONT_RELEVANCE,
            blocks=selected_blocks,
        ),
        original_page_count=snapshot.page_count,
        page_threshold=config.page_threshold,
        selected_page_numbers=tuple(
            sorted({block.page_number for block in selected_blocks})
        ),
        selected_block_ids=tuple(block.block_id for block in selected_blocks),
        truncated=True,
    )


def render_selected_source(
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    config: SelectionConfig | None = None,
) -> str:
    """최종 selection과 원본 hash를 다시 대조한 뒤 Pass 1 입력을 렌더링한다."""

    validate_selection_hash(snapshot, selection, config)
    selected_ids = set(selection.selected_block_ids)
    blocks = tuple(
        block for block in _all_candidate_blocks(snapshot) if block.block_id in selected_ids
    )
    return render_source_blocks(blocks)


def validate_selection_hash(
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    config: SelectionConfig | None = None,
) -> None:
    config = config or SelectionConfig()
    if selection.source_sha256 != snapshot.source_sha256:
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "selection source_sha256 does not match snapshot",
        )
    if (
        selection.policy_version != config.policy_version
        or selection.page_threshold != config.page_threshold
    ):
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "selection policy fields do not match selection config",
        )
    selected_ids = set(selection.selected_block_ids)
    blocks = tuple(
        block for block in _all_candidate_blocks(snapshot) if block.block_id in selected_ids
    )
    if len(blocks) != len(selected_ids):
        raise DocumentSelectionError(
            FailureCode.SELECTION_INVALID,
            "selection references blocks missing from snapshot",
        )
    selected_page_numbers = tuple(sorted({block.page_number for block in blocks}))
    if selection.selected_page_numbers != selected_page_numbers:
        raise DocumentSelectionError(
            FailureCode.SELECTION_INVALID,
            "selection page numbers do not match selected blocks",
        )
    expected = _selection_sha256(
        snapshot,
        config=config,
        method=selection.method,
        blocks=blocks,
    )
    if expected != selection.selection_sha256:
        raise DocumentSelectionError(
            FailureCode.SOURCE_CHANGED,
            "selection hash no longer matches source snapshot or selection config",
        )
