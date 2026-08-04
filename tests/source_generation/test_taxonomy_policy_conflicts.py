from __future__ import annotations

import pytest

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    DOCUMENT_FORM_DEFINITIONS,
    ClauseNumber,
    DocumentForm,
    SUBCLAUSE_DEFINITIONS,
    SubclauseKey,
    TAXONOMY_VERSION,
    render_clause_6_validator_guidance,
    render_document_form_guidance,
    render_generation_detail_guidance,
)
from rd2.source_generation.contracts import (
    EvidenceSpan,
    IdentificationStrength,
    SensitiveAssertion,
    SensitiveAttributeKind,
    SensitiveConsistencyAssessment,
    SensitiveSubjectRole,
    SensitiveVerdict,
)
from rd2.source_generation.sensitive_policy import (
    SENSITIVE_POLICY_VERSION,
    render_sensitive_policy_guidance,
    validate_sensitive_assessment,
)


def _employee_assessment(
    attribute: SensitiveAttributeKind,
    value: str,
    *,
    subclause: SubclauseKey = SubclauseKey.PERSONNEL_PII,
) -> SensitiveConsistencyAssessment:
    link = f"김민서 주무관의 개인별 정보는 {value}이다."
    assertion = SensitiveAssertion(
        subject_role=SensitiveSubjectRole.EMPLOYEE,
        subject_span=EvidenceSpan(block_id="g:b0", quote="김민서 주무관"),
        attribute_kind=attribute,
        value_span=EvidenceSpan(block_id="g:b0", quote=value),
        link_span=EvidenceSpan(block_id="g:b0", quote=link),
        identification_strength=IdentificationStrength.DIRECT,
    )
    return SensitiveConsistencyAssessment(
        document_form=DocumentForm.PERSONNEL_MATERIAL,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=subclause,
        evidence_spans=(assertion.link_span,),
        rationale="식별 가능한 직원과 개인별 인사정보가 직접 연결된다.",
        sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        assertions=(assertion,),
    )


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        (SensitiveAttributeKind.PERSONNEL_EVALUATION, "근무평정 C등급"),
        (SensitiveAttributeKind.DISCIPLINE, "감봉 1개월"),
    ),
)
def test_identified_employee_evaluation_and_discipline_are_s(attribute, value):
    validate_sensitive_assessment(_employee_assessment(attribute, value))


@pytest.mark.parametrize(
    "attribute,value",
    (
        (SensitiveAttributeKind.PERSONNEL_EVALUATION, "근무평정 C등급"),
        (SensitiveAttributeKind.DISCIPLINE, "감봉 1개월"),
    ),
)
def test_employee_evaluation_and_discipline_cannot_be_labelled_subject_pii(
    attribute, value
):
    with pytest.raises(ValueError, match="requires subclause personnel_pii"):
        validate_sensitive_assessment(
            _employee_assessment(
                attribute,
                value,
                subclause=SubclauseKey.SUBJECT_PII,
            )
        )


def test_investigation_subject_fact_cannot_be_labelled_personnel_pii():
    link = "조사대상자 김민서가 계약서 원본을 반출했다는 구체적 혐의가 확인되었다."
    assertion = SensitiveAssertion(
        subject_role=SensitiveSubjectRole.INVESTIGATION_SUBJECT,
        subject_span=EvidenceSpan(block_id="g:b0", quote="조사대상자 김민서"),
        attribute_kind=SensitiveAttributeKind.OTHER_PERSONAL_FACT,
        value_span=EvidenceSpan(
            block_id="g:b0", quote="계약서 원본을 반출했다는 구체적 혐의"
        ),
        link_span=EvidenceSpan(block_id="g:b0", quote=link),
        identification_strength=IdentificationStrength.DIRECT,
    )
    assessment = SensitiveConsistencyAssessment(
        document_form=DocumentForm.INVESTIGATION_REPORT,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=SubclauseKey.PERSONNEL_PII,
        evidence_spans=(assertion.link_span,),
        rationale="식별 가능한 조사대상자와 구체 혐의가 연결된다.",
        sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        assertions=(assertion,),
    )

    with pytest.raises(ValueError, match="requires subclause subject_pii"):
        validate_sensitive_assessment(assessment)


def test_employee_role_does_not_override_a_concrete_allegation():
    link = "김민서 주무관이 계약서 원본을 반출했다는 구체적 위반 혐의가 확인되었다."
    assertion = SensitiveAssertion(
        subject_role=SensitiveSubjectRole.EMPLOYEE,
        subject_span=EvidenceSpan(block_id="g:b0", quote="김민서 주무관"),
        attribute_kind=SensitiveAttributeKind.OTHER_PERSONAL_FACT,
        value_span=EvidenceSpan(
            block_id="g:b0", quote="계약서 원본을 반출했다는 구체적 위반 혐의"
        ),
        link_span=EvidenceSpan(block_id="g:b0", quote=link),
        identification_strength=IdentificationStrength.DIRECT,
    )
    assessment = SensitiveConsistencyAssessment(
        document_form=DocumentForm.INVESTIGATION_REPORT,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=SubclauseKey.PERSONNEL_PII,
        evidence_spans=(assertion.link_span,),
        rationale="직원 신원에 구체적 위반 혐의가 연결된다.",
        sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        assertions=(assertion,),
    )

    with pytest.raises(ValueError, match="requires subclause subject_pii"):
        validate_sensitive_assessment(assessment)


