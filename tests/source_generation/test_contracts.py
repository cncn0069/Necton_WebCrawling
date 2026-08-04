from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    DocumentForm,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    BulletListBlock,
    CallReceipt,
    ClassificationStageArtifact,
    ConsistencyComparison,
    DocumentPipelineResult,
    EvidenceSpan,
    FailureStage,
    GeneratedDocumentIR,
    GenerationArtifact,
    GenerationMode,
    GenerationPlan,
    GenerationProvenance,
    GenerationRoute,
    GenerationStageArtifact,
    GenerationTarget,
    JournalRecord,
    JournalStage,
    JournalStatus,
    ParagraphBlock,
    RepairCode,
    RunManifest,
    SecurityMode,
    SensitiveConsistencyAssessment,
    SourceActorRole,
    SourceClassification,
    SourceEvidenceLevel,
    SourceSlot,
    SourceSlotKind,
    TargetClassification,
    ValidationStageArtifact,
    model_sha256,
)

from .v2_fixtures import (
    accepted_sensitive_assessment,
    generated_document,
    snapshot,
    source_assessment,
    target,
)
from rd2.source_generation.document_select import prepare_document_selection


def _selection():
    value = prepare_document_selection(snapshot()).selection
    assert value is not None
    return value


#: 계획을 만들던 플래너는 사라졌다. 계약이 요구하는 것은 "잠긴 값들이 서로
#: 맞는가"이므로 여기서는 값을 직접 세워 그 검증만 본다.
_PLANNER_POLICY_SHA256 = "c" * 64


def _plan():
    assessment = source_assessment()
    return assessment, GenerationPlan(
        requested_target=target(),
        final_target=target(),
        generation_route=GenerationRoute.ANCHORED,
        source_assessment_sha256=model_sha256(assessment),
        source_sha256=snapshot().source_sha256,
        selection_sha256=_selection().selection_sha256,
        planner_policy_sha256=_PLANNER_POLICY_SHA256,
    )


def _receipt(stage: FailureStage, model: str = "model") -> CallReceipt:
    return CallReceipt(
        stage=stage,
        model_id=model,
        response_id=f"{stage.value}-response",
    )


def _generation_artifact() -> GenerationArtifact:
    assessment, plan = _plan()
    return GenerationArtifact(
        plan_sha256=model_sha256(plan),
        generated_document=generated_document(),
        attempt_index=1,
        provenance=GenerationProvenance(
            generation_route=GenerationRoute.ANCHORED,
            source_evidence_level=(
                SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY
            ),
            reason_code="FIXTURE",
            requested_target=plan.requested_target,
            final_target=plan.final_target,
            selection_sha256=plan.selection_sha256,
            uses_source_evidence=True,
            validated_evidence_spans=(
                assessment.source_suitability.evidence_spans
            ),
            sensitive_seed_sha256="b" * 64,
        ),
    )


def test_contract_version_is_v2_and_models_forbid_extra_fields():
    assert CONTRACT_SCHEMA_VERSION == "2.3.0"
    with pytest.raises(ValidationError):
        GeneratedDocumentIR(
            title="문서",
            blocks=(ParagraphBlock(block_id="g1", text="본문"),),
            unknown="금지",
        )


def test_generation_target_carries_only_valid_military_secret_grades():
    target = GenerationTarget(
        classification=TargetClassification.C,
        clause_no=ClauseNumber.CLAUSE_2,
        subclause_key=SubclauseKey.SECURITY_DEFENSE,
        generation_mode=GenerationMode.COUNTERFACTUAL,
        military_secret_grade="2급",
    )
    assert target.military_secret_grade == "2급"
    assert target.model_dump(mode="json")["military_secret_grade"] == "2급"

    with pytest.raises(ValidationError, match="only allowed for C targets"):
        GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.COUNTERFACTUAL,
            military_secret_grade="2급",
        )

    with pytest.raises(ValidationError):
        GenerationTarget(
            classification=TargetClassification.C,
            clause_no=ClauseNumber.CLAUSE_2,
            subclause_key=SubclauseKey.SECURITY_DEFENSE,
            generation_mode=GenerationMode.COUNTERFACTUAL,
            military_secret_grade="4급",
        )


