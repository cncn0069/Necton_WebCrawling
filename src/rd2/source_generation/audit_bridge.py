"""Typed bridge from source-generation results to the existing generation audit."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import ValidationError, model_validator

from rd2.audit.orchestrator import run_audit
from rd2.audit.row_contract import AuditContractError, REQUIRED_COLUMNS
from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.generators.agency_categories import get_agency_category
from rd2.generators.generation_plan_schema import GenerationPlan
from rd2.source_generation.classification_taxonomy import ClauseNumber
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    AuditStageArtifact,
    ConsistencyAssessment,
    ConsistencyComparison,
    ContractModel,
    DocumentPipelineResult,
    DocumentSelection,
    FailureCode,
    FailureStage,
    GenerationTarget,
    NonEmptyText,
    RunManifest,
    SensitiveConsistencyAssessment,
    Sha256Hex,
    SourceAssessment,
    StageFailure,
    TargetClassification,
    document_form_matches,
    effective_classification,
)
from rd2.source_generation.journal import (
    JournalIdentity,
    record_audit_failure,
    record_audit_success,
)
from rd2.source_generation.prompts import PromptBundle

AUDIT_BRIDGE_VERSION = "source-generation-audit-bridge-v3"

AUDIT_INPUT_FILENAME = "source_generation_audit_input.csv"
RUN_MANIFEST_FILENAME = "source_generation_run_manifest.json"
CLASSIFICATION_ARTIFACTS_FILENAME = "classification_artifacts.jsonl"
CLASSIFICATION_SUMMARY_FILENAME = "classification_summary.json"
COMMON_SUMMARY_FILENAME = "source_generation_audit_summary.json"
COMMON_MANIFEST_FILENAME = "_source_generation_audit_manifest.json"


class AuditCoverageAssignment(ContractModel):
    """Operational fields that cannot be inferred from semantic classification."""

    row_id: NonEmptyText
    coverage_cell_key: NonEmptyText
    coverage_slot: NonEmptyText
    ordering_agency: NonEmptyText
    template_id: NonEmptyText = "source-ir-v1"
    seed_candidate_id: str = ""
    seed_extraction_id: str = ""
    seed_source_path: str = ""


class AuditBridgeDocument(ContractModel):
    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    source_document_id: NonEmptyText
    source_manifest_key: NonEmptyText
    source_sha256: Sha256Hex
    selection: DocumentSelection
    selection_sha256: Sha256Hex
    selection_config_sha256: Sha256Hex
    classifier_prompt_sha256: Sha256Hex
    generator_prompt_sha256: Sha256Hex
    validator_prompt_sha256: Sha256Hex
    classification_artifact_sha256: Sha256Hex
    planning_artifact_sha256: Sha256Hex
    generation_artifact_sha256: Sha256Hex
    validation_artifact_sha256: Sha256Hex
    pipeline_result: DocumentPipelineResult
    assignment: AuditCoverageAssignment

    @model_validator(mode="after")
    def _pipeline_result_must_be_a_success_for_this_document(
        self,
    ) -> "AuditBridgeDocument":
        if not self.pipeline_result.succeeded:
            raise ValueError("audit bridge requires a successful pipeline result")
        if self.pipeline_result.source_document_id != self.source_document_id:
            raise ValueError("pipeline result source document ID does not match bridge input")
        if self.selection.source_sha256 != self.source_sha256:
            raise ValueError("document selection source hash does not match bridge input")
        if self.selection.selection_sha256 != self.selection_sha256:
            raise ValueError("document selection hash does not match bridge input")
        return self


class ClassificationAuditArtifact(ContractModel):
    """Separate source/target/validation labels; generated body is absent."""

    contract_version: Literal["2.2.0"] = CONTRACT_SCHEMA_VERSION
    bridge_version: Literal["source-generation-audit-bridge-v3"] = (
        AUDIT_BRIDGE_VERSION
    )
    run_id: NonEmptyText
    row_id: NonEmptyText
    source_document_id: NonEmptyText
    source_sha256: Sha256Hex
    selection: DocumentSelection
    selection_sha256: Sha256Hex
    selection_config_sha256: Sha256Hex
    classifier_prompt_sha256: Sha256Hex
    generator_prompt_sha256: Sha256Hex
    validator_prompt_sha256: Sha256Hex
    classification_artifact_sha256: Sha256Hex
    planning_artifact_sha256: Sha256Hex
    generation_artifact_sha256: Sha256Hex
    validation_artifact_sha256: Sha256Hex
    classifier_model: NonEmptyText
    generator_model: NonEmptyText
    validator_model: NonEmptyText
    source_assessment: SourceAssessment
    generation_target: GenerationTarget
    consistency_assessment: SensitiveConsistencyAssessment | ConsistencyAssessment
    comparison: ConsistencyComparison
    requires_review: bool
    review_reasons: tuple[NonEmptyText, ...] = ()

    @model_validator(mode="after")
    def _review_fields_must_match_comparison(self) -> "ClassificationAuditArtifact":
        source_classification = self.source_assessment.source_classification
        subject_role_match = True
        if isinstance(
            self.consistency_assessment,
            SensitiveConsistencyAssessment,
        ):
            allowed_roles = {
                role.value for role in self.source_assessment.subject_roles
            }
            subject_role_match = all(
                assertion.subject_role.value in allowed_roles
                for assertion in self.consistency_assessment.assertions
            )
        expected_comparison = ConsistencyComparison(
            document_form_match=document_form_matches(
                self.consistency_assessment,
                source_classification,
            ),
            classification_match=(
                effective_classification(
                    self.consistency_assessment.classification,
                    self.generation_target.administrative_statuses,
                ).value
                == self.generation_target.classification.value
            ),
            clause_match=(
                self.consistency_assessment.clause_no
                == self.generation_target.clause_no
            ),
            subclause_match=(
                self.consistency_assessment.subclause_key
                == self.generation_target.subclause_key
            ),
            subject_role_match=subject_role_match,
        )
        if self.comparison != expected_comparison:
            raise ValueError(
                "comparison does not match source/target/validation labels"
            )
        expected = _comparison_review_reasons(self.comparison)
        if self.review_reasons != expected:
            raise ValueError("review reasons must exactly match grade comparison")
        if self.requires_review != bool(expected):
            raise ValueError("requires_review must match review reasons")
        if self.validator_model in {
            self.classifier_model,
            self.generator_model,
        }:
            raise ValueError(
                "validator model must differ from classifier and generator"
            )
        source = source_classification
        target = self.generation_target
        if source.classification.value == "O":
            if target.generation_mode.value != "counterfactual":
                raise ValueError("O source requires a counterfactual audit target")
        elif (
            target.generation_mode.value != "source_aligned"
            or target.classification.value != source.classification.value
            or target.clause_no != source.clause_no
            or target.subclause_key != source.subclause_key
        ):
            raise ValueError(
                "C/S source and audit target must remain source-aligned"
            )
        return self


class GenerationAuditBridgeRow(ContractModel):
    """The exact target-only row consumed by ``rd2.audit.row_contract``."""

    row_id: NonEmptyText
    status: Literal["ok"] = "ok"
    body_text: NonEmptyText
    title: NonEmptyText
    cso_classification: TargetClassification
    clause_no: str
    cso_subclause_key: str
    doc_type: NonEmptyText
    document_status: str
    ordering_agency: NonEmptyText
    agency_category: NonEmptyText
    template_id: NonEmptyText
    seed_candidate_id: str = ""
    seed_extraction_id: str = ""
    seed_source_path: str = ""
    coverage_cell_key: NonEmptyText
    coverage_slot: NonEmptyText
    coverage_plan_run_id: NonEmptyText

    def to_csv_dict(self) -> dict[str, str]:
        payload = self.model_dump(mode="json")
        return {column: str(payload[column]) for column in REQUIRED_COLUMNS}


@dataclass(frozen=True)
class LoadedClassificationSidecar:
    artifacts: tuple[ClassificationAuditArtifact, ...]
    sha256: str
    metrics: dict[str, Any]
    forced_review_reasons: dict[str, str]
    target_labels: dict[str, tuple[str, str, str]]


@dataclass(frozen=True)
class AuditBridgeRunResult:
    audit_config_sha256: str
    classification_artifacts: tuple[ClassificationAuditArtifact, ...]
    classification_metrics: dict[str, Any]
    audit_summary: dict[str, Any]
    common_summary_path: Path
    common_manifest_path: Path
    common_manifest_sha256: str


def _comparison_review_reasons(
    comparison: ConsistencyComparison,
) -> tuple[str, ...]:
    reasons = []
    if not comparison.document_form_match:
        reasons.append("document_type_mismatch")
    if not comparison.classification_match:
        reasons.append("classification_mismatch")
    if not comparison.clause_match:
        reasons.append("clause_mismatch")
    if not comparison.subclause_match:
        reasons.append("subclause_mismatch")
    if not comparison.subject_role_match:
        reasons.append("subject_role_mismatch")
    return tuple(reasons)


def compute_audit_config_sha256(
    *,
    plan: GenerationPlan,
    sample_count: int,
) -> str:
    if sample_count < 1:
        raise ValueError("sample_count must be positive")
    return canonical_sha256(
        {
            "bridge_version": AUDIT_BRIDGE_VERSION,
            "plan_run_id": plan.run_id,
            "plan_matrix_hash": plan.matrix_hash,
            "sample_count": sample_count,
        },
        normalization_version=NORMALIZATION_VERSION,
    )


def _plan_cell(plan: GenerationPlan, coverage_cell_key: str):
    for cell in plan.cells:
        if cell.coverage_cell_key == coverage_cell_key:
            return cell
    raise AuditContractError(
        f"generation plan에 없는 coverage cell: {coverage_cell_key!r}"
    )


def _build_classification_artifact(
    *,
    run_manifest: RunManifest,
    document: AuditBridgeDocument,
) -> ClassificationAuditArtifact:
    result = document.pipeline_result
    assert result.source_assessment is not None
    assert result.generation_plan is not None
    assert result.consistency_assessment is not None
    assert result.classification_receipt is not None
    assert result.validation_receipt is not None
    assert result.comparison is not None
    reasons = _comparison_review_reasons(result.comparison)
    return ClassificationAuditArtifact(
        run_id=run_manifest.run_id,
        row_id=document.assignment.row_id,
        source_document_id=document.source_document_id,
        source_sha256=document.source_sha256,
        selection=document.selection,
        selection_sha256=document.selection_sha256,
        selection_config_sha256=document.selection_config_sha256,
        classifier_prompt_sha256=document.classifier_prompt_sha256,
        generator_prompt_sha256=document.generator_prompt_sha256,
        validator_prompt_sha256=document.validator_prompt_sha256,
        classification_artifact_sha256=(
            document.classification_artifact_sha256
        ),
        planning_artifact_sha256=document.planning_artifact_sha256,
        generation_artifact_sha256=document.generation_artifact_sha256,
        validation_artifact_sha256=document.validation_artifact_sha256,
        classifier_model=result.classification_receipt.model_id,
        generator_model=run_manifest.generator_model,
        validator_model=result.validation_receipt.model_id,
        source_assessment=result.source_assessment,
        generation_target=result.generation_plan.final_target,
        consistency_assessment=result.consistency_assessment,
        comparison=result.comparison,
        requires_review=bool(reasons),
        review_reasons=reasons,
    )


def bridge_document_to_audit(
    *,
    run_manifest: RunManifest,
    plan: GenerationPlan,
    document: AuditBridgeDocument,
    prompt_bundle: PromptBundle,
) -> tuple[GenerationAuditBridgeRow, ClassificationAuditArtifact]:
    """Validate all cross-contract links and produce target-only + classification views."""

    if document.source_document_id not in run_manifest.source_document_ids:
        raise AuditContractError(
            f"run manifest에 없는 source document: {document.source_document_id!r}"
        )
    result = document.pipeline_result
    assert result.source_assessment is not None
    assert result.generation_plan is not None
    assert result.generation_artifact is not None
    assert result.classification_receipt is not None
    assert result.validation_receipt is not None
    target = result.generation_plan.final_target

    # generator 프롬프트는 이제 문서마다 잠긴 document_form으로 필터링되므로
    # run_manifest.generator_prompt_sha256(번들 버전 표시용 고정값) 하나와
    # 비교할 수 없다 — 같은 배치 안에서 회의록 문서와 감사자료 문서가 서로
    # 다른(둘 다 정당한) 해시를 갖는 게 정상이다. 대신 이 문서의
    # source_assessment.document_form으로 pipeline.py가 실제로 썼던 것과
    # 같은 계산을 다시 실행해 기대값을 구한다.
    expected_generator_prompt_sha256 = prompt_bundle.generator_definition_for_form(
        result.source_assessment.source_classification.document_form,
        sensitive=(target.clause_no == ClauseNumber.CLAUSE_6),
        subclause_key=target.subclause_key,
    ).sha256

    prompt_pairs = (
        (
            document.classifier_prompt_sha256,
            run_manifest.classifier_prompt_sha256,
            "classifier",
        ),
        (
            document.generator_prompt_sha256,
            expected_generator_prompt_sha256,
            "generator",
        ),
        (
            document.validator_prompt_sha256,
            run_manifest.validator_prompt_sha256,
            "validator",
        ),
    )
    for actual, expected, stage_name in prompt_pairs:
        if actual != expected:
            raise AuditContractError(
                f"document {stage_name} prompt hash가 run manifest와 다릅니다"
            )
    if (
        document.selection_config_sha256
        != run_manifest.selection_config_sha256
    ):
        raise AuditContractError(
            "document selection config hash가 run manifest와 다릅니다"
        )
    if (
        result.classification_receipt.model_id
        != run_manifest.classifier_model
    ):
        raise AuditContractError(
            "classifier model ID가 run manifest와 다릅니다"
        )
    if (
        result.generation_receipt is not None
        and result.generation_receipt.model_id != run_manifest.generator_model
    ):
        raise AuditContractError(
            "generator model ID가 run manifest와 다릅니다"
        )
    if result.validation_receipt.model_id != run_manifest.validator_model:
        raise AuditContractError(
            "validator model ID가 run manifest와 다릅니다"
        )

    cell = _plan_cell(plan, document.assignment.coverage_cell_key)
    expected_target = (
        target.classification.value,
        target.clause_no.value if target.clause_no is not None else "",
        target.subclause_key.value if target.subclause_key is not None else "",
    )
    actual_cell = (
        cell.classification,
        cell.clause_no,
        cell.subclause_key,
    )
    if actual_cell != expected_target:
        raise AuditContractError(
            "coverage cell target label does not match generation target"
        )
    if target.administrative_statuses:
        if len(target.administrative_statuses) != 1:
            raise AuditContractError(
                "audit coverage cell supports exactly one administrative status"
            )
        if cell.admin_status != target.administrative_statuses[0].value:
            raise AuditContractError(
                "coverage cell administrative status does not match generation target"
            )
    elif cell.admin_status:
        raise AuditContractError(
            "coverage cell has an administrative status but generation target does not"
        )
    if cell.cell_state == "excluded" or cell.requested_target < 1:
        raise AuditContractError("coverage assignment points to an inactive plan cell")
    expected_agency_category = get_agency_category(
        document.assignment.ordering_agency
    )
    if expected_agency_category != cell.agency_category:
        raise AuditContractError(
            "ordering agency category does not match coverage plan cell"
        )

    assignment = document.assignment
    row = GenerationAuditBridgeRow(
        row_id=assignment.row_id,
        body_text=result.generation_artifact.generated_document.body_text,
        title=result.generation_artifact.generated_document.title,
        cso_classification=target.classification,
        clause_no=target.clause_no.value if target.clause_no is not None else "",
        cso_subclause_key=(
            target.subclause_key.value
            if target.subclause_key is not None
            else ""
        ),
        doc_type=cell.doc_type,
        document_status=cell.admin_status,
        ordering_agency=assignment.ordering_agency,
        agency_category=cell.agency_category,
        template_id=assignment.template_id,
        seed_candidate_id=(
            assignment.seed_candidate_id or document.source_document_id
        ),
        seed_extraction_id=(
            assignment.seed_extraction_id or document.source_sha256
        ),
        seed_source_path=(
            assignment.seed_source_path or document.source_manifest_key
        ),
        coverage_cell_key=cell.coverage_cell_key,
        coverage_slot=assignment.coverage_slot,
        coverage_plan_run_id=plan.run_id,
    )
    return row, _build_classification_artifact(
        run_manifest=run_manifest,
        document=document,
    )


def summarize_classification_artifacts(
    artifacts: Sequence[ClassificationAuditArtifact],
) -> dict[str, Any]:
    total = len(artifacts)
    mismatch_count = sum(artifact.requires_review for artifact in artifacts)
    reason_counts = {
        reason: sum(
            reason in artifact.review_reasons for artifact in artifacts
        )
        for reason in (
            "document_type_mismatch",
            "classification_mismatch",
            "clause_mismatch",
            "subclause_mismatch",
            "subject_role_mismatch",
        )
    }

    def matched(field: str) -> int:
        return sum(
            bool(getattr(artifact.comparison, field))
            for artifact in artifacts
        )

    return {
        "document_count": total,
        "match_count": total - mismatch_count,
        "mismatch_count": mismatch_count,
        "mismatch_rate": (mismatch_count / total) if total else 0.0,
        "document_form_match_count": matched("document_form_match"),
        "classification_match_count": matched("classification_match"),
        "clause_match_count": matched("clause_match"),
        "subclause_match_count": matched("subclause_match"),
        "subject_role_match_count": matched("subject_role_match"),
        "mismatch_reason_counts": reason_counts,
        "counterfactual_count": sum(
            artifact.generation_target.generation_mode.value == "counterfactual"
            for artifact in artifacts
        ),
        "source_o_count": sum(
            artifact.source_assessment.source_classification.classification.value
            == "O"
            for artifact in artifacts
        ),
    }


def _forced_review_reasons(
    artifacts: Sequence[ClassificationAuditArtifact],
) -> dict[str, str]:
    return {
        artifact.row_id: ",".join(artifact.review_reasons)
        for artifact in artifacts
        if artifact.requires_review
    }


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _classification_jsonl_bytes(
    artifacts: Sequence[ClassificationAuditArtifact],
) -> bytes:
    lines = [
        json.dumps(
            artifact.model_dump(mode="json", exclude_computed_fields=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for artifact in artifacts
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _write_bytes_atomic(path: Path, payload: bytes, token: str) -> None:
    temporary = path.with_name(f".{path.name}.{token}.tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_rows_atomic(
    path: Path,
    rows: Sequence[GenerationAuditBridgeRow],
    token: str,
) -> None:
    temporary = path.with_name(f".{path.name}.{token}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerows(row.to_csv_dict() for row in rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_classification_sidecar(
    path: Path | str,
) -> LoadedClassificationSidecar:
    sidecar_path = Path(path)
    try:
        payload = sidecar_path.read_bytes()
    except OSError as exc:
        raise AuditContractError("classification sidecar를 읽을 수 없습니다") from exc
    if payload and not payload.endswith(b"\n"):
        raise AuditContractError(
            "classification sidecar의 마지막 record가 완결되지 않았습니다"
        )

    artifacts: list[ClassificationAuditArtifact] = []
    seen_rows: set[str] = set()
    seen_source_documents: set[str] = set()
    sidecar_identity: tuple[str, ...] | None = None
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line:
            raise AuditContractError(
                f"classification sidecar에 빈 record가 있습니다: line {line_number}"
            )
        try:
            artifact = ClassificationAuditArtifact.model_validate_json(line)
        except ValidationError as exc:
            raise AuditContractError(
                f"classification sidecar contract 위반: line {line_number}"
            ) from exc
        if artifact.row_id in seen_rows:
            raise AuditContractError(
                f"classification sidecar row_id 중복: {artifact.row_id!r}"
            )
        seen_rows.add(artifact.row_id)
        if artifact.source_document_id in seen_source_documents:
            raise AuditContractError(
                "classification sidecar source_document_id 중복: "
                f"{artifact.source_document_id!r}"
            )
        seen_source_documents.add(artifact.source_document_id)
        current_identity = (
            artifact.run_id,
            artifact.classifier_prompt_sha256,
            artifact.generator_prompt_sha256,
            artifact.validator_prompt_sha256,
            artifact.classifier_model,
            artifact.generator_model,
            artifact.validator_model,
        )
        if sidecar_identity is None:
            sidecar_identity = current_identity
        elif sidecar_identity != current_identity:
            raise AuditContractError(
                "classification sidecar가 서로 다른 run/prompt/model을 혼합합니다"
            )
        artifacts.append(artifact)
    if not artifacts:
        raise AuditContractError("classification sidecar가 비어 있습니다")
    artifact_tuple = tuple(artifacts)
    return LoadedClassificationSidecar(
        artifacts=artifact_tuple,
        sha256=hashlib.sha256(payload).hexdigest(),
        metrics=summarize_classification_artifacts(artifact_tuple),
        forced_review_reasons=_forced_review_reasons(artifact_tuple),
        target_labels={
            artifact.row_id: (
                artifact.generation_target.classification.value,
                (
                    artifact.generation_target.clause_no.value
                    if artifact.generation_target.clause_no is not None
                    else ""
                ),
                (
                    artifact.generation_target.subclause_key.value
                    if artifact.generation_target.subclause_key is not None
                    else ""
                ),
            )
            for artifact in artifact_tuple
        },
    )


def _validate_batch(
    *,
    run_manifest: RunManifest,
    documents: Sequence[AuditBridgeDocument],
) -> None:
    if not documents:
        raise AuditContractError("audit bridge document batch가 비어 있습니다")
    source_ids = [document.source_document_id for document in documents]
    row_ids = [document.assignment.row_id for document in documents]
    slot_keys = [
        (
            document.assignment.coverage_cell_key,
            document.assignment.coverage_slot,
        )
        for document in documents
    ]
    if len(source_ids) != len(set(source_ids)):
        raise AuditContractError("audit bridge source document ID가 중복되었습니다")
    if len(row_ids) != len(set(row_ids)):
        raise AuditContractError("audit bridge row ID가 중복되었습니다")
    if len(slot_keys) != len(set(slot_keys)):
        raise AuditContractError("audit bridge coverage slot이 중복되었습니다")
    unknown_source_ids = sorted(
        set(source_ids) - set(run_manifest.source_document_ids)
    )
    if unknown_source_ids:
        raise AuditContractError(
            "audit bridge batch contains source documents outside run manifest: "
            f"{unknown_source_ids}"
        )


def run_source_generation_audit(
    *,
    run_manifest: RunManifest,
    plan: GenerationPlan,
    documents: Sequence[AuditBridgeDocument],
    sample_count: int,
    output_dir: Path | str,
    prompt_bundle: PromptBundle,
    pdf_dir: Path | None = None,
) -> AuditBridgeRunResult:
    """Publish bridge inputs, run the existing audit, then publish one common marker."""

    _validate_batch(run_manifest=run_manifest, documents=documents)
    if sample_count < 1:
        raise AuditContractError("sample_count must be positive")
    output_path = Path(output_dir)
    common_manifest_path = output_path / COMMON_MANIFEST_FILENAME
    if common_manifest_path.exists():
        raise RuntimeError(
            f"{output_path} already contains a completed source-generation audit"
        )
    output_path.mkdir(parents=True, exist_ok=True)

    rows: list[GenerationAuditBridgeRow] = []
    artifacts: list[ClassificationAuditArtifact] = []
    for document in documents:
        row, artifact = bridge_document_to_audit(
            run_manifest=run_manifest,
            plan=plan,
            document=document,
            prompt_bundle=prompt_bundle,
        )
        rows.append(row)
        artifacts.append(artifact)
    rows.sort(key=lambda item: item.row_id)
    artifacts.sort(key=lambda item: item.row_id)
    artifact_tuple = tuple(artifacts)

    audit_config_sha256 = compute_audit_config_sha256(
        plan=plan,
        sample_count=sample_count,
    )
    token = audit_config_sha256[:16]
    input_path = output_path / AUDIT_INPUT_FILENAME
    run_manifest_path = output_path / RUN_MANIFEST_FILENAME
    sidecar_path = output_path / CLASSIFICATION_ARTIFACTS_FILENAME
    classification_summary_path = output_path / CLASSIFICATION_SUMMARY_FILENAME
    _write_rows_atomic(input_path, rows, token)
    _write_bytes_atomic(
        run_manifest_path,
        _json_bytes(
            run_manifest.model_dump(
                mode="json",
                exclude_computed_fields=True,
            )
        ),
        token,
    )
    sidecar_payload = _classification_jsonl_bytes(artifact_tuple)
    _write_bytes_atomic(sidecar_path, sidecar_payload, token)

    loaded = load_classification_sidecar(sidecar_path)
    classification_summary = {
        "schema_version": 1,
        "bridge_version": AUDIT_BRIDGE_VERSION,
        "run_id": run_manifest.run_id,
        "classification_artifacts_sha256": loaded.sha256,
        "metrics": loaded.metrics,
    }
    _write_bytes_atomic(
        classification_summary_path,
        _json_bytes(classification_summary),
        token,
    )

    audit_summary = run_audit(
        plan=plan,
        input_csv=input_path,
        pdf_dir=pdf_dir,
        sample_count=sample_count,
        output_dir=output_path,
        forced_review_reasons=loaded.forced_review_reasons,
        classification_metrics=loaded.metrics,
        audit_context_sha256=loaded.sha256,
        classification_expectations=loaded.target_labels,
    )
    common_summary = {
        "schema_version": 1,
        "artifact": "rd2-source-generation-audit-summary",
        "bridge_version": AUDIT_BRIDGE_VERSION,
        "run_id": run_manifest.run_id,
        "plan_run_id": plan.run_id,
        "audit_run_id": audit_summary["run"]["audit_run_id"],
        "audit_status": audit_summary["run"]["status"],
        "source_document_count_planned": len(
            run_manifest.source_document_ids
        ),
        "source_document_count_audited": len(artifact_tuple),
        "source_document_count_not_audited": (
            len(run_manifest.source_document_ids) - len(artifact_tuple)
        ),
        "classification": loaded.metrics,
        "review_sample_count": audit_summary["review_selection"]["count"],
    }
    common_summary_path = output_path / COMMON_SUMMARY_FILENAME
    _write_bytes_atomic(common_summary_path, _json_bytes(common_summary), token)

    manifest_files = {}
    for path in sorted(
        (
            input_path,
            run_manifest_path,
            sidecar_path,
            classification_summary_path,
            common_summary_path,
            output_path / "_audit_manifest.json",
            output_path / "diversity_summary.json",
            output_path / "review_samples.csv",
        ),
        key=lambda item: item.name,
    ):
        manifest_files[path.name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        }
    common_manifest = {
        "schema_version": 1,
        "artifact": "rd2-source-generation-audit",
        "bridge_version": AUDIT_BRIDGE_VERSION,
        "run_id": run_manifest.run_id,
        "audit_config_sha256": audit_config_sha256,
        "audit_run_id": audit_summary["run"]["audit_run_id"],
        "files": manifest_files,
    }
    _write_bytes_atomic(
        common_manifest_path,
        _json_bytes(common_manifest),
        token,
    )
    common_manifest_sha256 = hashlib.sha256(
        common_manifest_path.read_bytes()
    ).hexdigest()
    return AuditBridgeRunResult(
        audit_config_sha256=audit_config_sha256,
        classification_artifacts=artifact_tuple,
        classification_metrics=loaded.metrics,
        audit_summary=audit_summary,
        common_summary_path=common_summary_path,
        common_manifest_path=common_manifest_path,
        common_manifest_sha256=common_manifest_sha256,
    )


def record_audit_bridge_success(
    *,
    result: AuditBridgeRunResult,
    journal_run_dir: Path | str,
    journal_run_id: str,
    identities: Mapping[str, JournalIdentity],
):
    """Append per-document ``audited`` records pointing to the common manifest."""

    expected_ids = {
        artifact.source_document_id for artifact in result.classification_artifacts
    }
    if set(identities) != expected_ids:
        raise ValueError("journal identities must match audited source documents")
    journal_records = []
    marker = AuditStageArtifact(
        audit_artifact_path=str(result.common_manifest_path),
        audit_artifact_sha256=result.common_manifest_sha256,
    )
    for source_document_id in sorted(expected_ids):
        identity = identities[source_document_id]
        if identity.audit_config_sha256 != result.audit_config_sha256:
            raise ValueError("journal audit config hash does not match bridge run")
        journal_records.append(
            record_audit_success(
                run_dir=journal_run_dir,
                run_id=journal_run_id,
                source_document_id=source_document_id,
                identity=identity,
                audit_artifact=marker,
            )
        )
    return tuple(journal_records)


def record_audit_bridge_failure(
    *,
    journal_run_dir: Path | str,
    journal_run_id: str,
    identities: Mapping[str, JournalIdentity],
    retryable: bool = False,
):
    """Append sanitized per-document audit failures without replaying either LLM."""

    failure = StageFailure(
        stage=FailureStage.AUDIT,
        code=FailureCode.AUDIT_FAILED,
        retryable=retryable,
        message="formal source-generation audit failed",
    )
    return tuple(
        record_audit_failure(
            run_dir=journal_run_dir,
            run_id=journal_run_id,
            source_document_id=source_document_id,
            identity=identities[source_document_id],
            failure=failure,
        )
        for source_document_id in sorted(identities)
    )
