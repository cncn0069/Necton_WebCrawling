"""부분공개 원문의 마스킹 복원 route.

여기서 지키는 것은 하나다 — **원문은 그대로 남고 마스킹 자리만 바뀐다.** 다른
route의 실패는 대부분 생성기가 문서를 새로 쓰다가 원문에서 표류한 것이었고,
이 route는 모델에게 문서를 쓰게 하지 않아 그 표류가 불가능해야 한다.
"""

from __future__ import annotations

import pytest

from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    FailureStage,
    GenerationRoute,
    MaskFill,
    MaskFillResponse,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
)
from rd2.source_generation.document_select import prepare_document_selection
from rd2.source_generation.mask_restoration import (
    MaskRestorationError,
    apply_mask_fills,
    derive_title,
    detect_redaction_evidence,
    render_mask_slot_table,
    render_masked_source,
    resolve_mask_restoration_subclause,
)
from rd2.source_generation.pipeline import (
    PipelineConfig,
    build_generation_plan,
    execute_generation,
)

from .v2_fixtures import FakeGateway, source_assessment, target

#: 실제 코퍼스(seoul_opengov)의 결재문서 모양을 줄인 것 — 제목 행, 마스킹된
#: 표, 결재선 푸터의 ``부분공개(6)``.
_HEADER = "| 수신 | 내부결재 |"
_TITLE = "| 제목 | 가정의 날 초과근무 실시 |"
_BODY = "1. 초과근무일시 : ****************"
_TABLE = "| 대상자 | 제외사유 |\n| ******** | ************** |"
_FOOTER = (
    "| 주무관 채우석 총무팀장 김만태 시행 공원운영과-11509 "
    "( 2026. 7. 15. ) / 부분공개(6) |"
)


def _redacted_snapshot(
    *,
    footer: str = _FOOTER,
    body: str = _BODY,
) -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document_id="source-1",
        source="fixture",
        manifest_key="fixture/source-1",
        source_sha256="a" * 64,
        pages=(
            SourcePage(
                page_number=1,
                blocks=(
                    SourceTextBlock(block_id="source:b0", text=_HEADER),
                    SourceTextBlock(block_id="source:b1", text=_TITLE),
                    SourceTextBlock(block_id="source:b2", text=body),
                    SourceTextBlock(block_id="source:b3", text=_TABLE),
                    SourceTextBlock(block_id="source:b4", text=footer),
                ),
            ),
        ),
    )


def _fills(*values: str) -> MaskFillResponse:
    return MaskFillResponse(
        fills=tuple(
            MaskFill(mask_id=f"m{index}", value=value)
            for index, value in enumerate(values, start=1)
        ),
        rationale="초과근무 일시와 대상자·제외사유를 가상 값으로 채웠다.",
    )


def test_detects_footer_clause_and_every_mask_span():
    evidence = detect_redaction_evidence(_redacted_snapshot())

    assert evidence is not None
    assert evidence.clause_no is ClauseNumber.CLAUSE_6
    assert evidence.label_quote == "부분공개(6)"
    assert evidence.label_block_id == "source:b4"
    assert [span.mask_id for span in evidence.mask_spans] == ["m1", "m2", "m3"]
    assert [span.block_id for span in evidence.mask_spans] == [
        "source:b2",
        "source:b3",
        "source:b3",
    ]


def test_footer_without_masks_is_not_this_route():
    """비공개분이 붙임에 있는 문서 — 본문에 채울 자리가 없다.

    실측 229건 중 52건이 이 모양이다. 라벨만 보고 route를 잡으면 그 52건은
    채울 자리 없이 생성 단계에 들어가 빈 응답으로 실패한다.
    """

    snapshot = _redacted_snapshot(body="1. 초과근무일시 : 2026. 7. 15.")
    snapshot = SourceDocumentSnapshot(
        source_document_id=snapshot.source_document_id,
        source=snapshot.source,
        manifest_key=snapshot.manifest_key,
        source_sha256=snapshot.source_sha256,
        pages=(
            SourcePage(
                page_number=1,
                blocks=tuple(
                    block
                    for block in snapshot.pages[0].blocks
                    if block.block_id != "source:b3"
                ),
            ),
        ),
    )

    assert detect_redaction_evidence(snapshot) is None


def test_masks_without_footer_label_is_not_this_route():
    """어느 호인지 사람이 표시하지 않았으면 전제가 서지 않는다."""

    assert detect_redaction_evidence(_redacted_snapshot(footer="| 공개 |")) is None


def test_clause_outside_five_to_eight_is_not_this_route():
    snapshot = _redacted_snapshot(footer="| 부분공개(2) |")

    assert detect_redaction_evidence(snapshot) is None