def test_generated_ir_body_is_derived_and_block_ids_are_unique():
    document = GeneratedDocumentIR(
        title="문서",
        blocks=(
            ParagraphBlock(block_id="g1", text="첫 문단"),
            ParagraphBlock(block_id="g2", text="둘째 문단"),
        ),
    )
    assert document.body_text == "첫 문단\n\n둘째 문단"

    with pytest.raises(ValidationError):
        GeneratedDocumentIR(
            title="문서",
            blocks=(
                ParagraphBlock(block_id="same", text="첫 문단"),
                ParagraphBlock(block_id="same", text="둘째 문단"),
            ),
        )


def test_bullet_items_that_carry_a_marker_are_not_double_marked():
    """개조식 기호를 달고 온 항목에 ``- ``를 또 붙이지 않는다.

    C트랙 프롬프트가 ``□ > ○ > - > ※`` 위계를 요구해서 생성기가 기호를 항목
    안에 넣어 온다. 실측(``c-track-correction-security-001``)에서 body_text가
    ``- ○ 인력 증감 소요임`` / ``- - 순환근무조 1개 조 신설``로 나왔다.
    """

    document = GeneratedDocumentIR(
        title="문서",
        blocks=(
            BulletListBlock(
                block_id="b1",
                items=(
                    "○ 중항목임",
                    "- 소항목임",
                    "※ 참고임",
                    "기호 없는 항목임",
                    "-5% 감소함",
                ),
            ),
        ),
    )
    assert document.body_text == (
        "○ 중항목임\n"
        "- 소항목임\n"
        "※ 참고임\n"
        "- 기호 없는 항목임\n"
        "- -5% 감소함"
    )


def test_source_classification_requires_evidence_for_s_and_none_for_o():
    with pytest.raises(ValidationError):
        SourceClassification(
            document_form=DocumentForm.OFFICIAL_LETTER,
            classification=CsoClassification.S,
            clause_no=ClauseNumber.CLAUSE_6,
            subclause_key=SubclauseKey.PETITIONER_PII,
            rationale="근거가 빠졌다.",
        )
    with pytest.raises(ValidationError):
        SourceClassification(
            document_form=DocumentForm.OFFICIAL_LETTER,
            classification=CsoClassification.O,
            clause_no=ClauseNumber.CLAUSE_6,
            subclause_key=SubclauseKey.PETITIONER_PII,
            rationale="O에는 조항이 없다.",
        )


def test_source_assessment_rejects_confidential_c_and_duplicate_facts():
    base = source_assessment()
    confidential = SourceClassification(
        document_form=DocumentForm.OFFICIAL_LETTER,
        classification=CsoClassification.C,
        clause_no=ClauseNumber.CLAUSE_1,
        subclause_key=SubclauseKey.LEGAL_SECRET,
        evidence_spans=(
            EvidenceSpan(block_id="source:b0", quote="민원 신청서"),
        ),
        rationale="제1호",
    )
    with pytest.raises(ValidationError):
        base.model_copy(
            update={"source_classification": confidential}
        ).__class__.model_validate(
            {
                **base.model_dump(
                    mode="python",
                    exclude={"source_classification"},
                ),
                "source_classification": confidential,
            }
        )

    with pytest.raises(ValidationError):
        base.__class__.model_validate(
            {
                **base.model_dump(
                    mode="python",
                    exclude={"subject_roles"},
                ),
                "subject_roles": (
                    SourceActorRole.APPLICANT,
                    SourceActorRole.APPLICANT,
                ),
            }
        )


