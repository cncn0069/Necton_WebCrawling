from __future__ import annotations

import pytest
from pydantic import ValidationError

from rd2.source_generation.classification_taxonomy import (
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import SourceEvidenceLevel
from rd2.source_generation.document_form_compatibility import (
    FormSubclauseCompatibility,
    compatibility_counts,
    form_subclause_compatibility,
    render_form_subclause_bridge_guidance,
)
from .v2_fixtures import source_assessment


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


def test_conflicting_primary_subclause_is_rejected_at_the_assessment_contract():
    """"갈아탈 후보가 없다"는 상태를 계약이 애초에 못 만들게 한다.

    이전에는 판별기가 형식과 충돌하는 1순위를 낼 수 있었고, 후보 목록까지 전부
    충돌하면 계획 단계에서 ``SOURCE_INCOMPATIBLE``로 끝났다. 1순위를 그대로
    생성 목표로 쓰기로 한 이상(폴백 없음) 그 검사는 판별 단계로 올라가야 한다 —
    계획까지 가서 버리면 판별 호출 하나가 통째로 낭비된다.
    """

    with pytest.raises(ValidationError) as exc_info:
        source_assessment(
            document_form=DocumentForm.POLICY_MATERIAL,
            subclause=SubclauseKey.PETITIONER_PII,
            primary_subclause=SubclauseKey.PETITIONER_PII,
            evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
        )

    assert "conflicts with document form policy_material" in str(exc_info.value)
