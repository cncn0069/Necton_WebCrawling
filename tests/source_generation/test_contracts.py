from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    SUBCLAUSES_BY_CLAUSE,
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AdministrativeStatusFinding,
    AssessmentScope,
    AttachmentReferenceBlock,
    BulletListBlock,
    DocumentSelection,
    EvidenceSpan,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    JournalRecord,
    JournalStage,
    JournalStatus,
    KeyValueBlock,
    KeyValueEntry,
    ParagraphBlock,
    Pass1Result,
    Pass2Assessment,
    RunManifest,
    SecurityMode,
    SelectionMethod,
    SourceClassification,
    SourceEvidenceLevel,
    SourceSuitability,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
    StageFailure,
    FailureCode,
    FailureStage,
    TableBlock,
    TargetClassification,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _source_snapshot() -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document_id="source-1",
        source="PRISM",
        manifest_key="manifest/source-1",
        source_sha256=HASH_A,
        pages=(
            SourcePage(
                page_number=1,
                blocks=(
                    SourceTextBlock(
                        block_id="source-block-1",
                        text="입찰 예정가격과 평가 기준은 내부 검토 중이다.",
                    ),
                ),
            ),
        ),
    )


def _source_classification() -> SourceClassification:
    text = _source_snapshot().pages[0].blocks[0].text
    quote = "입찰 예정가격"
    return SourceClassification(
        document_type=SemanticDocumentType.BID_NOTICE,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        evidence_spans=(
            EvidenceSpan(block_id="source-block-1", quote=quote),
        ),
        rationale="입찰계약 내부 검토 정보가 포함되어 있다.",
    )


def _generated_document() -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        title="사업자 선정 검토안",
        blocks=(
            ParagraphBlock(block_id="p1", text="사업자 평가 기준은 내부 검토 중이다."),
            BulletListBlock(block_id="b1", items=("가격평가", "기술평가")),
            KeyValueBlock(
                block_id="k1",
                entries=(
                    KeyValueEntry(key="상태", value="검토 중"),
                    KeyValueEntry(key="담당", value="계약팀"),
                ),
            ),
            TableBlock(
                block_id="t1",
                columns=("항목", "배점"),
                rows=(("가격", "40"), ("기술", "60")),
            ),
            AttachmentReferenceBlock(
                block_id="a1",
                attachment_id="att-1",
                label="평가표",
                description="별도 보관",
            ),
        ),
    )


def test_taxonomy_has_26_real_types_plus_other_and_24_subclauses():
    assert len(SemanticDocumentType) == 27
    assert SemanticDocumentType.OTHER.value == "other"
    assert "synthetic_document" not in {item.value for item in SemanticDocumentType}
    assert len({key for keys in SUBCLAUSES_BY_CLAUSE.values() for key in keys}) == 24
    assert set(SUBCLAUSES_BY_CLAUSE) == set(ClauseNumber)


def test_generated_ir_body_text_is_deterministic_and_not_an_input_field():
    document = _generated_document()

    assert document.body_text == (
        "사업자 평가 기준은 내부 검토 중이다.\n\n"
        "- 가격평가\n- 기술평가\n\n"
        "상태: 검토 중\n담당: 계약팀\n\n"
        "항목\t배점\n가격\t40\n기술\t60\n\n"
        "[첨부] 평가표 (att-1): 별도 보관"
    )
    schema = GeneratedDocumentIR.model_json_schema()
    assert "body_text" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_pass1_schema_uses_openai_supported_any_of_for_document_blocks():
    schema = Pass1Result.model_json_schema()
    block_items = schema["$defs"]["GeneratedDocumentIR"]["properties"]["blocks"][
        "items"
    ]

    assert "anyOf" in block_items
    assert "oneOf" not in block_items
    assert "discriminator" not in block_items


