"""LLM evidence quote가 실제 block text에 유일하게 대응하는지 검증한다."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from rd2.source_generation.contracts import EvidenceSpan


class EvidenceResolutionError(ValueError):
    """Evidence quote가 실제 block text에 유일하게 대응하지 않을 때 발생한다."""


def validate_evidence_quotes(
    spans: Iterable[EvidenceSpan],
    block_text: Callable[[str], str],
) -> tuple[EvidenceSpan, ...]:
    """각 quote가 해당 block에 정확히 한 번 나타나는지 확인한다.

    모델은 인용문만 반환하고 위치는 코드가 찾는다(``EvidenceSpan`` 참고).
    따라서 여기서 걸러야 하는 것은 오프셋 오류가 아니라 다음 두 가지다.

    - 입력에 없는 문장을 지어낸 경우
    - 같은 block에 두 번 이상 나오는 문장이라 어느 쪽인지 모르는 경우

    같은 (block, quote) 쌍을 중복 반환하는 것도 거부한다 — 근거 개수를
    부풀려 신뢰도가 높아 보이게 만들 수 있다.
    """

    validated: list[EvidenceSpan] = []
    seen: set[tuple[str, str]] = set()
    for span in spans:
        key = (span.block_id, span.quote)
        if key in seen:
            raise EvidenceResolutionError(
                f"duplicate evidence quote for block {span.block_id!r}"
            )
        seen.add(key)
        try:
            span.locate_in(block_text(span.block_id))
        except ValueError as exc:
            raise EvidenceResolutionError(str(exc)) from exc
        validated.append(span)
    return tuple(validated)
