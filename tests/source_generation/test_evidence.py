from __future__ import annotations

import pytest

from rd2.source_generation.contracts import EvidenceSpan
from rd2.source_generation.evidence import (
    EvidenceResolutionError,
    canonicalize_evidence_spans,
)


def test_canonicalize_evidence_spans_ignores_model_offsets():
    text = "· 연구결과의 활용 가능성 · 비공개 시 사유의 적정성"
    model_span = EvidenceSpan(
        block_id="p1:b14",
        start=18,
        end=30,
        quote="비공개 시 사유의 적정성",
    )

    result = canonicalize_evidence_spans(
        (model_span,),
        lambda block_id: text,
    )

    assert result == (
        EvidenceSpan(
            block_id="p1:b14",
            start=17,
            end=30,
            quote="비공개 시 사유의 적정성",
        ),
    )


def test_canonicalize_evidence_spans_rejects_missing_or_ambiguous_quote():
    with pytest.raises(EvidenceResolutionError, match="not found"):
        canonicalize_evidence_spans(
            (
                EvidenceSpan(
                    block_id="b1",
                    start=0,
                    end=2,
                    quote="없음",
                ),
            ),
            lambda block_id: "실제 본문",
        )

    with pytest.raises(EvidenceResolutionError, match="ambiguous"):
        canonicalize_evidence_spans(
            (
                EvidenceSpan(
                    block_id="b1",
                    start=0,
                    end=2,
                    quote="반복",
                ),
            ),
            lambda block_id: "반복 후 반복",
        )