def test_generated_ir_rejects_duplicate_block_ids_and_bad_table_shape():
    with pytest.raises(ValidationError, match="block IDs must be unique"):
        GeneratedDocumentIR(
            title="중복",
            blocks=(
                ParagraphBlock(block_id="same", text="첫 문단"),
                ParagraphBlock(block_id="same", text="둘째 문단"),
            ),
        )

    with pytest.raises(ValidationError, match="expected 2"):
        TableBlock(block_id="table", columns=("a", "b"), rows=(("only-one",),))


def test_source_snapshot_requires_sorted_unique_pages_and_global_block_ids():
    with pytest.raises(ValidationError, match="contiguous"):
        SourceDocumentSnapshot(
            source_document_id="source-1",
            source="PRISM",
            manifest_key="manifest/source-1",
            source_sha256=HASH_A,
            pages=(
                SourcePage(
                    page_number=2,
                    blocks=(SourceTextBlock(block_id="b2", text="둘째"),),
                ),
                SourcePage(
                    page_number=1,
                    blocks=(SourceTextBlock(block_id="b1", text="첫째"),),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="unique across"):
        SourceDocumentSnapshot(
            source_document_id="source-1",
            source="PRISM",
            manifest_key="manifest/source-1",
            source_sha256=HASH_A,
            pages=(
                SourcePage(
                    page_number=1,
                    blocks=(SourceTextBlock(block_id="same", text="첫째"),),
                ),
                SourcePage(
                    page_number=2,
                    blocks=(SourceTextBlock(block_id="same", text="둘째"),),
                ),
            ),
        )


def test_document_selection_enforces_85_86_boundary():
    full = DocumentSelection(
        policy_version="v1",
        method=SelectionMethod.FULL_DOCUMENT,
        source_sha256=HASH_A,
        selection_sha256=HASH_B,
        original_page_count=85,
        selected_page_numbers=tuple(range(1, 86)),
        selected_block_ids=("b1",),
        truncated=False,
    )
    assert full.original_page_count == 85

    relevance = DocumentSelection(
        policy_version="v1",
        method=SelectionMethod.FRONT_RELEVANCE,
        source_sha256=HASH_A,
        selection_sha256=HASH_B,
        original_page_count=86,
        selected_page_numbers=(1, 2),
        selected_block_ids=("b1", "b2"),
        truncated=True,
    )
    assert relevance.original_page_count == 86

    with pytest.raises(ValidationError, match="above page_threshold"):
        DocumentSelection(
            policy_version="v1",
            method=SelectionMethod.FRONT_RELEVANCE,
            source_sha256=HASH_A,
            selection_sha256=HASH_B,
            original_page_count=85,
            selected_page_numbers=(1,),
            selected_block_ids=("b1",),
            truncated=True,
        )


def test_classification_rejects_wrong_clause_subclause_and_o_with_clause():
    span = EvidenceSpan(block_id="b1", quote="x")
    with pytest.raises(ValidationError, match="does not map"):
        SourceClassification(
            document_type=SemanticDocumentType.REPORT,
            classification=CsoClassification.C,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            evidence_spans=(span,),
            rationale="잘못된 조합",
        )

    with pytest.raises(ValidationError, match="does not belong"):
        SourceClassification(
            document_type=SemanticDocumentType.REPORT,
            classification=CsoClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.PERSONNEL_PII,
            evidence_spans=(span,),
            rationale="잘못된 조합",
        )

    with pytest.raises(ValidationError, match="cannot have clause"):
        SourceClassification(
            document_type=SemanticDocumentType.REPORT,
            classification=CsoClassification.O,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            rationale="공개 문서",
        )


def test_other_type_requires_free_text_description():
    with pytest.raises(ValidationError, match="other_document_type is required"):
        SourceClassification(
            document_type=SemanticDocumentType.OTHER,
            classification=CsoClassification.O,
            rationale="기존 enum 밖 문서",
        )


def test_source_evidence_quote_must_actually_exist_in_the_snapshot():
    """모델이 오프셋을 안 내도 '근거가 실재해야 한다'는 속성은 유지된다."""

    classification = _source_classification()
    classification.validate_against_snapshot(_source_snapshot())

    bad = classification.model_copy(
        update={
            "evidence_spans": (
                EvidenceSpan(block_id="source-block-1", quote="불일치"),
            )
        }
    )
    with pytest.raises(ValueError, match="not found"):
        bad.validate_against_snapshot(_source_snapshot())


def test_pass1_requires_counterfactual_for_o_source():
    source = SourceClassification(
        document_type=SemanticDocumentType.RESEARCH_REPORT,
        classification=CsoClassification.O,
        rationale="공개 연구보고서",
    )
    target = GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.DECISION_REVIEW,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )

    result = Pass1Result(
        source_classification=source,
        source_suitability=SourceSuitability(
            evidence_level=SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            reason_code="NO_USABLE_SOURCE",
            rationale="생성 목표에 사용할 공개 근거가 없다.",
        ),
        generation_route=GenerationRoute.FULLY_SYNTHETIC,
        generation_target=target,
        generated_document=_generated_document(),
    )
    assert result.source_classification.classification == CsoClassification.O
    assert result.generation_target.classification == TargetClassification.S

    with pytest.raises(ValidationError, match="counterfactual"):
        Pass1Result(
            source_classification=source,
            source_suitability=result.source_suitability,
            generation_route=GenerationRoute.FULLY_SYNTHETIC,
            generation_target=target.model_copy(
                update={"generation_mode": GenerationMode.SOURCE_ALIGNED}
            ),
            generated_document=_generated_document(),
        )


def test_pass1_source_aligned_target_must_exactly_match_source_label():
    source = _source_classification()
    suitability = SourceSuitability(
        evidence_level=SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE,
        assessment_scope=AssessmentScope.FULL_DOCUMENT,
        evidence_spans=source.evidence_spans,
        reason_code="DIRECT_SOURCE_LABEL",
        rationale="원문에 직접적인 법적 분류 근거가 있다.",
    )
    matching = GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.SOURCE_ALIGNED,
    )
    Pass1Result(
        source_classification=source,
        source_suitability=suitability,
        generation_route=GenerationRoute.SOURCE_ALIGNED,
        generation_target=matching,
        generated_document=_generated_document(),
    )

    with pytest.raises(ValidationError, match="exactly match"):
        Pass1Result(
            source_classification=source,
            source_suitability=suitability,
            generation_route=GenerationRoute.SOURCE_ALIGNED,
            generation_target=matching.model_copy(
                update={"subclause_key": SubclauseKey.DECISION_REVIEW}
            ),
            generated_document=_generated_document(),
        )


