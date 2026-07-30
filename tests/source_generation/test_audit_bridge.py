from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

import pytest

from rd2.audit.row_contract import AuditContractError, REQUIRED_COLUMNS
from rd2.generators.coverage_plan import CoverageCell
from rd2.generators.generation_plan_schema import build_generation_plan
from rd2.source_generation.audit_bridge import (
    CLASSIFICATION_ARTIFACTS_FILENAME,
    COMMON_MANIFEST_FILENAME,
    AuditBridgeDocument,
    AuditCoverageAssignment,
    bridge_document_to_audit,
    load_classification_sidecar,
    run_source_generation_audit,
    summarize_classification_artifacts,
)
from rd2.source_generation.classification_taxonomy import DocumentForm
from rd2.source_generation.contracts import (
    RunManifest,
    SecurityMode,
    SensitiveConsistencyAssessment,
)
from rd2.source_generation.document_select import (
    SelectionConfig,
    prepare_document_selection,
)
from rd2.source_generation.pipeline import (
    PipelineConfig,
    model_sha256,
    run_three_stage_pipeline,
)
from rd2.source_generation.prompts import build_prompt_bundle

from .v2_fixtures import (
    FakeGateway,
    accepted_sensitive_assessment,
    generated_document,
    snapshot,
    source_assessment,
    target,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64

_AGENCY_WEIGHTS = {
    "central_ministry": 0.2,
    "education_office": 0.2,
    "metro_local_government": 0.2,
    "public_corporation": 0.2,
    "research_institute": 0.2,
}


def _coverage_plan():
    return build_generation_plan(
        c_target=500,
        s_target=650,
        minimum_per_valid_cell=1,
        agency_weights=_AGENCY_WEIGHTS,
        allocation_seed=42,
        max_rows_per_candidate=1,
        candidate_profile_rows=[],
        candidate_manifest={
            "run_id": "candidate-run-1",
            "rule_version": "candidate-rules-v2",
        },
        candidate_profile_digest="sha256:" + "0" * 64,
        created_at="2026-07-28T00:00:00+00:00",
    )


def _target_cell(plan) -> CoverageCell:
    return next(
        cell
        for cell in plan.cells
        if (
            cell.classification == "S"
            and cell.clause_no == "6"
            and cell.subclause_key == "petitioner_pii"
            and cell.agency_category == "public_corporation"
            and cell.requested_target > 0
        )
    )


def _selection():
    value = prepare_document_selection(snapshot()).selection
    assert value is not None
    return value


def _pipeline_config() -> PipelineConfig:
    return PipelineConfig(
        classifier_model="shared-model",
        generator_model="shared-model",
        validator_model="blind-validator",
        source_sensitive_mode=True,
    )


def _pipeline_result(*, mismatch: bool = False):
    consistency = accepted_sensitive_assessment()
    if mismatch:
        consistency = SensitiveConsistencyAssessment.model_validate(
            {
                **consistency.model_dump(
                    mode="python",
                    exclude={"document_form"},
                    exclude_computed_fields=True,
                ),
                "document_form": DocumentForm.REPORT,
            }
        )
    result = run_three_stage_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=target(),
        gateway=FakeGateway(
            [
                source_assessment(),
                generated_document(),
                consistency,
            ]
        ),
        config=_pipeline_config(),
        sensitive_seed="audit-fixture-seed",
    )
    assert result.succeeded
    return result


def _run_manifest() -> RunManifest:
    bundle = build_prompt_bundle()
    selection_config = SelectionConfig()
    return RunManifest(
        run_id="source-generation-run-v2",
        created_at=datetime.now(UTC),
        classifier_model="shared-model",
        generator_model="shared-model",
        validator_model="blind-validator",
        classifier_prompt_sha256=bundle.definition("classifier").sha256,
        generator_prompt_sha256=bundle.definition(
            "sensitive_generator"
        ).sha256,
        validator_prompt_sha256=bundle.definition(
            "sensitive_validator"
        ).sha256,
        planner_policy_sha256=HASH_A,
        selection_config_sha256=selection_config.sha256,
        security_mode=SecurityMode.EXTERNAL_UNREDACTED_APPROVED,
        source_document_ids=(snapshot().source_document_id,),
    )


