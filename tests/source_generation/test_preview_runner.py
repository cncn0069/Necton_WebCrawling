from __future__ import annotations

import json
from types import SimpleNamespace

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    DocumentForm,
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    CallReceipt,
    EvidenceSpan,
    FailureStage,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationProvenance,
    GenerationRoute,
    GenerationTarget,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    Pass1Result,
    SourceClassification,
    SourceEvidenceLevel,
    SourceSuitability,
    TargetClassification,
)
from scripts.generate_source_generation_previews import (
    PreviewCase,
    _content_quality_issues,
    _load_p1,
    _repair_generated_document,
    preview_cases,
)


def test_load_p1_accepts_legacy_cache_with_computed_body_text(tmp_path):
    evidence = EvidenceSpan(block_id="p1:b0", quote="공개")
    target = GenerationTarget(
        classification=TargetClassification.C,
        clause_no=ClauseNumber.CLAUSE_1,
        subclause_key=SubclauseKey.LEGAL_SECRET,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )
    result = Pass1Result(
        source_classification=SourceClassification(
            document_form=DocumentForm.REPORT,
            classification=CsoClassification.O,
            evidence_spans=(evidence,),
            rationale="공개 업무 맥락이다.",
        ),
        source_suitability=SourceSuitability(
            evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_spans=(evidence,),
            reason_code="CONTEXT_ONLY",
            rationale="공개 업무 맥락만 사용한다.",
        ),
        generation_route=GenerationRoute.ANCHORED,
        generation_target=target,
        generated_document=GeneratedDocumentIR(
            title="검토 문서",
            blocks=(
                ParagraphBlock(
                    block_id="g1",
                    text="별도 법률에 따른 비공개 대상을 검토한다.",
                ),
            ),
        ),
    )
    receipt = CallReceipt(
        stage=FailureStage.PASS1,
        model_id="generator-model",
        response_id="response-1",
    )
    provenance = GenerationProvenance(
        generation_route=GenerationRoute.ANCHORED,
        source_evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
        reason_code="CONTEXT_ONLY",
        requested_target=target,
        final_target=target,
        selection_sha256="a" * 64,
        uses_source_evidence=True,
        validated_evidence_spans=(evidence,),
        sensitive_seed_sha256="b" * 64,
    )
    payload = {
        "failure": None,
        "result": result.model_dump(mode="json"),
        "receipt": receipt.model_dump(mode="json"),
        "provenance": provenance.model_dump(mode="json"),
    }
    (tmp_path / "p1_stage.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = _load_p1(tmp_path)

    assert loaded is not None
    loaded_result, loaded_receipt, loaded_provenance = loaded
    assert loaded_result.generated_document.body_text == result.generated_document.body_text
    assert loaded_receipt == receipt
    assert loaded_provenance == provenance


def test_content_quality_rejects_meta_description_and_missing_facts():
    case = PreviewCase(
        case_id="clause-5",
        sample_axis="clause",
        sample_value="5",
        target=GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        ),
        required_content_markers=("75점", "380,000,000원"),
    )
    document = GeneratedDocumentIR(
        title="평가 검토 보고서",
        blocks=(
            ParagraphBlock(
                block_id="g1",
                text="본 문서는 평가 기준에 관한 내용을 포함하고 있습니다.",
            ),
        ),
    )

    issues = _content_quality_issues(case, document)

    assert "missing required content marker: 75점" in issues
    assert "missing required content marker: 380,000,000원" in issues
    assert (
        "meta-description phrase present: 내용을 포함하고 있습니다"
        in issues
    )
    assert "no structured content block" in issues
    assert "fewer than two concrete numeric facts" in issues


def test_content_quality_accepts_direct_facts_in_structured_content():
    case = PreviewCase(
        case_id="clause-5",
        sample_axis="clause",
        sample_value="5",
        target=GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        ),
        required_content_markers=("75점", "380,000,000원"),
    )
    document = GeneratedDocumentIR(
        title="평가항목 확정안",
        blocks=(
            KeyValueBlock(
                block_id="g1",
                entries=(
                    KeyValueEntry(key="기술평가 통과선", value="75점"),
                    KeyValueEntry(
                        key="협상 상한",
                        value="380,000,000원",
                    ),
                ),
            ),
        ),
    )

    assert _content_quality_issues(case, document) == ()


def test_admin_preview_quality_requires_semantic_status_context():
    admin_cases = [
        case for case in preview_cases() if case.sample_axis == "admin_status"
    ]

    assert len(admin_cases) == 12
    assert all(len(case.required_content_markers) == 2 for case in admin_cases)
    approval = next(
        case
        for case in admin_cases
        if case.case_id == "admin-01-approval_pending"
    )
    assert approval.required_content_markers == ("기안", "최종 결재")
    assert "결재진행중" not in approval.required_content_markers


def test_content_quality_rejects_literal_administrative_status_label():
    case = next(
        case
        for case in preview_cases()
        if case.case_id == "admin-01-approval_pending"
    )
    document = GeneratedDocumentIR(
        title="행정서비스 개선안",
        blocks=(
            KeyValueBlock(
                block_id="g1",
                entries=(
                    KeyValueEntry(key="문서 상태", value="결재진행중"),
                    KeyValueEntry(key="기안", value="2026-07-28"),
                    KeyValueEntry(key="최종 결재", value="미완료"),
                ),
            ),
        ),
    )

    issues = _content_quality_issues(case, document)

    assert (
        "explicit administrative status label present: 결재진행중"
        in issues
    )


def test_quality_repair_passes_missing_facts_to_dedicated_generation_call():
    case = PreviewCase(
        case_id="clause-5",
        sample_axis="clause",
        sample_value="5",
        target=GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        ),
        sensitive_seed="공고 전 평가안의 통과선은 75점이다.",
        required_content_markers=("공고 전", "75점"),
    )
    repaired = GeneratedDocumentIR(
        title="공고 전 평가안",
        blocks=(
            KeyValueBlock(
                block_id="g1",
                entries=(
                    KeyValueEntry(key="기술평가 통과선", value="75점"),
                    KeyValueEntry(key="협상 상한", value="380,000,000원"),
                ),
            ),
        ),
    )

    class FakeGateway:
        def __init__(self):
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                parsed=repaired,
                response_id="repair-response",
                request_id="repair-request",
                token_usage=None,
            )

    gateway = FakeGateway()
    document, record = _repair_generated_document(
        case=case,
        document=GeneratedDocumentIR(
            title="평가 보고서",
            blocks=(
                ParagraphBlock(
                    block_id="g0",
                    text="평가 내용을 포함하고 있습니다.",
                ),
            ),
        ),
        issues=("missing required content marker: 공고 전",),
        gateway=gateway,
        model="generator-model",
    )

    assert document == repaired
    assert record["response_id"] == "repair-response"
    assert gateway.kwargs["response_model"] is GeneratedDocumentIR
    assert '"공고 전"' in gateway.kwargs["user_prompt"]
    assert "missing required content marker" in gateway.kwargs["user_prompt"]