def test_pass1_route_requires_matching_source_evidence_level():
    source = SourceClassification(
        document_type=SemanticDocumentType.BID_NOTICE,
        classification=CsoClassification.O,
        rationale="명시적인 비공개 법적 근거가 없다.",
    )
    target = GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )
    evidence = EvidenceSpan(block_id="source-block-1", quote="입찰")

    with pytest.raises(ValidationError, match="direct_sensitive_span"):
        Pass1Result(
            source_classification=source,
            source_suitability=SourceSuitability(
                evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
                assessment_scope=AssessmentScope.FULL_DOCUMENT,
                evidence_spans=(evidence,),
                reason_code="CONTEXT_ONLY",
                rationale="입찰 업무 맥락만 있다.",
            ),
            generation_route=GenerationRoute.SPAN_SEEDED,
            generation_target=target,
            generated_document=_generated_document(),
        )


def test_clause_6_span_seeded_is_disabled_until_deidentification_exists():
    with pytest.raises(ValidationError, match="de-identification"):
        Pass1Result(
            source_classification=SourceClassification(
                document_type=SemanticDocumentType.REPORT,
                classification=CsoClassification.O,
                rationale="개인정보 비공개 조항은 명시되지 않았다.",
            ),
            source_suitability=SourceSuitability(
                evidence_level=SourceEvidenceLevel.DIRECT_SENSITIVE_SPAN,
                assessment_scope=AssessmentScope.FULL_DOCUMENT,
                evidence_spans=(
                    EvidenceSpan(block_id="source-block-1", quote="성명"),
                ),
                reason_code="PII_SPAN",
                rationale="개인 식별정보로 보이는 span이 있다.",
            ),
            generation_route=GenerationRoute.SPAN_SEEDED,
            generation_target=GenerationTarget(
                classification=TargetClassification.S,
                clause_no=ClauseNumber.CLAUSE_6,
                subclause_key=SubclauseKey.PERSONNEL_PII,
                generation_mode=GenerationMode.COUNTERFACTUAL,
            ),
            generated_document=_generated_document(),
        )