def test_two_asterisks_are_a_mask_but_a_lone_asterisk_is_not():
    """경계가 2자인 이유 — 단독 ``*``는 각주, ``**``는 짧은 값이 가려진 자리다.

    실측 150건에서 단독 ``*``는 1,657건으로 각주 기호이고, 2연속 27건은 문맥이
    전부 ``직급 **``, ``- 연동확인사항 **`` 같은 마스킹이었다. 3자 기준일 때
    route를 타는 178건 중 8건이 ``**``를 남긴 채 생성됐다.
    """

    snapshot = _redacted_snapshot(body="1. 직급 ** 초과근무 인정* 별도 통보")
    evidence = detect_redaction_evidence(snapshot)

    assert evidence is not None
    assert [span.block_id for span in evidence.mask_spans] == [
        "source:b2",
        "source:b3",
        "source:b3",
    ]
    filled = apply_mask_fills(snapshot, evidence, _fills("6급", "김민수", "배우자 간병"))
    # 각주 ``*``는 건드리지 않는다.
    assert filled.blocks[2].render_text() == "1. 직급 6급 초과근무 인정* 별도 통보"


def test_lone_asterisk_alone_in_a_table_cell_is_a_mask():
    """실측(seoul_opengov-36534186) 훈련 실적 표에서 나온 자리다.

    ``| *** | * | *** | * |``에서 단독 ``*``는 각주가 아니라 한 자리 숫자가
    가려진 것이었다. 채우지 않으면 생성본에 ``*``가 그대로 남는다.
    """

    snapshot = _redacted_snapshot(body="| 수료자 | *** | * | 비고* |")
    evidence = detect_redaction_evidence(snapshot)
    assert evidence is not None

    body_spans = [s for s in evidence.mask_spans if s.block_id == "source:b2"]
    assert len(body_spans) == 2
    # 자리 순서가 문서 순서와 같아야 모델이 문맥에 맞는 값을 넣는다.
    assert [s.mask_id for s in body_spans] == ["m1", "m2"]

    filled = apply_mask_fills(
        snapshot,
        evidence,
        _fills("이민영", "3", "김민수", "배우자 간병"),
    )
    # 셀 안의 단독 ``*``는 채우고, 낱말에 붙은 각주 ``*``는 남긴다.
    assert filled.blocks[2].render_text() == "| 수료자 | 이민영 | 3 | 비고* |"


def test_prompt_never_reveals_the_masked_width():
    """폭을 주면 모델이 그 길이에 맞추고, 그건 원값 길이 추정이다."""

    snapshot = _redacted_snapshot()
    evidence = detect_redaction_evidence(snapshot)
    assert evidence is not None

    rendered = render_masked_source(snapshot, evidence)

    assert "[[m1]]" in rendered
    assert "[[m2]]" in rendered
    assert "***" not in rendered
    assert "1. 초과근무일시 : [[m1]]" in rendered
    assert "채워야 할 마스킹 자리 3개" in render_mask_slot_table(evidence)


def test_prompt_forbids_guessing_the_real_redacted_value():
    """이 route에서만 나오는 위험이다 — 원문에 진짜 값이 있었던 자리다.

    다른 route는 처음부터 없는 문서를 쓰므로 "실제 값을 알아맞힌다"가 성립하지
    않는다. 여기서는 성립하고, 그 방향으로 가면 실재하는 사람의 개인정보를
    복원하려 드는 셈이 된다. 문장 하나가 조용히 빠진 적이 있어 검사로 고정한다.
    """

    from rd2.source_generation.prompts import (
        render_mask_restoration_system_prompt,
    )

    prompt = render_mask_restoration_system_prompt(
        ClauseNumber.CLAUSE_6,
        label_quote="부분공개(6)",
        subclause_key=SubclauseKey.PERSONNEL_PII,
    )

    assert "알아맞히는 것이 아니다" in prompt
    assert "알아내려 해서도 안 된다" in prompt
    assert "완전히 가상의 값" in prompt
    assert "실재하는 사람·법인의 정보를 쓰지 않는다" in prompt
    assert "가려진 글자 수는 알려주지 않았다" in prompt


def test_fills_replace_only_the_masked_spans():
    snapshot = _redacted_snapshot()
    evidence = detect_redaction_evidence(snapshot)
    assert evidence is not None

    document = apply_mask_fills(
        snapshot,
        evidence,
        _fills("2026. 7. 15.(수) 18:00~20:00", "김민수", "배우자 간병"),
    )

    texts = [block.render_text() for block in document.blocks]
    assert texts[0] == _HEADER
    assert texts[1] == _TITLE
    assert texts[2] == "1. 초과근무일시 : 2026. 7. 15.(수) 18:00~20:00"
    assert texts[3] == "| 대상자 | 제외사유 |\n| 김민수 | 배우자 간병 |"
    assert texts[4] == _FOOTER
    assert [block.block_id for block in document.blocks] == [
        "source:b0",
        "source:b1",
        "source:b2",
        "source:b3",
        "source:b4",
    ]


