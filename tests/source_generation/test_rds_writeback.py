"""승인된 생성물을 documents 행으로 옮기는 규칙."""

from __future__ import annotations

from datetime import date

import pytest

from rd2.schema.models import CsoClassification, DisclosureStatus
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    GeneratedDocumentIR,
    GenerationPlan,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    SensitivePipelineStatus,
    SensitiveVerdict,
    TargetClassification,
)
from rd2.source_generation.rds_writeback import (
    SourceRow,
    build_generated_document,
    generated_source_url,
    non_disclosure_reason,
    should_commit,
)


def _target(
    *,
    clause: ClauseNumber = ClauseNumber.CLAUSE_6,
    subclause: SubclauseKey | None = SubclauseKey.PETITIONER_PII,
) -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=clause,
        subclause_key=subclause,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def _plan(
    *,
    route: GenerationRoute = GenerationRoute.SOURCE_ALIGNED,
    target: GenerationTarget | None = None,
) -> GenerationPlan:
    final_target = target or _target()
    return GenerationPlan(
        requested_target=final_target,
        final_target=final_target,
        generation_route=route,
        source_assessment_sha256="0" * 64,
        source_sha256="a" * 64,
        selection_sha256="b" * 64,
        planner_policy_sha256="c" * 64,
    )


def _document() -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        title="인사위원회 심의 결과 통보",
        blocks=(
            {
                "block_id": "b0",
                "kind": "paragraph",
                "text": "심의 대상자 김민준(생년월일 1989-04-02)에 대한 결과를 통보합니다.",
            },
        ),
    )


def _row(**overrides) -> SourceRow:
    defaults = dict(
        id=18752,
        source="seoul_opengov",
        doc_type="official_document",
        title="원문 제목",
        ordering_agency="서울특별시",
        department="품질지도과",
        production_date=date(2026, 7, 20),
        subject_category="일반행정",
        body_file_path="seoul_opengov/official_document/1-500/18752_원문.hwpx",
    )
    defaults.update(overrides)
    return SourceRow(**defaults)


def test_generated_row_carries_the_final_target_not_the_requested_one():
    """계획기가 목표를 바꿔도 라벨은 실제 생성된 것을 따라간다."""

    plan = _plan(
        target=_target(
            clause=ClauseNumber.CLAUSE_5,
            subclause=SubclauseKey.AUDIT_INSPECTION,
        )
    )

    doc = build_generated_document(
        document=_document(),
        plan=plan,
        source_document_id="seoul_opengov-18752",
        source_row=_row(),
    )

    assert doc.cso_classification == CsoClassification.S
    # ClauseNumber 값이 이미 "5"라 숫자만 규약과 변환 없이 맞는다.
    assert doc.cso_sub_clause == "5"


def test_generated_row_is_closed_and_keeps_the_subclause_in_the_reason():
    """테이블에 세부조항 컬럼이 없어 비공개사유가 그걸 남길 유일한 자리다."""

    doc = build_generated_document(
        document=_document(),
        plan=_plan(),
        source_document_id="seoul_opengov-18752",
        source_row=_row(),
    )

    assert doc.disclosure_status == DisclosureStatus.CLOSED
    assert "제6호" in doc.non_disclosure_reason
    assert "petitioner_pii" in doc.non_disclosure_reason


def test_generated_row_inherits_source_metadata_and_marks_its_origin():
    doc = build_generated_document(
        document=_document(),
        plan=_plan(),
        source_document_id="seoul_opengov-18752",
        source_row=_row(),
    )

    assert doc.ordering_agency == "서울특별시"
    assert doc.department == "품질지도과"
    assert doc.production_date == date(2026, 7, 20)
    assert doc.doc_type == "official_document"
    # is_synthetic 컬럼이 없어졌으므로 source 접두사가 유일한 구분자다.
    assert doc.source == "gen_seoul_opengov"
    assert doc.is_synthetic is True


def test_generated_row_leaves_the_body_file_path_for_the_template_backfill():
    doc = build_generated_document(
        document=_document(),
        plan=_plan(),
        source_document_id="seoul_opengov-18752",
        source_row=_row(),
    )

    assert doc.body_file_path is None
    assert doc.body_text.startswith("심의 대상자")


def test_source_url_is_stable_so_reruns_do_not_duplicate_rows():
    """dedup_key가 흔들리면 나중에 PDF 경로를 백필할 행을 찾을 수 없다."""

    plan = _plan()
    first = generated_source_url("seoul_opengov-18752", plan)
    second = generated_source_url("seoul_opengov-18752", plan)

    assert first == second
    assert "18752" in first
    # 다른 세부조항으로 생성하면 별도 행이 된다.
    other = generated_source_url(
        "seoul_opengov-18752",
        _plan(target=_target(subclause=SubclauseKey.PERSONNEL_PII)),
    )
    assert other != first


def test_row_without_source_metadata_falls_back_to_the_document_form():
    doc = build_generated_document(
        document=_document(),
        plan=_plan(),
        source_document_id="seoul_opengov-abc",
        source_row=None,
        fallback_source="seoul_opengov",
        document_form=DocumentForm.OFFICIAL_LETTER,
    )

    assert doc.doc_type == "official_letter"
    assert doc.ordering_agency == "미상"


def test_missing_origin_source_is_rejected():
    with pytest.raises(ValueError):
        build_generated_document(
            document=_document(),
            plan=_plan(),
            source_document_id="x-1",
            source_row=None,
        )


@pytest.mark.parametrize(
    "status",
    [
        SensitivePipelineStatus.HARD_CASE_REVIEW,
        SensitivePipelineStatus.EXCLUDED_AFTER_RETRY,
        SensitivePipelineStatus.PIPELINE_FAILED,
    ],
)
def test_only_approved_documents_are_committed(status):
    assert not should_commit(status=status, plan=_plan(), assessment=None)


def test_mask_restoration_with_an_o_verdict_is_held_back(monkeypatch):
    """이 route는 검증기가 O를 내도 accepted_s로 통과한다 — 그걸 그대로
    넣으면 근거가 약한 문서가 S 학습셋에 섞인다."""

    class _Assessment:
        sensitivity_verdict = SensitiveVerdict.ASSESSED_O

    plan = _plan(route=GenerationRoute.MASK_RESTORATION)

    assert not should_commit(
        status=SensitivePipelineStatus.ACCEPTED_S,
        plan=plan,
        assessment=_Assessment(),
    )
    assert should_commit(
        status=SensitivePipelineStatus.ACCEPTED_S,
        plan=plan,
        assessment=_Assessment(),
        include_weak_mask_restoration=True,
    )


def test_mask_restoration_with_an_s_verdict_is_committed():
    class _Assessment:
        sensitivity_verdict = SensitiveVerdict.ACCEPTED_S

    assert should_commit(
        status=SensitivePipelineStatus.ACCEPTED_S,
        plan=_plan(route=GenerationRoute.MASK_RESTORATION),
        assessment=_Assessment(),
    )


def test_administrative_status_only_target_still_has_a_reason():
    """Document 계약이 비공개 문서에 사유를 요구한다."""

    from rd2.administrative_status import AdminStatus

    plan = _plan(
        target=GenerationTarget(
            classification=TargetClassification.S,
            administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )
    )

    reason = non_disclosure_reason(plan)

    assert reason
    assert "제5~8호" in reason
