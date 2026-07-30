from __future__ import annotations

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
    TableBlock,
)
from rd2.generators.pdf_sensitive_evidence import (
    verify_sensitive_evidence_pages,
)


def _assessment(block_id: str, link: str) -> SensitiveConsistencyAssessment:
    assertion = SensitiveAssertion(
        subject_role=SensitiveSubjectRole.PETITIONER,
        subject_span=EvidenceSpan(block_id=block_id, quote="민원인 김민서"),
        attribute_kind=SensitiveAttributeKind.PHONE,
        value_span=EvidenceSpan(block_id=block_id, quote="010-4827-1936"),
        link_span=EvidenceSpan(block_id=block_id, quote=link),
        identification_strength=IdentificationStrength.DIRECT,
    )
    return SensitiveConsistencyAssessment(
        document_form=DocumentForm.REPLY_NOTICE,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=SubclauseKey.PETITIONER_PII,
        evidence_spans=(assertion.link_span,),
        rationale="민원인과 개인 연락처가 연결된다.",
        sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        assertions=(assertion,),
    )


def test_paragraph_relation_must_survive_in_pdf_text():
    link = "민원인 김민서의 연락처는 010-4827-1936이다."
    document = GeneratedDocumentIR(
        title="민원 처리",
        blocks=(ParagraphBlock(block_id="g:b0", text=link),),
    )

    checks = verify_sensitive_evidence_pages(
        assessment=_assessment("g:b0", link),
        document=document,
        page_texts=(f"문서 제목\n{link}",),
    )

    assert checks[0]["passed"] is True
    assert checks[0]["matching_pages"] == [1]
    assert checks[0]["link_pages"] == [1]


def test_missing_value_fails_pdf_preservation():
    link = "민원인 김민서의 연락처는 010-4827-1936이다."
    document = GeneratedDocumentIR(
        title="민원 처리",
        blocks=(ParagraphBlock(block_id="g:b0", text=link),),
    )

    checks = verify_sensitive_evidence_pages(
        assessment=_assessment("g:b0", link),
        document=document,
        page_texts=("민원인 김민서의 연락처가 기재되어 있다.",),
    )

    assert checks[0]["passed"] is False


def test_table_relation_allows_pdf_cell_spacing_but_requires_same_page():
    link = "민원인 김민서\t010-4827-1936"
    document = GeneratedDocumentIR(
        title="민원 처리",
        blocks=(
            TableBlock(
                block_id="g:t0",
                columns=("민원인", "연락처"),
                rows=(("민원인 김민서", "010-4827-1936"),),
            ),
        ),
    )

    checks = verify_sensitive_evidence_pages(
        assessment=_assessment("g:t0", link),
        document=document,
        page_texts=("민원인 김민서\n다른 셀\n010-4827-1936",),
    )

    assert checks[0]["passed"] is True
    assert checks[0]["link_pages"] == []
