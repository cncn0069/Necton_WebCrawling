from __future__ import annotations

from typing import Any

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    ConsistencyAssessment,
    EvidenceSpan,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationTarget,
    IdentificationStrength,
    ParagraphBlock,
    SensitiveAssertion,
    SensitiveAttributeKind,
    SensitiveConsistencyAssessment,
    SensitiveSubjectRole,
    SensitiveVerdict,
    SourceActorRole,
    SourceAssessment,
    SourceClassification,
    SourceDocumentSnapshot,
    SourceEvidenceLevel,
    SourcePage,
    SourceSlot,
    SourceSlotKind,
    SourceSuitability,
    SourceTextBlock,
    TargetClassification,
)
from rd2.source_generation.pipeline import StructuredCall


def snapshot() -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document_id="source-1",
        source="fixture",
        manifest_key="fixture/source-1",
        source_sha256="a" * 64,
        pages=(
            SourcePage(
                page_number=1,
                blocks=(
                    SourceTextBlock(
                        block_id="source:b0",
                        text=(
                            "민원 신청서에는 신청인 연락처 기재란과 처리 의견란이 "
                            "있으며 공개 양식으로 운영한다."
                        ),
                    ),
                ),
            ),
        ),
    )


def target(
    *,
    clause: ClauseNumber = ClauseNumber.CLAUSE_6,
    subclause: SubclauseKey = SubclauseKey.PETITIONER_PII,
) -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=clause,
        subclause_key=subclause,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def source_assessment(
    *,
    classification: CsoClassification = CsoClassification.O,
    evidence_level: SourceEvidenceLevel = (
        SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY
    ),
    clause: ClauseNumber = ClauseNumber.CLAUSE_6,
    subclause: SubclauseKey = SubclauseKey.PETITIONER_PII,
    primary_subclause: SubclauseKey | None = None,
    compatible_subclauses: tuple[SubclauseKey, ...] = (),
    role: SourceActorRole = SourceActorRole.APPLICANT,
    document_form: DocumentForm = DocumentForm.OFFICIAL_LETTER,
    other_document_form: str | None = None,
) -> SourceAssessment:
    span = EvidenceSpan(
        block_id="source:b0",
        quote="민원 신청서",
    )
    if classification == CsoClassification.S:
        source_label = SourceClassification(
            document_form=document_form,
            other_document_form=other_document_form,
            classification=classification,
            clause_no=clause,
            subclause_key=subclause,
            evidence_spans=(span,),
            rationale="비공개 요건이 직접 확인된다.",
        )
        evidence_level = SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE
    else:
        source_label = SourceClassification(
            document_form=document_form,
            other_document_form=other_document_form,
            classification=CsoClassification.O,
            rationale="공개 양식만 확인된다.",
        )
    primary = primary_subclause if primary_subclause is not None else subclause
    evidence = (
        ()
        if evidence_level == SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE
        else (span,)
    )
    return SourceAssessment(
        source_classification=source_label,
        source_suitability=SourceSuitability(
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_level=evidence_level,
            evidence_spans=evidence,
            rationale="원문 적합성 근거다.",
            reason_code="FIXTURE",
        ),
        business_context="민원 신청 처리",
        subject_roles=(role,),
        available_slots=(
            SourceSlot(
                name="처리 의견란",
                kind=SourceSlotKind.PARAGRAPH,
                evidence_span=span,
            ),
        ),
        primary_subclause=primary,
        primary_rationale="원문의 민원 처리 업무와 신청인 역할에 가장 가깝다.",
        compatible_subclauses=compatible_subclauses,
    )


def generated_document(
    text: str = "신청인 김민서의 개인 연락처는 010-1234-5678이다.",
) -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        title="민원 처리 결과",
        blocks=(ParagraphBlock(block_id="generated:b0", text=text),),
    )


def consistency_assessment(
    *,
    clause: ClauseNumber = ClauseNumber.CLAUSE_5,
    subclause: SubclauseKey = SubclauseKey.BID_CONTRACT,
) -> ConsistencyAssessment:
    quote = "평가기준 35점과 협상 상한 380,000,000원은 공고 전 내부 검토자료다."
    return ConsistencyAssessment(
        document_form=DocumentForm.OFFICIAL_LETTER,
        classification=CsoClassification.S,
        clause_no=clause,
        subclause_key=subclause,
        evidence_spans=(
            EvidenceSpan(block_id="generated:b0", quote=quote),
        ),
        rationale="목표 비공개 요건이 확인된다.",
    )


def accepted_sensitive_assessment(
    text: str = "신청인 김민서의 개인 연락처는 010-1234-5678이다.",
    *,
    document_form: DocumentForm = DocumentForm.OFFICIAL_LETTER,
    other_document_form: str | None = None,
) -> SensitiveConsistencyAssessment:
    link = EvidenceSpan(block_id="generated:b0", quote=text)
    return SensitiveConsistencyAssessment(
        document_form=document_form,
        other_document_form=other_document_form,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_6,
        subclause_key=SubclauseKey.PETITIONER_PII,
        evidence_spans=(link,),
        rationale="신청인과 개인 연락처가 직접 연결된다.",
        sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        assertions=(
            SensitiveAssertion(
                subject_role=SensitiveSubjectRole.APPLICANT,
                subject_span=EvidenceSpan(
                    block_id="generated:b0",
                    quote="신청인 김민서",
                ),
                attribute_kind=SensitiveAttributeKind.PHONE,
                value_span=EvidenceSpan(
                    block_id="generated:b0",
                    quote="010-1234-5678",
                ),
                link_span=link,
                identification_strength=IdentificationStrength.DIRECT,
            ),
        ),
    )


def open_sensitive_assessment() -> SensitiveConsistencyAssessment:
    return SensitiveConsistencyAssessment(
        document_form=DocumentForm.OFFICIAL_LETTER,
        classification=CsoClassification.O,
        rationale="구체적인 개인정보 값이 없다.",
        sensitivity_verdict=SensitiveVerdict.ASSESSED_O,
    )


class FakeGateway:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outputs:
            raise AssertionError("unexpected gateway call")
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return StructuredCall(
            parsed=output,
            response_id=f"response-{len(self.calls)}",
        )
