from __future__ import annotations

import pytest

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    EvidenceSpan,
    GeneratedDocumentIR,
    IdentificationStrength,
    ParagraphBlock,
    SensitiveAssertion,
    SensitiveAttributeKind,
    SensitiveConsistencyAssessment,
    SensitiveSubjectRole,
    SensitiveVerdict,
)
from rd2.source_generation.sensitive_policy import (
    validate_sensitive_assessment,
)


def _span(quote: str, block_id: str = "g:b0") -> EvidenceSpan:
    return EvidenceSpan(block_id=block_id, quote=quote)


def _assertion(
    *,
    subject: str,
    value: str,
    link: str,
    role: SensitiveSubjectRole = SensitiveSubjectRole.PETITIONER,
    attribute: SensitiveAttributeKind = SensitiveAttributeKind.PHONE,
    strength: IdentificationStrength = IdentificationStrength.DIRECT,
) -> SensitiveAssertion:
    return SensitiveAssertion(
        subject_role=role,
        subject_span=_span(subject),
        attribute_kind=attribute,
        value_span=_span(value),
        link_span=_span(link),
        identification_strength=strength,
    )


def _accepted(assertion: SensitiveAssertion) -> SensitiveConsistencyAssessment:
    return SensitiveConsistencyAssessment(
        document_form=DocumentForm.REPLY_NOTICE,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=SubclauseKey.PETITIONER_PII,
        evidence_spans=(assertion.link_span,),
        rationale="식별 가능한 외부인과 구체 개인정보가 연결된다.",
        sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        assertions=(assertion,),
    )


def _o() -> SensitiveConsistencyAssessment:
    return SensitiveConsistencyAssessment(
        document_form=DocumentForm.REPLY_NOTICE,
        classification=CsoClassification.O,
        rationale="개인과 연결된 구체값이 없다.",
        sensitivity_verdict=SensitiveVerdict.ASSESSED_O,
    )


def _hard(assertion: SensitiveAssertion) -> SensitiveConsistencyAssessment:
    return SensitiveConsistencyAssessment(
        document_form=DocumentForm.REPLY_NOTICE,
        classification=CsoClassification.O,
        rationale="마스킹 또는 간접 식별이라 사람 검수가 필요하다.",
        sensitivity_verdict=SensitiveVerdict.HARD_CASE_REVIEW,
        assertions=(assertion,),
    )


def test_generic_pii_field_description_cannot_be_accepted_as_s():
    assertion = _assertion(
        subject="지원자 김민서",
        value="지원자의 전화번호 및 이메일 주소",
        link="지원자 김민서의 지원자의 전화번호 및 이메일 주소를 수집한다.",
        role=SensitiveSubjectRole.JOB_APPLICANT,
    )

    with pytest.raises(ValueError, match="lacks a concrete value"):
        validate_sensitive_assessment(_accepted(assertion))


def test_name_only_document_is_o():
    validate_sensitive_assessment(_o())


def test_external_person_linked_to_realistic_phone_is_s():
    assertion = _assertion(
        subject="민원인 김민서",
        value="010-4827-1936",
        link="민원인 김민서의 연락처는 010-4827-1936이다.",
    )
    validate_sensitive_assessment(_accepted(assertion))


def test_external_person_linked_to_health_or_welfare_fact_is_s():
    assertion = _assertion(
        subject="신청인 김민서",
        value="장애인연금 신청 및 진단등급 3급",
        link="신청인 김민서는 장애인연금 신청 및 진단등급 3급으로 심사되었다.",
        role=SensitiveSubjectRole.BENEFICIARY,
        attribute=SensitiveAttributeKind.WELFARE_CIRCUMSTANCE,
    )
    validate_sensitive_assessment(_accepted(assertion))


@pytest.mark.parametrize(
    "attribute,value",
    [
        (SensitiveAttributeKind.BUSINESS_CONTACT, "02-2133-1234"),
        (SensitiveAttributeKind.PERSONNEL_EVALUATION, "근무평정 A등급"),
        (SensitiveAttributeKind.DISCIPLINE, "감봉 1개월"),
    ],
)
def test_employee_business_and_personnel_policy_fields_are_o(attribute, value):
    assertion = _assertion(
        subject="담당자 김민서",
        value=value,
        link=f"담당자 김민서의 정보는 {value}이다.",
        role=SensitiveSubjectRole.EMPLOYEE,
        attribute=attribute,
    )

    with pytest.raises(ValueError, match="is O"):
        validate_sensitive_assessment(_accepted(assertion))


def test_employee_national_id_is_s():
    assertion = _assertion(
        subject="직원 김민서",
        value="900101-2234567",
        link="직원 김민서의 주민등록번호는 900101-2234567이다.",
        role=SensitiveSubjectRole.EMPLOYEE,
        attribute=SensitiveAttributeKind.NATIONAL_ID,
    )
    validate_sensitive_assessment(_accepted(assertion))


def test_aggregate_without_person_rows_is_o():
    validate_sensitive_assessment(_o())


def test_masked_identity_is_hard_case():
    assertion = _assertion(
        subject="신청인 김○○",
        value="010-****-1234",
        link="신청인 김○○의 연락처는 010-****-1234이다.",
        strength=IdentificationStrength.MASKED,
    )
    validate_sensitive_assessment(_hard(assertion))


def test_case_id_with_personal_fact_is_hard_case():
    assertion = _assertion(
        subject="신청번호 2026-1842",
        value="장애인연금 신청 및 진단등급 3급",
        link="신청번호 2026-1842는 장애인연금 신청 및 진단등급 3급 건이다.",
        role=SensitiveSubjectRole.CASE_SUBJECT,
        attribute=SensitiveAttributeKind.WELFARE_CIRCUMSTANCE,
        strength=IdentificationStrength.INDIRECT,
    )
    validate_sensitive_assessment(_hard(assertion))


def test_case_id_with_direct_identity_and_personal_fact_is_s():
    assertion = _assertion(
        subject="신청번호 2026-1842의 신청인 김민서",
        value="010-4827-1936",
        link=(
            "신청번호 2026-1842의 신청인 김민서 연락처는 "
            "010-4827-1936이다."
        ),
        role=SensitiveSubjectRole.APPLICANT,
    )
    validate_sensitive_assessment(_accepted(assertion))


def test_assertion_must_preserve_subject_value_link_in_one_document_block():
    link = "민원인 김민서\t010-4827-1936"
    assertion = _assertion(
        subject="민원인 김민서",
        value="010-4827-1936",
        link=link,
    )
    assessment = _accepted(assertion)
    document = GeneratedDocumentIR(
        title="민원 처리",
        blocks=(ParagraphBlock(block_id="g:b0", text=link),),
    )

    assessment.validate_against_document(document)
    validate_sensitive_assessment(assessment)
