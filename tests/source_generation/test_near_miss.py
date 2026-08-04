"""O 판정에 붙는 시정 지적(``near_miss``) — 생성기 개선용 진단 신호.

생성기가 어디서 미끄러지는지는 지금까지 자유 문장 ``rationale``에만 남아
여러 건을 모아 패턴을 보기 어려웠다. 실측(2026-08-01, 5회 실행)에서 반복된
실패가 그런 종류였다 — 항목명만 쓰고 값을 안 쓴다, 자료를 요청만 한다.

**진단 전용이다.** 재생성 입력으로 되먹이지 않는다 — 채점자가 생성기에게 답을
알려주는 경로가 되면 두 판정이 더 이상 독립이 아니게 된다.
"""

from __future__ import annotations

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    ConsistencyAssessment,
    EvidenceSpan,
    NearMissNote,
)
from rd2.source_generation.prompts import build_prompt_bundle


def _open_assessment(**overrides) -> dict:
    payload = {
        "document_form": DocumentForm.OFFICIAL_LETTER,
        "classification": CsoClassification.O,
        "rationale": "비공개 요건이 확인되지 않는다.",
    }
    payload.update(overrides)
    return payload


def test_open_assessment_can_carry_a_near_miss_note():
    assessment = ConsistencyAssessment.model_validate(
        _open_assessment(
            near_miss=(
                NearMissNote(
                    subclause_key=SubclauseKey.BID_CONTRACT,
                    missing=(
                        "평가위원별 점수가 항목명만 있고 실제 배점 값이 없다"
                    ),
                    block_id="generated:b2",
                ),
            )
        )
    )

    assert assessment.classification == CsoClassification.O
    assert assessment.near_miss[0].subclause_key == SubclauseKey.BID_CONTRACT
    assert "배점 값이 없다" in assessment.near_miss[0].missing


def test_near_miss_is_optional_so_past_artifacts_still_parse():
    """기본값이 비어 있어야 계약 버전을 올리지 않고도 과거 산출물이 읽힌다."""

    assessment = ConsistencyAssessment.model_validate(_open_assessment())
    assert assessment.near_miss == ()
    assert assessment.contract_version == "2.3.0"


def test_near_miss_is_dropped_on_a_sensitive_verdict():
    """S로 판정해 놓고 "무엇이 부족했다"를 같이 내면 둘 중 하나는 거짓이다.

    **거짓인 쪽은 지적이고, 버릴 것도 지적이다.** 전에는 여기서
    ``ValidationError``를 냈는데, 그러면 gateway가 응답 전체를 재시도 없이
    버린다(``pipeline.SdkStructuredGateway.parse``의 ``ValidationError`` 분기는
    ``retryable=False``). 진단 전용 칸 하나가 멀쩡한 S 판정을 죽이는 값은
    치를 수 없다.
    """

    assessment = ConsistencyAssessment.model_validate(
        {
            "document_form": DocumentForm.OFFICIAL_LETTER,
            "classification": CsoClassification.S,
            "clause_no": ClauseNumber.CLAUSE_5,
            "subclause_key": SubclauseKey.BID_CONTRACT,
            "evidence_spans": (
                EvidenceSpan(block_id="generated:b0", quote="예정가격 72,000원"),
            ),
            "rationale": "예정가격이 확인된다.",
            "near_miss": (
                NearMissNote(
                    subclause_key=SubclauseKey.BID_CONTRACT,
                    missing="배점표가 없다",
                ),
            ),
        }
    )

    assert assessment.classification == CsoClassification.S
    assert assessment.near_miss == ()


def test_near_miss_outside_clauses_five_to_eight_is_dropped():
    """검증기는 제5~8호 taxonomy만 보지만 스키마의 ``SubclauseKey``에는 제1~4호가
    남아 있다 — ``classification=C`` 금지를 프롬프트 한 줄로 남긴 것과 같은
    상황이다. 그 값이 섞이면 세부유형별 집계가 조용히 오염되므로, 판정은 살리고
    지적만 버린다.
    """

    assessment = ConsistencyAssessment.model_validate(
        _open_assessment(
            near_miss=(
                NearMissNote(
                    subclause_key=SubclauseKey.LEGAL_SECRET,
                    missing="다른 법령의 비밀 지정 근거가 없다",
                ),
                NearMissNote(
                    subclause_key=SubclauseKey.BID_CONTRACT,
                    missing="평가위원별 배점 값이 없다",
                ),
            )
        )
    )

    assert assessment.classification == CsoClassification.O
    assert [note.subclause_key for note in assessment.near_miss] == [
        SubclauseKey.BID_CONTRACT
    ]


def test_blank_block_id_is_read_as_null():
    """프롬프트는 "짚을 수 없으면 null"이라고 하지만 모델은 빈 문자열을 낸다.

    ``NonEmptyText``가 그걸 거부하면 O 판정 전체가 재시도 없이 버려진다.
    """

    note = NearMissNote.model_validate(
        {
            "subclause_key": SubclauseKey.BID_CONTRACT,
            "missing": "평가위원별 배점 값이 없다",
            "block_id": "   ",
        }
    )

    assert note.block_id is None


def test_validator_prompt_asks_for_the_note_only_after_judging_open():
    """판정이 먼저고 지적이 나중이다.

    순서가 뒤집히면 "무엇이 있었으면 S였을까"를 먼저 생각하게 되고, 그건
    감찰관 페르소나에서 이미 경계한 S 오탐 쪽 편향이다.
    """

    prompt = build_prompt_bundle().definition("validator").system_prompt

    assert "[시정 지적 — O로 판정했을 때만]" in prompt
    assert "판정을 내린 뒤" in prompt
    assert "이 값을 채운다고 해서 위 classification을 S로 바꾸지" in prompt
    assert "억지로 채우지 않는다" in prompt
    # 항목명이 아니라 값이 없다고 쓰게 한다 — 이번 실측의 실패 유형 그대로다.
    assert "항목명이 아니라" in prompt


def test_near_miss_never_reaches_the_generator():
    """진단 전용 경계를 고정한다 — 재생성 입력에 섞이면 두 판정이 독립이 아니다."""

    bundle = build_prompt_bundle()
    for name in ("generator", "sensitive_generator"):
        definition = bundle.definition(name)
        assert "near_miss" not in definition.system_prompt, name
        assert "near_miss" not in definition.user_template, name
        assert "시정 지적" not in definition.system_prompt, name