def test_planner_contract_locks_hashes_and_route():
    assessment, plan = _plan()

    assert plan.source_assessment_sha256 == model_sha256(assessment)
    assert plan.planner_policy_sha256 == _PLANNER_POLICY_SHA256
    assert plan.generation_route == GenerationRoute.ANCHORED
    assert plan.final_target.classification == TargetClassification.S
    plan.validate_against(assessment)

    with pytest.raises(ValueError):
        plan.model_copy(
            update={"generation_route": GenerationRoute.SPAN_SEEDED}
        ).validate_against(assessment)


def test_generation_artifact_enforces_retry_lineage():
    first = _generation_artifact()
    with pytest.raises(ValidationError):
        GenerationArtifact(
            plan_sha256=first.plan_sha256,
            generated_document=first.generated_document,
            attempt_index=2,
            provenance=first.provenance,
        )

    retry = GenerationArtifact(
        plan_sha256=first.plan_sha256,
        generated_document=first.generated_document,
        attempt_index=2,
        parent_generation_sha256=model_sha256(first),
        repair_codes=(RepairCode.DIRECT_VALUE_MISSING,),
        provenance=first.provenance,
    )
    assert retry.parent_generation_sha256 == model_sha256(first)


def test_generation_stage_requires_receipt_except_source_free_route():
    artifact = _generation_artifact()
    with pytest.raises(ValidationError):
        GenerationStageArtifact(generation_artifact=artifact)

    stage = GenerationStageArtifact(
        generation_artifact=artifact,
        receipt=_receipt(FailureStage.GENERATION),
    )
    assert stage.receipt is not None


def test_sensitive_validation_artifact_round_trips_subclass_and_repairs():
    document = generated_document()
    assessment = accepted_sensitive_assessment()
    artifact = ValidationStageArtifact(
        generated_document_sha256=model_sha256(document),
        consistency_assessment=assessment,
        receipt=_receipt(FailureStage.VALIDATION, "validator"),
        comparison=ConsistencyComparison(
            document_form_match=True,
            classification_match=True,
            clause_match=True,
            subclause_match=True,
            subject_role_match=True,
        ),
        repair_codes=(),
    )

    restored = ValidationStageArtifact.model_validate_json(
        artifact.model_dump_json(exclude_computed_fields=True)
    )
    assert isinstance(
        restored.consistency_assessment,
        SensitiveConsistencyAssessment,
    )


def test_successful_pipeline_result_requires_all_four_outputs():
    assessment, plan = _plan()
    generated = _generation_artifact()
    consistency = accepted_sensitive_assessment()
    comparison = ConsistencyComparison(
        document_form_match=True,
        classification_match=True,
        clause_match=True,
        subclause_match=True,
        subject_role_match=True,
    )

    with pytest.raises(ValidationError):
        DocumentPipelineResult(source_document_id="source-1")

    result = DocumentPipelineResult(
        source_document_id="source-1",
        source_assessment=assessment,
        generation_plan=plan,
        generation_artifact=generated,
        consistency_assessment=consistency,
        classification_receipt=_receipt(
            FailureStage.CLASSIFICATION,
            "shared",
        ),
        generation_receipt=_receipt(FailureStage.GENERATION, "shared"),
        validation_receipt=_receipt(
            FailureStage.VALIDATION,
            "validator",
        ),
        comparison=comparison,
    )
    assert result.succeeded


def test_journal_record_enforces_stage_specific_identity_fields():
    common = {
        "run_id": "run-1",
        "sequence": 1,
        "source_document_id": "source-1",
        "recorded_at": datetime.now(UTC),
        "source_sha256": "a" * 64,
        "selection_sha256": "b" * 64,
        "status": JournalStatus.SUCCEEDED,
        "artifact_sha256": "c" * 64,
        "artifact_path": "artifacts/c.json",
    }
    with pytest.raises(ValidationError):
        JournalRecord(stage=JournalStage.CLASSIFIED, **common)

    classified = JournalRecord(
        stage=JournalStage.CLASSIFIED,
        prompt_sha256="d" * 64,
        model_id="classifier",
        **common,
    )
    assert classified.upstream_artifact_sha256 is None

    with pytest.raises(ValidationError):
        JournalRecord(
            stage=JournalStage.PLANNED,
            upstream_artifact_sha256="e" * 64,
            **common,
        )