def _bridge_document(
    cell: CoverageCell,
    *,
    mismatch: bool = False,
    classifier_prompt_sha256: str | None = None,
) -> AuditBridgeDocument:
    manifest = _run_manifest()
    result = _pipeline_result(mismatch=mismatch)
    selection = _selection()
    assert result.source_assessment is not None
    assert result.generation_plan is not None
    assert result.generation_artifact is not None
    assert result.consistency_assessment is not None
    return AuditBridgeDocument(
        source_document_id=snapshot().source_document_id,
        source_manifest_key=snapshot().manifest_key,
        source_sha256=snapshot().source_sha256,
        selection=selection,
        selection_sha256=selection.selection_sha256,
        selection_config_sha256=SelectionConfig().sha256,
        classifier_prompt_sha256=(
            classifier_prompt_sha256
            or manifest.classifier_prompt_sha256
        ),
        generator_prompt_sha256=manifest.generator_prompt_sha256,
        validator_prompt_sha256=manifest.validator_prompt_sha256,
        classification_artifact_sha256=model_sha256(
            result.source_assessment
        ),
        planning_artifact_sha256=model_sha256(result.generation_plan),
        generation_artifact_sha256=model_sha256(
            result.generation_artifact
        ),
        validation_artifact_sha256=model_sha256(
            result.consistency_assessment
        ),
        pipeline_result=result,
        assignment=AuditCoverageAssignment(
            row_id="row-source-1",
            coverage_cell_key=cell.coverage_cell_key,
            coverage_slot="0",
            ordering_agency="가상공공기관",
        ),
    )


def test_bridge_emits_target_only_row_and_separate_classification_artifact():
    plan = _coverage_plan()
    cell = _target_cell(plan)

    row, artifact = bridge_document_to_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        document=_bridge_document(cell),
    )

    assert set(row.to_csv_dict()) == set(REQUIRED_COLUMNS)
    assert row.cso_classification.value == "S"
    assert row.clause_no == "6"
    assert row.cso_subclause_key == "petitioner_pii"
    assert row.doc_type == cell.doc_type
    assert "010-1234-5678" in row.body_text
    assert artifact.source_assessment.source_classification.classification.value == "O"
    assert artifact.generation_target.classification.value == "S"
    assert artifact.consistency_assessment.classification.value == "S"
    assert artifact.requires_review is False


def test_document_form_mismatch_is_preserved_as_review_reason():
    plan = _coverage_plan()
    _, artifact = bridge_document_to_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        document=_bridge_document(_target_cell(plan), mismatch=True),
    )

    assert artifact.requires_review
    assert artifact.review_reasons == ("document_type_mismatch",)
    metrics = summarize_classification_artifacts((artifact,))
    assert metrics["mismatch_count"] == 1
    assert metrics["mismatch_reason_counts"]["document_type_mismatch"] == 1


def test_prompt_hash_mismatch_is_rejected_before_audit():
    plan = _coverage_plan()

    with pytest.raises(AuditContractError, match="classifier"):
        bridge_document_to_audit(
            run_manifest=_run_manifest(),
            plan=plan,
            document=_bridge_document(
                _target_cell(plan),
                classifier_prompt_sha256=HASH_B,
            ),
        )


def test_coverage_cell_must_match_locked_generation_target():
    plan = _coverage_plan()
    wrong_cell = next(
        cell
        for cell in plan.cells
        if (
            cell.classification == "S"
            and cell.clause_no == "5"
            and cell.subclause_key == "bid_contract"
            and cell.agency_category == "public_corporation"
            and cell.requested_target > 0
        )
    )

    with pytest.raises(AuditContractError, match="target label"):
        bridge_document_to_audit(
            run_manifest=_run_manifest(),
            plan=plan,
            document=_bridge_document(wrong_cell),
        )


def test_full_audit_publishes_v2_sidecar_and_common_manifest(tmp_path):
    plan = _coverage_plan()
    output_dir = tmp_path / "audit"

    result = run_source_generation_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        documents=(_bridge_document(_target_cell(plan)),),
        sample_count=1,
        output_dir=output_dir,
    )

    with (output_dir / "source_generation_audit_input.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        row = next(csv.DictReader(stream))
    assert row["cso_classification"] == "S"
    assert row["clause_no"] == "6"
    assert (output_dir / COMMON_MANIFEST_FILENAME).is_file()
    loaded = load_classification_sidecar(
        output_dir / CLASSIFICATION_ARTIFACTS_FILENAME
    )
    assert loaded.metrics["document_count"] == 1
    assert loaded.artifacts[0].contract_version == "2.1.0"
    assert result.common_manifest_sha256 == (
        __import__("hashlib")
        .sha256(result.common_manifest_path.read_bytes())
        .hexdigest()
    )


def test_sidecar_rejects_tampered_review_decision(tmp_path):
    plan = _coverage_plan()
    output_dir = tmp_path / "audit"
    run_source_generation_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        documents=(
            _bridge_document(_target_cell(plan), mismatch=True),
        ),
        sample_count=1,
        output_dir=output_dir,
    )
    path = output_dir / CLASSIFICATION_ARTIFACTS_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    payload["requires_review"] = False
    path.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(AuditContractError, match="contract"):
        load_classification_sidecar(path)
