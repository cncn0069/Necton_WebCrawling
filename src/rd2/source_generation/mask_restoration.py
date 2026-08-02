"""부분공개 원문의 마스킹 자리를 되돌려 S 문서를 만드는 route.

다른 route는 공개 원문을 **소재로** 새 문서를 쓴다 — 문서형식·업무 맥락·목표
조항을 모두 모델이 새로 구성하므로, 목표 조항이 본문에 실물로 실리지 못하면
그대로 실패한다. 이 route는 그 일을 하지 않는다.

서울시 공개 코퍼스의 결재문서 다수는 **부분공개** 문서다. 실무자가 제9조 제N호를
적용해 비공개로 판단한 자리를 ``****``로 가리고 나머지를 공개했고, 문서 하단
결재선에 ``부분공개(6)``처럼 적용한 호가 함께 찍혀 있다. 즉 원문 자체가

- 어느 호에 걸리는지(푸터 라벨)와
- 그 정보가 문서의 **어느 자리**에 있었는지(마스킹 스팬)

를 사람 손으로 표시해 준다. 무작위 300건 실측에서 76%가 푸터 라벨을,
59%가 라벨과 본문 마스킹을 함께 가졌고 호 분포는 제6호 108 / 제5호 82 /
제7호 31이었다.

그래서 이 route의 모델 호출은 문서를 쓰지 않고 **마스킹 자리에 들어갈 값만**
반환한다(``MaskFillResponse``). 원문 block은 코드가 그대로 옮기고 마스킹
구간만 치환하므로 서식·문체·결재선이 원문 그대로 남고, 다른 route에서 반복된
실패(원문과 무관한 업무로 표류, 원문 붙임 라벨 복사, 표제부·붙임 계약 위반)가
구조적으로 일어나지 않는다.

마스킹 **폭은 프롬프트에 노출하지 않는다.** ``[[m1]]``처럼 자리만 표시한다 —
폭을 주면 모델이 그 길이에 맞추려 하고, 그건 마스킹되기 전 원값을 추정하는
방향이다. 이 파이프라인이 만드는 것은 가상 값이지 복원값이 아니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SubclauseKey,
    clause_of_subclause,
)
from rd2.source_generation.contracts import (
    DocumentBlock,
    GeneratedDocumentIR,
    MaskFillResponse,
    ParagraphBlock,
    SourceDocumentSnapshot,
)

#: 결재선 푸터의 공개등급 표기. ``부분공개(6)``만 받는다 — ``공개``는 마스킹이
#: 없고, ``비공개``는 본문 자체가 코퍼스에 없다.
DISCLOSURE_LABEL_PATTERN = re.compile(r"부분공개\s*\(\s*([1-8])\s*\)")

#: 마스킹 문자열. 실측(무작위 150건)에서 마스킹은 사실상 전부 ``*``였다 —
#: ``□□``는 39건이지만 체크박스이고 ``○○``는 1건, ``XXX``는 0건이다.
#:
#: 경계는 **2자**다. 단독 ``*``는 각주 기호라 반드시 빼야 하지만(1,657건),
#: 2연속은 실측 문맥에서 각주로 쓰인 사례가 하나도 없었고 전부 짧은 값이
#: 가려진 자리였다 — ``직급 **``, ``- 연동확인사항 **``, ``폐 전 종 류**``.
#:
#: 처음에 3자로 뒀다가 낮췄다. 3자 기준에서는 route를 타는 178건 중 8건이
#: 2자 마스킹을 남긴 채 생성됐다. 두 방향의 실패 비용이 다르다 — 각주를
#: 마스킹으로 오인하면 그 자리에 값이 하나 더 들어갈 뿐이지만, 마스킹을
#: 놓치면 ``**``가 그대로 남은 문서가 나와 ``MASK_REMAINS``로 버려진다.
MASK_PATTERN = re.compile(r"\*{2,}|○{2,}|●{2,}")

#: 표 셀 하나가 통째로 ``*`` 하나인 경우. 위 규칙이 유일하게 놓치는 자리다.
#:
#: 실측(seoul_opengov-36534186) 훈련 실적 표::
#:
#:     | *********** | ** | * | * | *** | * | *** | * | *** | * |
#:
#: 여기서 단독 ``*``는 각주가 아니라 **한 자리 숫자가 가려진 것**이다. 문장
#: 안에 붙은 각주 ``*``와 구분되는 것은 셀 경계(``|``)로 둘러싸여 홀로 서 있다는
#: 점뿐이라, 그 형태일 때만 마스킹으로 본다.
_TABLE_CELL_MASK_PATTERN = re.compile(r"(?<=\|)(\s*)(\*)(\s*)(?=\|)")

#: 이 파이프라인이 다루는 범위. 푸터가 제1~4호를 가리키면 route를 쓰지 않는다.
SUPPORTED_CLAUSES: frozenset[ClauseNumber] = frozenset(
    {
        ClauseNumber.CLAUSE_5,
        ClauseNumber.CLAUSE_6,
        ClauseNumber.CLAUSE_7,
        ClauseNumber.CLAUSE_8,
    }
)

#: 마스킹 자리 표시. 원문에 이 문자열이 이미 있을 일은 없다.
_PLACEHOLDER = "[[{mask_id}]]"


class MaskRestorationError(ValueError):
    """마스킹 복원 입력·응답이 계약을 만족하지 못한다."""


@dataclass(frozen=True)
class MaskSpan:
    """원문 한 block 안의 마스킹 구간 하나."""

    mask_id: str
    block_id: str
    start: int
    length: int

    @property
    def end(self) -> int:
        return self.start + self.length


@dataclass(frozen=True)
class RedactionEvidence:
    """푸터 라벨과 마스킹 스팬 — 원문이 사람 손으로 남긴 법적 근거."""

    clause_no: ClauseNumber
    label_block_id: str
    label_quote: str
    mask_spans: tuple[MaskSpan, ...]

    def spans_for(self, block_id: str) -> tuple[MaskSpan, ...]:
        return tuple(
            span for span in self.mask_spans if span.block_id == block_id
        )


def detect_redaction_evidence(
    snapshot: SourceDocumentSnapshot,
) -> RedactionEvidence | None:
    """푸터 라벨과 마스킹이 **둘 다** 있을 때만 근거를 만든다.

    라벨만 있고 본문 마스킹이 없는 문서(실측 229건 중 52건)는 비공개분이 붙임·
    별지에 있어 본문에 채울 자리가 없다. 마스킹만 있고 라벨이 없으면 어느 호인지
    사람이 표시해 주지 않은 것이므로 이 route의 전제가 서지 않는다. 둘 중 하나가
    없으면 ``None``을 돌려 호출자가 기존 route로 가게 한다.
    """

    clause_no: ClauseNumber | None = None
    label_block_id = ""
    label_quote = ""
    spans: list[MaskSpan] = []

    for page in snapshot.pages:
        for block in page.blocks:
            if clause_no is None:
                match = DISCLOSURE_LABEL_PATTERN.search(block.text)
                if match is not None:
                    candidate = ClauseNumber(match.group(1))
                    if candidate in SUPPORTED_CLAUSES:
                        clause_no = candidate
                        label_block_id = block.block_id
                        label_quote = match.group(0)
            for start, end in _mask_ranges(block.text):
                spans.append(
                    MaskSpan(
                        mask_id=f"m{len(spans) + 1}",
                        block_id=block.block_id,
                        start=start,
                        length=end - start,
                    )
                )

    if clause_no is None or not spans:
        return None
    return RedactionEvidence(
        clause_no=clause_no,
        label_block_id=label_block_id,
        label_quote=label_quote,
        mask_spans=tuple(spans),
    )


def resolve_mask_restoration_subclause(
    evidence: RedactionEvidence,
    *,
    candidates: tuple[SubclauseKey | None, ...],
) -> SubclauseKey | None:
    """푸터가 정한 호 **안에서** 첫 번째로 맞는 세부유형을 고른다.

    호는 사람이 정해 준 값이므로 흔들 수 없다. 세부유형은 푸터에 없으니
    호출자가 우선순위대로 넘긴 후보(요청 목표 -> 판별기 primary ->
    compatible)에서 그 호에 속하는 첫 값을 쓴다. 하나도 맞지 않으면 ``None``을
    돌려주고, 그때는 호만 잠근 채 세부유형 없이 생성한다 — 푸터 라벨이 이미
    유효한 법적 근거이므로 세부유형을 못 고른다고 문서를 버릴 이유가 없다.
    """

    for candidate in candidates:
        if candidate is None:
            continue
        if clause_of_subclause(candidate) is evidence.clause_no:
            return candidate
    return None


def render_masked_source(
    snapshot: SourceDocumentSnapshot,
    evidence: RedactionEvidence,
) -> str:
    """마스킹을 ``[[m1]]``로 바꾼 원문 전체.

    폭을 지우는 것이 요점이다 — ``**********``을 그대로 보여 주면 모델이 열
    글자짜리 값을 만들려 하고, 그건 원값 길이 추정이다.
    """

    lines: list[str] = []
    for page in snapshot.pages:
        for block in page.blocks:
            lines.append(f"[{block.block_id}]")
            lines.append(_placeholder_text(block.text, evidence.spans_for(block.block_id)))
            lines.append("")
    return "\n".join(lines).rstrip()


def render_mask_slot_table(evidence: RedactionEvidence) -> str:
    """채워야 할 자리 목록. block 순서 그대로 둔다."""

    lines = [f"채워야 할 마스킹 자리 {len(evidence.mask_spans)}개:"]
    lines.extend(
        f"- {span.mask_id} (block {span.block_id})"
        for span in evidence.mask_spans
    )
    return "\n".join(lines)


def apply_mask_fills(
    snapshot: SourceDocumentSnapshot,
    evidence: RedactionEvidence,
    response: MaskFillResponse,
    *,
    title: str | None = None,
) -> GeneratedDocumentIR:
    """원문 block을 그대로 옮기고 마스킹 구간만 응답 값으로 치환한다.

    모델은 값만 반환하고 문서 구성에는 손대지 않는다. block ID·순서·나머지
    글자가 원문과 같으므로 서식 검사와 렌더 계약을 원문이 이미 통과한 상태로
    물려받는다.
    """

    fills = {fill.mask_id: fill.value for fill in response.fills}
    expected = {span.mask_id for span in evidence.mask_spans}
    missing = sorted(expected - set(fills))
    if missing:
        raise MaskRestorationError(
            f"mask fills missing for {', '.join(missing)}"
        )
    unknown = sorted(set(fills) - expected)
    if unknown:
        raise MaskRestorationError(
            f"mask fills reference unknown mask ids: {', '.join(unknown)}"
        )
    for mask_id, value in sorted(fills.items()):
        if MASK_PATTERN.search(value):
            raise MaskRestorationError(
                f"mask fill {mask_id} still contains a mask string: {value!r}"
            )

    blocks: list[DocumentBlock] = []
    for page in snapshot.pages:
        for block in page.blocks:
            text = _fill_text(
                block.text,
                evidence.spans_for(block.block_id),
                fills,
            )
            blocks.append(ParagraphBlock(block_id=block.block_id, text=text))

    return GeneratedDocumentIR(
        title=title or derive_title(snapshot),
        blocks=tuple(blocks),
    )


#: 결재문서 본문에서 제목을 담는 행. ``| 제목 | 가정의 날 초과근무 실시 |``.
_TITLE_ROW_PATTERN = re.compile(r"\|\s*제\s*목\s*\|(?P<title>[^|]+)\|")


def derive_title(snapshot: SourceDocumentSnapshot) -> str:
    """원문 ``제목`` 행을 그대로 쓴다.

    이 route는 문서를 새로 쓰지 않으므로 제목도 새로 짓지 않는다. 결재문서가
    아니어서 제목 행이 없으면 문서 ID로 대신한다 — ``NonEmptyText``만 만족하면
    되고, 제목을 지어내는 것보다 출처가 분명하다.
    """

    for page in snapshot.pages:
        for block in page.blocks:
            match = _TITLE_ROW_PATTERN.search(block.text)
            if match is None:
                continue
            title = MASK_PATTERN.sub("", match.group("title")).strip()
            if title:
                return title
    return snapshot.source_document_id


def _mask_ranges(text: str) -> list[tuple[int, int]]:
    """한 block의 마스킹 구간을 **문서 순서대로** 돌려준다.

    두 패턴이 겹칠 일은 없다 — 하나는 2자 이상, 하나는 셀 안에 홀로 선 1자다.
    그래도 순서를 섞으면 ``mask_id``와 화면상 자리가 어긋나 모델이 앞뒤 문맥과
    다른 자리를 채우게 되므로 시작 위치로 다시 정렬한다.
    """

    ranges = [(m.start(), m.end()) for m in MASK_PATTERN.finditer(text)]
    ranges.extend(
        (m.start(2), m.end(2)) for m in _TABLE_CELL_MASK_PATTERN.finditer(text)
    )
    ranges.sort()
    return ranges


def _placeholder_text(text: str, spans: tuple[MaskSpan, ...]) -> str:
    return _replace_spans(
        text,
        spans,
        lambda span: _PLACEHOLDER.format(mask_id=span.mask_id),
    )


def _fill_text(
    text: str,
    spans: tuple[MaskSpan, ...],
    fills: dict[str, str],
) -> str:
    return _replace_spans(text, spans, lambda span: fills[span.mask_id])


def _replace_spans(text, spans, value_of):
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for span in sorted(spans, key=lambda item: item.start):
        parts.append(text[cursor : span.start])
        parts.append(value_of(span))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)
