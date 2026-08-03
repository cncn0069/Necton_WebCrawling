"""LLM evidence quote가 실제 block text에 유일하게 대응하는지 검증한다."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from difflib import SequenceMatcher

from rd2.source_generation.contracts import EvidenceSpan, condense_whitespace


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
    부풀려 신뢰도가 높아 보이게 만들 수 있다. 중복 판정은 **공백을 무시한**
    형태로 한다. 매칭이 공백에 관대해졌으므로 공백만 다른 두 인용문은 같은
    근거를 두 번 센 것이고, 원문 그대로 비교하면 그 우회를 놓친다.
    """

    validated: list[EvidenceSpan] = []
    seen: set[tuple[str, str]] = set()
    for span in spans:
        key = (span.block_id, condense_whitespace(span.quote))
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


def validate_evidence_quotes_in_document(
    spans: Iterable[EvidenceSpan],
    document_text: str,
) -> tuple[EvidenceSpan, ...]:
    """block을 특정하지 않고 **원문 어딘가에** 있는지만 확인한다.

    원문 판별기용이다. 걸러야 할 것이 "지어낸 문장"뿐이고 위치는 아무도 쓰지
    않으므로 block 경계를 요구하지 않는다 — 자세한 근거는
    ``EvidenceSpan.require_in_document``에 있다.

    중복 거부는 ``(block_id, quote)`` 쌍 그대로다. 인용문만으로 좁혔다가
    되돌렸다 — 실측(alio-2021040202182097): 판별기가 ``Ⅱ. 분야별 주요 감사
    결과``를 목차와 본문 절 머리에서 각각 인용했는데, 문서에 실제로 두 번
    있는 서로 다른 자리라 부풀린 근거가 아니었다. block_id가 대조에서
    빠졌다고 해서 **모델이 서로 다른 자리를 가리켰다는 신호**까지 버릴
    이유는 없다.
    """

    validated: list[EvidenceSpan] = []
    seen: set[tuple[str, str]] = set()
    for span in spans:
        key = (span.block_id, condense_whitespace(span.quote))
        if key in seen:
            raise EvidenceResolutionError(
                f"duplicate evidence quote for block {span.block_id!r}"
            )
        seen.add(key)
        try:
            span.require_in_document(document_text)
        except ValueError as exc:
            raise EvidenceResolutionError(str(exc)) from exc
        validated.append(span)
    return tuple(validated)


def evidence_from_inserted_text(
    source_text: str,
    generated_body: str,
    quotes: Iterable[str],
) -> tuple[bool, ...]:
    """각 근거 인용문이 **원문에 없던 부분**에서 왔는지 판정한다.

    원문 보존율이 높아질수록 필요한 검사다. 생성물의 99%가 원문이면 검사기가
    원문 쪽 문장을 근거로 S를 줄 수 있고, 그러면 라벨은 맞아도 학습데이터로는
    해롭다 — 실측(alio 연간감사 결과보고서): 우리가 넣은 것은 감사 표본 기준과
    임계값인데 검사기는 이미 공표된 징계 처분 내역을 근거로 들었다. 공개된
    감사 연차보고서를 S로 배우게 된다.

    원문과 생성물을 대조해 **바뀌거나 새로 들어간 구간**을 구하고, 인용문이 그
    구간과 겹치는지 본다. 삽입 좌표를 따로 실어 나르지 않아도 되고, 합성
    마스킹뿐 아니라 문서를 새로 쓰는 route에도 그대로 쓸 수 있다.
    """

    matcher = SequenceMatcher(None, source_text, generated_body, autojunk=False)
    inserted: list[tuple[int, int]] = [
        (j1, j2)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes()
        if tag in ("insert", "replace")
    ]

    verdicts: list[bool] = []
    for quote in quotes:
        span = locate_quote(generated_body, quote)
        if span is None:
            # 어디인지 모르면 "삽입 쪽"이라고 말할 수 없다. 판단 불가는 판단이
            # 아니므로 보수적으로 False를 둔다.
            verdicts.append(False)
            continue
        start, end = span
        verdicts.append(any(start < hi and lo < end for lo, hi in inserted))
    return tuple(verdicts)


#: 근사 일치로 인정할 최소 글자 수.
#:
#: 검사기 인용문은 자리를 가리키는 **주소**다. 실측 7건 중 5건이 앞 24자는 맞고
#: 뒤만 바꿔 쓴 경우였는데, 그 정도면 어느 문장인지 분명하다.
_QUOTE_MIN_MATCH = 12


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """인용문이 가리키는 구간을 찾는다. 근사 일치를 허용하고, 없으면 ``None``."""

    needle = quote.strip()
    if not needle:
        return None
    exact = text.find(needle)
    if exact >= 0:
        return exact, exact + len(needle)
    block = SequenceMatcher(None, text, needle, autojunk=False).find_longest_match(
        0, len(text), 0, len(needle)
    )
    if block.size < _QUOTE_MIN_MATCH:
        return None
    return block.a, block.a + block.size