def test_clause_6_taxonomy_distinguishes_personnel_and_subject_pii():
    personnel = SUBCLAUSE_DEFINITIONS[SubclauseKey.PERSONNEL_PII]
    subject = SUBCLAUSE_DEFINITIONS[SubclauseKey.SUBJECT_PII]

    assert any("근무평정" in item and "징계처분" in item for item in personnel.includes)
    assert any("이름·부서·직위·업무 연락처만" in item for item in personnel.excludes)
    assert "구체적인 위반 혐의" in subject.definition
    assert any("personnel_pii" in item for item in subject.excludes)


def test_clause_6_validator_guidance_uses_only_four_pii_subclauses():
    guidance = render_clause_6_validator_guidance()
    expected = {
        SubclauseKey.PERSONNEL_PII,
        SubclauseKey.PETITIONER_PII,
        SubclauseKey.SUBJECT_PII,
        SubclauseKey.WELFARE_PII,
    }

    for subclause in expected:
        assert f"- {subclause.value} [제6호]" in guidance
    for subclause in set(SubclauseKey) - expected:
        assert f"- {subclause.value} [" not in guidance
    assert "근무평정·징계처분이면 personnel_pii" in guidance
    assert "이름·부서·직위·업무 연락처만 있으면 제6호 S 근거가 아니다" in guidance


def test_sensitive_policy_guidance_matches_clause_6_taxonomy():
    guidance = render_sensitive_policy_guidance()

    assert SENSITIVE_POLICY_VERSION == "source-sensitive-policy-2026-08-01-v3"
    assert "직원의 이름·부서·직위·업무 연락처만 있으면 O" in guidance
    assert "개인별 근무평정 또는 징계처분" in guidance
    assert "인사·채용 개인정보의 S" in guidance
    assert "구체적인 위반 혐의·진술·조사내용" in guidance
    assert "조사대상자 개인정보의 S" in guidance
    assert "인사평가, 징계내역은 이 연구 정책에서 O" not in guidance


def test_all_document_forms_separate_classification_and_generation_fields():
    assert TAXONOMY_VERSION == "source-generation-taxonomy-v3"
    assert set(DOCUMENT_FORM_DEFINITIONS) == set(DocumentForm)

    for form, definition in DOCUMENT_FORM_DEFINITIONS.items():
        assert definition.includes, form
        assert definition.required_elements, form
        assert definition.variant_patterns, form

        generation = render_generation_detail_guidance(form)
        for item in definition.required_elements + definition.variant_patterns:
            assert item in generation
        for classifier_only_item in definition.includes:
            assert classifier_only_item not in generation


def test_other_means_outside_defined_forms_and_allows_unknown_form_label():
    other = DOCUMENT_FORM_DEFINITIONS[DocumentForm.OTHER]
    guidance = render_document_form_guidance()

    assert "정의된 16개 전문 형식" in other.definition
    assert any("신청서·접수대장·확인서" in item for item in other.includes)
    assert "other_document_form" in guidance
    assert "형식 불명" in guidance
    assert all("other_document_form에" not in item for item in other.required_elements)


def test_inspection_report_boundary_turns_on_target_not_the_word_점검():
    """`점검`이라는 낱말이 아니라 점검 대상이 두 형식을 가른다.

    실측(2026-08-03 배치): `2026년도 3차 복무감사결과`, `공직기강 특별점검
    감사결과` 등 15건이 inspection_report로 분류돼 계획 단계에서 전부 끝났다
    (inspection_report × audit_inspection은 CONFLICT다). 같은 배치에서 제목이
    거의 같은 `2026년도 복무감사결과`는 audit_material로 가서 통과했다.
    사람을 대상으로 한 복무·기강 점검은 감사자료다.
    """

    audit = DOCUMENT_FORM_DEFINITIONS[DocumentForm.AUDIT_MATERIAL]
    inspection = DOCUMENT_FORM_DEFINITIONS[DocumentForm.INSPECTION_REPORT]

    # 감사자료가 복무·기강 점검을 자기 것으로 명시한다.
    assert any("복무" in item and "기강" in item for item in audit.includes)
    assert "점검" in audit.definition

    # 점검보고서는 대상이 사물일 때로 한정하고, 사람 대상은 넘긴다.
    assert "사물" in inspection.definition
    assert any(
        "복무" in item and "audit_material" in item for item in inspection.excludes
    )

    # 두 규칙이 판별기가 실제로 읽는 지침에 함께 나타나야 의미가 있다.
    guidance = render_document_form_guidance()
    assert "사람의 복무·기강을 대상으로 한 점검 결과" in guidance
    assert "점검 **대상이 사물**일 때만" in guidance
