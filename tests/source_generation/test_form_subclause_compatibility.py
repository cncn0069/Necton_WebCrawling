from __future__ import annotations

import pytest

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import FailureCode, SourceEvidenceLevel
from rd2.source_generation.document_form_compatibility import (
    FormSubclauseCompatibility,
    compatibility_counts,
    form_subclause_compatibility,
    render_form_subclause_bridge_guidance,
)
from rd2.source_generation.document_select import prepare_document_selection
from rd2.source_generation.pipeline import (
    GenerationPlanningError,
    build_generation_plan,
)

from .v2_fixtures import snapshot, source_assessment, target


def _selection():
    selection = prepare_document_selection(snapshot()).selection
    assert selection is not None
    return selection


def test_compatibility_policy_classifies_all_272_pairs():
    assert compatibility_counts() == {
        FormSubclauseCompatibility.NATIVE: 87,
        FormSubclauseCompatibility.BRIDGE: 121,
        FormSubclauseCompatibility.CONFLICT: 64,
    }


@pytest.mark.parametrize(
    ("document_form", "subclause", "expected"),
    (
        (
            DocumentForm.MEETING_MINUTES,
            SubclauseKey.DECISION_REVIEW,
            FormSubclauseCompatibility.NATIVE,
        ),
        (
            DocumentForm.OFFICIAL_LETTER,
            SubclauseKey.DECISION_REVIEW,
            FormSubclauseCompatibility.NATIVE,
        ),
        (
            DocumentForm.OFFICIAL_LETTER,
            SubclauseKey.PERSONNEL_PII,
            FormSubclauseCompatibility.BRIDGE,
        ),
        (
            DocumentForm.POLICY_MATERIAL,
            SubclauseKey.PERSONNEL_PII,
            FormSubclauseCompatibility.CONFLICT,
        ),
        (
            DocumentForm.REPORT,
            SubclauseKey.SECURITY_DIAGNOSIS,
            FormSubclauseCompatibility.CONFLICT,
        ),
    ),
)
def test_representative_compatibility_levels(document_form, subclause, expected):
    assert form_subclause_compatibility(document_form, subclause) is expected


def test_bridge_guidance_requires_both_core_form_and_target_content():
    guidance = render_form_subclause_bridge_guidance(
        DocumentForm.OFFICIAL_LETTER,
        SubclauseKey.PERSONNEL_PII,
    )

    assert "표제부만으로 형식을 주장하지 말고" in guidance
    assert "실제 내용을 먼저 전달" in guidance
    assert "식별 가능한 사람과 보호되는 개인속성" in guidance
    assert not render_form_subclause_bridge_guidance(
        DocumentForm.MEETING_MINUTES,
        SubclauseKey.PERSONNEL_PII,
    )


def test_planner_remaps_a_hard_conflict_to_first_non_conflicting_candidate():
    assessment = source_assessment(
        document_form=DocumentForm.POLICY_MATERIAL,
        subclause=SubclauseKey.PETITIONER_PII,
        primary_subclause=SubclauseKey.PETITIONER_PII,
        compatible_subclauses=(SubclauseKey.BUSINESS_STRATEGY,),
    )

    plan = build_generation_plan(
        assessment=assessment,
        requested_target=target(
            clause=ClauseNumber.CLAUSE_6,
            subclause=SubclauseKey.PETITIONER_PII,
        ),
        snapshot=snapshot(),
        selection=_selection(),
        sensitive_seed="민원인의 개인 연락처",
    )

    assert plan.final_target.clause_no == ClauseNumber.CLAUSE_7
    assert plan.final_target.subclause_key == SubclauseKey.BUSINESS_STRATEGY


def test_planner_rejects_a_hard_conflict_without_a_viable_candidate():
    assessment = source_assessment(
        document_form=DocumentForm.POLICY_MATERIAL,
        subclause=SubclauseKey.PETITIONER_PII,
        primary_subclause=SubclauseKey.PETITIONER_PII,
        compatible_subclauses=(SubclauseKey.SUBJECT_PII,),
        evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
    )

    with pytest.raises(GenerationPlanningError) as exc_info:
        build_generation_plan(
            assessment=assessment,
            requested_target=target(
                clause=ClauseNumber.CLAUSE_6,
                subclause=SubclauseKey.PETITIONER_PII,
            ),
            snapshot=snapshot(),
            selection=_selection(),
            sensitive_seed="민원인의 개인 연락처",
        )

    assert exc_info.value.code == FailureCode.SOURCE_INCOMPATIBLE
    assert "no source-compatible subclause" in str(exc_info.value)


def test_source_aligned_hard_conflict_is_rejected_instead_of_relabelled():
    assessment = source_assessment(
        classification=CsoClassification.S,
        document_form=DocumentForm.PRESS_RELEASE,
        clause=ClauseNumber.CLAUSE_6,
        subclause=SubclauseKey.PERSONNEL_PII,
    )

    with pytest.raises(GenerationPlanningError) as exc_info:
        build_generation_plan(
            assessment=assessment,
            requested_target=target(),
            snapshot=snapshot(),
            selection=_selection(),
        )

    assert exc_info.value.code == FailureCode.SOURCE_INCOMPATIBLE
    assert "source legal target conflicts" in str(exc_info.value)