def test_pass2_span_is_validated_only_against_generated_ir():
    document = _generated_document()
    quote = "평가 기준"
    block_text = document.block_text("p1")
    assessment = Pass2Assessment(
        document_type=SemanticDocumentType.APPROVAL,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.DECISION_REVIEW,
        evidence_spans=(
            EvidenceSpan(block_id="p1", quote=quote),
        ),
        rationale="내부 검토 중인 평가 기준이 핵심 근거다.",
    )
    assessment.validate_against_document(document)

    bad = assessment.model_copy(
        update={
            "evidence_spans": (
                EvidenceSpan(block_id="missing", quote="x"),
            )
        }
    )
    with pytest.raises(ValueError, match="unknown generated"):
        bad.validate_against_document(document)


def test_run_manifest_rejects_same_generator_and_grader_model():
    with pytest.raises(ValidationError, match="must be different"):
        RunManifest(
            run_id="run-1",
            created_at=datetime.now(UTC),
            generator_model="same-model",
            grader_model="same-model",
            prompt_bundle_sha256=HASH_A,
            selection_config_sha256=HASH_B,
            security_mode=SecurityMode.EXTERNAL_UNREDACTED_APPROVED,
            source_document_ids=("source-1",),
        )


def test_journal_success_and_failure_payloads_are_typed():
    success = JournalRecord(
        run_id="run-1",
        sequence=1,
        source_document_id="source-1",
        stage=JournalStage.PASS1_GENERATED,
        status=JournalStatus.SUCCEEDED,
        recorded_at=datetime.now(UTC),
        source_sha256=HASH_A,
        selection_sha256=HASH_B,
        prompt_bundle_sha256=HASH_A,
        model_id="generator-model",
        artifact_sha256=HASH_C,
        artifact_path="artifacts/pass1.json",
    )
    assert success.failure is None

    failure = JournalRecord(
        run_id="run-1",
        sequence=2,
        source_document_id="source-1",
        stage=JournalStage.PASS2_GRADED,
        status=JournalStatus.FAILED,
        recorded_at=datetime.now(UTC),
        source_sha256=HASH_A,
        selection_sha256=HASH_B,
        prompt_bundle_sha256=HASH_A,
        model_id="grader-model",
        upstream_artifact_sha256=HASH_C,
        failure=StageFailure(
            stage=FailureStage.PASS2,
            code=FailureCode.SDK_ERROR,
            retryable=True,
            message="SDK retries exhausted",
        ),
    )
    assert failure.failure is not None

    with pytest.raises(ValidationError, match="requires artifact"):
        JournalRecord(
            run_id="run-1",
            sequence=3,
            source_document_id="source-1",
            stage=JournalStage.AUDITED,
            status=JournalStatus.SUCCEEDED,
            recorded_at=datetime.now(UTC),
            source_sha256=HASH_A,
            selection_sha256=HASH_B,
            upstream_artifact_sha256=HASH_C,
            stage_config_sha256=HASH_A,
        )

    with pytest.raises(ValidationError, match="requires failure stage pass2"):
        JournalRecord(
            run_id="run-1",
            sequence=4,
            source_document_id="source-1",
            stage=JournalStage.PASS2_GRADED,
            status=JournalStatus.FAILED,
            recorded_at=datetime.now(UTC),
            source_sha256=HASH_A,
            selection_sha256=HASH_B,
            prompt_bundle_sha256=HASH_A,
            model_id="grader-model",
            upstream_artifact_sha256=HASH_C,
            failure=StageFailure(
                stage=FailureStage.AUDIT,
                code=FailureCode.AUDIT_FAILED,
                retryable=False,
                message="wrong stage",
            ),
        )