def test_title_comes_from_the_source_not_the_model():
    assert derive_title(_redacted_snapshot()) == "가정의 날 초과근무 실시"


def test_missing_fill_is_rejected():
    snapshot = _redacted_snapshot()
    evidence = detect_redaction_evidence(snapshot)
    assert evidence is not None

    with pytest.raises(MaskRestorationError, match="m3"):
        apply_mask_fills(snapshot, evidence, _fills("일시", "김민수"))


def test_fill_that_reuses_a_mask_string_is_rejected():
    """마스킹을 마스킹으로 채우면 복원한 척만 한 문서가 남는다."""

    snapshot = _redacted_snapshot()
    evidence = detect_redaction_evidence(snapshot)
    assert evidence is not None

    with pytest.raises(MaskRestorationError, match="m2"):
        apply_mask_fills(snapshot, evidence, _fills("일시", "****", "사유"))


def test_subclause_resolution_stays_inside_the_footer_clause():
    evidence = detect_redaction_evidence(_redacted_snapshot())
    assert evidence is not None

    resolved = resolve_mask_restoration_subclause(
        evidence,
        candidates=(
            SubclauseKey.BID_CONTRACT,  # 제5호 — 푸터가 정한 호가 아니다
            SubclauseKey.PERSONNEL_PII,
        ),
    )

    assert resolved is SubclauseKey.PERSONNEL_PII


def test_no_candidate_in_the_footer_clause_returns_none():
    evidence = detect_redaction_evidence(_redacted_snapshot())
    assert evidence is not None

    assert (
        resolve_mask_restoration_subclause(
            evidence,
            candidates=(SubclauseKey.BID_CONTRACT, SubclauseKey.AUDIT_INSPECTION),
        )
        is None
    )


def _assessment_quoting(snapshot: SourceDocumentSnapshot):
    """공용 fixture의 evidence를 이 snapshot에 실재하는 인용문으로 바꾼다.

    ``execute_classification``은 인용문이 그 block에 글자 그대로 있는지 검사한다.
    공용 fixture는 자기 snapshot(민원 신청서)을 가리키므로 여기 결재문서에는
    없는 문장이고, 그대로 쓰면 route를 타보기도 전에 분류 단계에서 끝난다.
    """

    from rd2.source_generation.contracts import SourceAssessment

    payload = source_assessment().model_dump(mode="json")
    span = {"block_id": "source:b0", "quote": "내부결재"}
    payload["source_suitability"]["evidence_spans"] = [span]
    for slot in payload["available_slots"]:
        slot["evidence_span"] = dict(span)
    return SourceAssessment.model_validate(payload)


def _plan(**kwargs):
    snapshot = _redacted_snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None
    return snapshot, selection, build_generation_plan(
        assessment=kwargs.pop("assessment", source_assessment()),
        requested_target=kwargs.pop("requested_target", target()),
        snapshot=snapshot,
        selection=selection,
        **kwargs,
    )


def test_planner_prefers_mask_restoration_over_the_evidence_level_table():
    """부분공개 원문은 판별기 추정보다 먼저 걸린다."""

    _, _, plan = _plan()

    assert plan.generation_route is GenerationRoute.MASK_RESTORATION
    assert plan.final_target.clause_no is ClauseNumber.CLAUSE_6
    assert plan.final_target.subclause_key is SubclauseKey.PETITIONER_PII


def test_planner_lets_the_footer_clause_beat_the_requested_clause():
    """요청이 제5호라도 이 문서에서 만들 수 있는 것은 푸터의 제6호다."""

    _, _, plan = _plan(
        requested_target=target(
            clause=ClauseNumber.CLAUSE_5,
            subclause=SubclauseKey.BID_CONTRACT,
        ),
        assessment=source_assessment(
            primary_subclause=SubclauseKey.PERSONNEL_PII,
        ),
    )

    assert plan.generation_route is GenerationRoute.MASK_RESTORATION
    assert plan.final_target.clause_no is ClauseNumber.CLAUSE_6
    assert plan.final_target.subclause_key is SubclauseKey.PERSONNEL_PII
    assert plan.requested_target.clause_no is ClauseNumber.CLAUSE_5