def test_run_manifest_allows_shared_classifier_generator_but_blind_validator():
    payload = {
        "run_id": "run",
        "created_at": datetime.now(UTC),
        "classifier_model": "shared",
        "generator_model": "shared",
        "validator_model": "validator",
        "classifier_prompt_sha256": "a" * 64,
        "generator_prompt_sha256": "b" * 64,
        "validator_prompt_sha256": "c" * 64,
        "planner_policy_sha256": "d" * 64,
        "selection_config_sha256": "e" * 64,
        "security_mode": SecurityMode.PUBLIC_ONLY,
        "source_document_ids": ("source-1",),
    }
    manifest = RunManifest(**payload)
    assert manifest.classifier_model == manifest.generator_model

    with pytest.raises(ValidationError):
        RunManifest(**{**payload, "validator_model": "shared"})


def test_classification_stage_receipt_must_have_classification_stage():
    with pytest.raises(ValidationError):
        ClassificationStageArtifact(
            source_assessment=source_assessment(),
            receipt=_receipt(FailureStage.GENERATION),
        )

def _sensitive_symbols():
    from rd2.source_generation.contracts import (
        SensitiveConsistencyAssessment,
        SensitiveVerdict,
    )

    return SensitiveConsistencyAssessment, SensitiveVerdict


def test_accepted_s_covers_clauses_five_to_eight():
    """제6호 밖의 목표도 S로 확정될 수 있어야 한다.

    이전에는 ``accepted_s requires clause 6``이 계약에 박혀 있었다. 그 제약은
    이 계약이 제6호 개인정보 관계 판정 전용이던 때 남은 것인데, 목표 강제를
    끄고 판별기가 제5호를 고르기 시작하자 생성까지 정상으로 끝난 문서 34건이
    전부 여기서 버려졌다.

    ``assertions``는 제6호 전용 구조라 다른 호에서는 비어 있는 것이 정상이다 —
    예정가격이 비공개인 이유에는 '주체 역할'이 없다.
    """

    for clause, subclause in (
        (ClauseNumber.CLAUSE_5, SubclauseKey.BID_CONTRACT),
        (ClauseNumber.CLAUSE_7, SubclauseKey.UNIT_COST),
        (ClauseNumber.CLAUSE_8, SubclauseKey.CORNERING),
    ):
        SensitiveConsistencyAssessment, SensitiveVerdict = _sensitive_symbols()
        assessment = SensitiveConsistencyAssessment(
            document_form=DocumentForm.OFFICIAL_LETTER,
            classification=CsoClassification.S,
            clause_no=clause,
            subclause_key=subclause,
            rationale="목표 조항의 보호 대상이 본문에 있다.",
            sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        )

        assert assessment.clause_no is clause
        assert assessment.assertions == ()


def test_accepted_s_still_rejects_clauses_one_to_four():
    """범위를 넓힌 것이지 연 것이 아니다. 이 파이프라인은 제5~8호만 다룬다.

    제1~4호는 C로 매핑돼 있어 ``expected_classification`` 검사가 먼저 걸린다 —
    ``clause 5-8`` 규칙까지 가지도 못한다. 어느 쪽이 잡든 거부되는 것이 요점이라
    메시지를 좁게 고정하지 않는다.
    """

    SensitiveConsistencyAssessment, SensitiveVerdict = _sensitive_symbols()
    with pytest.raises(ValidationError):
        SensitiveConsistencyAssessment(
            document_form=DocumentForm.OFFICIAL_LETTER,
            classification=CsoClassification.S,
            clause_no=ClauseNumber.CLAUSE_2,
            subclause_key=SubclauseKey.SECURITY_DEFENSE,
            rationale="범위 밖 조항이다.",
            sensitivity_verdict=SensitiveVerdict.ACCEPTED_S,
        )