def test_contract_models_forbid_extra_fields_and_are_frozen():
    with pytest.raises(ValidationError, match="Extra inputs"):
        ParagraphBlock(block_id="p1", text="본문", unexpected="x")

    block = ParagraphBlock(block_id="p1", text="본문")
    with pytest.raises(ValidationError, match="frozen"):
        block.text = "변경"


def test_judgment_models_declare_evidence_and_rationale_before_the_verdict():
    """필드 순서 = structured output의 생성 순서 = 추론 순서.

    판정 필드가 evidence/rationale보다 앞서면 근거가 사후 정당화로 바뀌고
    evidence span 인용 불일치가 늘어난다. 되돌아가면 이 테스트가 잡는다.
    """

    def index_of(model: type, field: str) -> int:
        return list(model.model_fields).index(field)

    # 교차 제약이 없는 모델에서는 근거를 판정보다 앞에 둔다.
    for model, verdicts in (
        (SourceSuitability, ("reason_code",)),
        (AdministrativeStatusFinding, ("status",)),
    ):
        evidence_at = index_of(model, "evidence_spans")
        rationale_at = index_of(model, "rationale")
        assert evidence_at < rationale_at, model.__name__
        for verdict in verdicts:
            assert rationale_at < index_of(model, verdict), (
                f"{model.__name__}.{verdict} must follow evidence and rationale"
            )


def test_mutually_constrained_verdict_fields_stay_adjacent():
    """서로를 제약하는 필드를 떼어놓으면 모델이 모순을 만든다.

    classification·clause_no·subclause_key는 C=제1~4호, S=제5~8호, O=둘 다
    null, subclause는 clause 소속이라는 제약으로 묶여 있다. 실측에서 이
    셋 사이에 evidence_spans를 끼워넣자 "clause 5 does not map to
    classification C" 같은 자기모순 응답이 나왔다.
    """

    for model in (SourceClassification, Pass2Assessment):
        fields = list(model.model_fields)
        cluster = [fields.index(name) for name in
                   ("classification", "clause_no", "subclause_key")]
        assert cluster == sorted(cluster), model.__name__
        assert cluster[-1] - cluster[0] == 2, (
            f"{model.__name__}: 제약으로 묶인 판정 필드 사이에 다른 필드가 있다"
        )
        # 덩어리 전체가 근거보다 앞에 온다.
        assert cluster[-1] < fields.index("evidence_spans"), model.__name__


def test_gating_fields_precede_the_evidence_they_gate():
    """근거의 허용 여부를 결정하는 필드는 근거보다 앞에 와야 한다.

    gate를 근거 뒤에 두면 모델이 span을 먼저 뱉고 나중에 그 조합을 금지하는
    값을 골라 스스로 모순되는 응답을 만든다 — 실측에서 20건 중 4건이
    이 방식으로 계약 위반 거절됐다.
    """

    def index_of(model: type, field: str) -> int:
        return list(model.model_fields).index(field)

    for model, gate in (
        # classification: C/S는 span을 요구하고 O는 clause/subclause를 금지한다.
        (SourceClassification, "classification"),
        (Pass2Assessment, "classification"),
        # evidence_level: no_usable_public_source는 span을 금지한다.
        (SourceSuitability, "evidence_level"),
    ):
        assert index_of(model, gate) < index_of(model, "evidence_spans"), (
            f"{model.__name__}.{gate} gates evidence and must precede it"
        )


def test_pass1_result_orders_analysis_before_generation():
    """분류·적합성을 먼저 확정한 뒤 route/target/본문을 생성한다."""

    order = list(Pass1Result.model_fields)
    assert order.index("source_classification") < order.index("source_suitability")
    assert order.index("source_suitability") < order.index("generation_route")
    assert order.index("generation_route") < order.index("generation_target")
    assert order.index("generation_target") < order.index("generated_document")