def test_planner_falls_through_when_no_subclause_matches_the_footer_clause():
    """세부유형을 못 고르면 틀린 라벨을 만드느니 기존 route로 보낸다."""

    _, _, plan = _plan(
        requested_target=target(
            clause=ClauseNumber.CLAUSE_5,
            subclause=SubclauseKey.BID_CONTRACT,
        ),
        assessment=source_assessment(
            clause=ClauseNumber.CLAUSE_5,
            subclause=SubclauseKey.BID_CONTRACT,
            primary_subclause=SubclauseKey.AUDIT_INSPECTION,
        ),
    )

    assert plan.generation_route is GenerationRoute.ANCHORED


def test_execution_returns_the_source_document_with_masks_filled():
    snapshot, selection, plan = _plan()
    gateway = FakeGateway(
        [_fills("2026. 7. 15.(수) 18:00~20:00", "김민수", "배우자 간병")]
    )

    execution = execute_generation(
        assessment=source_assessment(),
        plan=plan,
        snapshot=snapshot,
        selection=selection,
        gateway=gateway,
        config=PipelineConfig(
            classifier_model="shared-model",
            generator_model="shared-model",
            validator_model="validator-model",
        ),
        prompt_bundle=None,
    )

    assert execution.failure is None
    artifact = execution.artifact
    assert artifact is not None
    body = artifact.generated_document.body_text
    assert "가정의 날 초과근무 실시" in body
    assert "김민수" in body
    assert "배우자 간병" in body
    assert "*" not in body
    # 근거는 판별기 span이 아니라 푸터다 — 무엇을 보고 route를 골랐는지가
    # provenance에 남아야 감사에서 재현된다.
    assert artifact.provenance.generation_route is GenerationRoute.MASK_RESTORATION
    assert "부분공개(6)" in artifact.provenance.reason_code
    assert artifact.provenance.validated_evidence_spans[0].block_id == "source:b4"


def test_open_verdict_still_lands_as_s_for_this_route():
    """S 근거가 검증기의 재판독이 아니라 원문에 남은 사람의 판단이기 때문이다.

    실무자가 그 자리를 제N호로 가렸고 우리는 그 자리만 채웠다. 다른 route라면
    O 판정이 곧 실패지만 여기서는 아니다.

    다만 검증기 판정 자체는 결과에 그대로 남아야 한다 — 채운 값이 약해 본문이
    요건에 못 미치는 문서를 나중에 걸러낼 유일한 단서다.
    """

    from rd2.schema.models import CsoClassification
    from rd2.source_generation.contracts import (
        SensitiveMonitorDecision,
        SensitivePipelineStatus,
    )
    from rd2.source_generation.pipeline import run_source_sensitive_pipeline

    snapshot = _redacted_snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None
    gateway = FakeGateway(
        [
            _assessment_quoting(snapshot),
            _fills("2026. 7. 15.", "김민수", "배우자 간병"),
            SensitiveMonitorDecision(
                classification=CsoClassification.O,
                rationale="이름과 짧은 사유뿐이라 개인정보로 보기 어렵다.",
            ),
        ]
    )

    run = run_source_sensitive_pipeline(
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=target(),
        gateway=gateway,
        config=PipelineConfig(
            classifier_model="shared-model",
            generator_model="shared-model",
            validator_model="validator-model",
            source_sensitive_mode=True,
        ),
    )

    assert run.status is SensitivePipelineStatus.ACCEPTED_S
    plan = run.final_result.generation_plan
    assert plan is not None
    assert plan.generation_route is GenerationRoute.MASK_RESTORATION
    # 라벨은 S지만 검증기가 O라고 본 사실은 지우지 않는다.
    assert (
        run.final_result.consistency_assessment.classification
        is CsoClassification.O
    )
    # 재시도로 돌지 않는다 — 다시 물어도 같은 답이고 호출만 늘어난다.
    assert [c["response_model"].__name__ for c in gateway.calls] == [
        "SourceAssessment",
        "MaskFillResponse",
        "SensitiveMonitorDecision",
    ]


def test_execution_rejects_a_response_that_skips_a_mask():
    snapshot, selection, plan = _plan()
    gateway = FakeGateway([_fills("일시", "김민수")])

    execution = execute_generation(
        assessment=source_assessment(),
        plan=plan,
        snapshot=snapshot,
        selection=selection,
        gateway=gateway,
        config=PipelineConfig(
            classifier_model="shared-model",
            generator_model="shared-model",
            validator_model="validator-model",
        ),
        prompt_bundle=None,
    )

    assert execution.failure is not None
    assert execution.failure.stage is FailureStage.GENERATION
    assert "m3" in execution.failure.message
