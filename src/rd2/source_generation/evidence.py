"""LLM evidence quote를 canonical character span으로 정규화한다."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from rd2.source_generation.contracts import EvidenceSpan


class EvidenceResolutionError(ValueError):
    """Evidence quote가 실제 block text에 유일하게 대응하지 않을 때 발생한다."""


def canonicalize_evidence_spans(
    spans: Iterable[EvidenceSpan],
    block_text: Callable[[str], str],
) -> tuple[EvidenceSpan, ...]:
    """모델의 start/end를 신뢰하지 않고 block text에서 정확한 좌표를 계산한다.

    동일 quote가 같은 block에 두 번 이상 있으면 임의의 occurrence를 고르지 않는다.
    모델은 더 긴 고유 quote를 반환해야 한다.
    """

    canonical: list[EvidenceSpan] = []
    seen: set[tuple[str, str]] = set()
    for span in spans:
        key = (span.block_id, span.quote)
        if key in seen:
            raise EvidenceResolutionError(
                f"duplicate evidence quote for block {span.block_id!r}"
            )
        seen.add(key)

        text = block_text(span.block_id)
        start = text.find(span.quote)
        if start < 0:
            raise EvidenceResolutionError(
                f"evidence quote not found in block {span.block_id!r}"
            )
        if text.find(span.quote, start + 1) >= 0:
            raise EvidenceResolutionError(
                f"evidence quote is ambiguous in block {span.block_id!r}; "
                "return a longer unique quote"
            )
        canonical.append(
            EvidenceSpan(
                block_id=span.block_id,
                start=start,
                end=start + len(span.quote),
                quote=span.quote,
            )
        )
    return tuple(canonical)
