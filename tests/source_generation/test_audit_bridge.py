from __future__ import annotations

import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import audit_cs_generation as audit_cli  # noqa: E402

from rd2.administrative_status import AdminStatus
from rd2.audit.row_contract import AuditContractError, REQUIRED_COLUMNS
from rd2.generators.coverage_plan import CoverageCell
from rd2.generators.generation_plan_schema import (
    build_generation_plan,
    write_generation_plan_atomic,
)
from rd2.schema.models import CsoClassification
from rd2.source_generation.audit_bridge import (
    AuditBridgeDocument,
    AuditCoverageAssignment,
    CLASSIFICATION_ARTIFACTS_FILENAME,
    COMMON_MANIFEST_FILENAME,
    COMMON_SUMMARY_FILENAME,
    RUN_MANIFEST_FILENAME,
    bridge_document_to_audit,
    compute_audit_config_sha256,
    load_classification_sidecar,
    record_audit_bridge_success,
    run_source_generation_audit,
)
from rd2.source_generation.classification_taxonomy import (
    DocumentForm,
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    CallReceipt,
    DocumentPipelineResult,
    DocumentSelection,
    EvidenceSpan,
    FailureStage,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    GradeComparison,
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
    TargetClassification,
)
from rd2.source_generation.document_select import prepare_document_selection
from rd2.source_generation.journal import (
    JournalIdentity,
    read_journal,
    run_two_pass_with_journal,
)
from rd2.source_generation.pipeline import PipelineConfig, StructuredCall
from rd2.source_generation.prompts import build_prompt_bundle
from rd2.source_generation.legacy_synthetic import FullySyntheticContext

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64

_AGENCY_WEIGHTS = {
    "central_ministry": 0.2,
    "education_office": 0.2,
    "metro_local_government": 0.2,
    "public_corporation": 0.2,
    "research_institute": 0.2,
}


def _plan():
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
            and cell.clause_no == "5"
            and cell.subclause_key == "bid_contract"
            and cell.agency_category == "public_corporation"
            and cell.requested_target > 0
        )
    )


def _generated_document() -> GeneratedDocumentIR:
    return GeneratedDocumentIR(
        title="사업자 선정 평가 검토안",
        blocks=(
            ParagraphBlock(
                block_id="generated-p1",
                text="사업자 선정 평가 기준과 배점은 내부 검토 중이다.",
            ),
        ),
    )


def _pass1() -> Pass1Result:
    return Pass1Result(
        source_classification=SourceClassification(
            document_form=DocumentForm.BID_MATERIAL,
            classification=CsoClassification.O,
            rationale="공개 입찰 공고 자체에는 비공개 근거가 없다.",
        ),
        source_suitability=SourceSuitability(
            evidence_level=SourceEvidenceLevel.NO_USABLE_PUBLIC_SOURCE,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            reason_code="NO_USABLE_SOURCE",
            rationale="생성 목표에 사용할 공개 근거가 없다.",
        ),
        generation_route=GenerationRoute.FULLY_SYNTHETIC,
        generation_target=GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        ),
        generated_document=_generated_document(),
    )


def _pass2(*, mismatch: bool = True) -> Pass2Assessment:
    text = _generated_document().block_text("generated-p1")
    quote = "평가 기준"
    return Pass2Assessment(
        document_form=(
            DocumentForm.REPORT
            if mismatch
            else DocumentForm.BID_MATERIAL
        ),
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        evidence_spans=(
            EvidenceSpan(block_id="generated-p1", quote=quote),
        ),
        rationale="생성본에 입찰 평가 기준이 포함되어 있다.",
    )


def _pipeline_result(*, mismatch: bool = True) -> DocumentPipelineResult:
    pass1 = _pass1()
    pass2 = _pass2(mismatch=mismatch)
    comparison = GradeComparison(
        document_form_match=not mismatch,
        classification_match=True,
        clause_match=True,
        subclause_match=True,
    )
    return DocumentPipelineResult(
        source_document_id="source-1",
        pass1_result=pass1,
        pass2_assessment=pass2,
        pass1_receipt=CallReceipt(
            stage=FailureStage.PASS1,
            model_id="generator-model",
            response_id="response-pass1",
        ),
        pass2_receipt=CallReceipt(
            stage=FailureStage.PASS2,
            model_id="grader-model",
            response_id="response-pass2",
        ),
        comparison=comparison,
    )


def _run_manifest(
    prompt_hash: str = HASH_C,
    selection_config_hash: str = HASH_D,
) -> RunManifest:
    return RunManifest(
        run_id="source-generation-run-1",
        created_at=datetime.now(UTC),
        generator_model="generator-model",
        grader_model="grader-model",
        prompt_bundle_sha256=prompt_hash,
        selection_config_sha256=selection_config_hash,
        security_mode=SecurityMode.EXTERNAL_UNREDACTED_APPROVED,
        source_document_ids=("source-1",),
    )


def _bridge_document(
    cell: CoverageCell,
    *,
    pipeline_result: DocumentPipelineResult | None = None,
    prompt_hash: str = HASH_C,
    pass1_hash: str = HASH_D,
    pass2_hash: str = HASH_E,
) -> AuditBridgeDocument:
    return AuditBridgeDocument(
        source_document_id="source-1",
        source_manifest_key="manifest/source-1",
        source_sha256=HASH_A,
        selection=DocumentSelection(
            policy_version="front-relevance-v1",
            method=SelectionMethod.FULL_DOCUMENT,
            source_sha256=HASH_A,
            selection_sha256=HASH_B,
            original_page_count=1,
            selected_page_numbers=(1,),
            selected_block_ids=("p1:b0",),
            truncated=False,
        ),
        selection_sha256=HASH_B,
        selection_config_sha256=HASH_D,
        prompt_bundle_sha256=prompt_hash,
        pass1_artifact_sha256=pass1_hash,
        pass2_artifact_sha256=pass2_hash,
        pipeline_result=pipeline_result or _pipeline_result(),
        assignment=AuditCoverageAssignment(
            row_id="row-source-1",
            coverage_cell_key=cell.coverage_cell_key,
            coverage_slot="0",
            ordering_agency="가상공공기관",
        ),
    )


def test_bridge_preserves_source_target_and_pass2_but_audits_target_only(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    output_dir = tmp_path / "audit"

    result = run_source_generation_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        documents=(_bridge_document(cell),),
        sample_count=10,
        output_dir=output_dir,
    )

    with (output_dir / "source_generation_audit_input.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        row = next(csv.DictReader(stream))
    assert set(row) == set(REQUIRED_COLUMNS)
    assert row["cso_classification"] == "S"
    assert row["clause_no"] == "5"
    assert row["cso_subclause_key"] == "bid_contract"
    assert row["doc_type"] == cell.doc_type
    assert row["coverage_plan_run_id"] == plan.run_id

    loaded = load_classification_sidecar(
        output_dir / CLASSIFICATION_ARTIFACTS_FILENAME
    )
    artifact = loaded.artifacts[0]
    assert artifact.source_classification.classification == CsoClassification.O
    assert artifact.generation_target.classification == TargetClassification.S
    assert artifact.pass2_assessment.classification == CsoClassification.S
    assert artifact.selection.selected_block_ids == ("p1:b0",)
    assert artifact.selection_config_sha256 == HASH_D
    assert artifact.requires_review is True
    assert artifact.review_reasons == ("document_type_mismatch",)
    assert loaded.metrics["mismatch_count"] == 1
    assert loaded.metrics["counterfactual_count"] == 1
    assert loaded.metrics["source_o_count"] == 1

    with (output_dir / "review_samples.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        review_rows = list(csv.DictReader(stream))
    forced = next(row for row in review_rows if row["row_id"] == "row-source-1")
    assert forced["sample_kind"] == "mandatory"
    assert forced["reason_code"] == "classification_disagreement"
    assert "document_type_mismatch" in forced["reason_detail"]

    assert result.audit_summary["classification_consistency"]["status"] == "ok"
    assert (
        result.audit_summary["classification_consistency"]["metrics"][
            "mismatch_count"
        ]
        == 1
    )
    assert (output_dir / COMMON_SUMMARY_FILENAME).is_file()
    assert (output_dir / COMMON_MANIFEST_FILENAME).is_file()
    persisted_manifest = json.loads(
        (output_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert persisted_manifest["run_id"] == "source-generation-run-1"
    common_manifest = json.loads(
        (output_dir / COMMON_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert RUN_MANIFEST_FILENAME in common_manifest["files"]
    assert result.common_manifest_sha256 == hashlib.sha256(
        result.common_manifest_path.read_bytes()
    ).hexdigest()


def test_bridge_supports_admin_only_s_with_empty_legal_labels():
    plan = _plan()
    base_cell = _target_cell(plan)
    cell = CoverageCell(
        classification="S",
        clause_no="",
        subclause_key="",
        doc_type=base_cell.doc_type,
        agency_category=base_cell.agency_category,
        admin_status=AdminStatus.APPROVAL_PENDING.value,
        cell_state="fallback_only",
        requested_target=1,
        real_candidate_count=0,
        planned_span_seeded=0,
        planned_fallback=1,
        candidate_shortage=1,
        allocation_weight=base_cell.allocation_weight,
        allocation_reason="admin-only test cell",
    )
    plan = type(plan)(
        schema_version=plan.schema_version,
        run_id=plan.run_id,
        created_at=plan.created_at,
        matrix_hash=plan.matrix_hash,
        candidate_manifest=plan.candidate_manifest,
        candidate_profile_digest=plan.candidate_profile_digest,
        normalization_version=plan.normalization_version,
        allocation=plan.allocation,
        cells=(cell,),
    )
    text = "본 문서는 현재 결재 진행 중이며 최종 결재 전 검토가 진행되고 있다."
    generated = GeneratedDocumentIR(
        title="행정상태 검토 문서",
        blocks=(ParagraphBlock(block_id="generated-admin", text=text),),
    )
    pass1 = Pass1Result(
        source_classification=SourceClassification(
            document_form=DocumentForm.BID_MATERIAL,
            classification=CsoClassification.O,
            rationale="법적 비공개 사유는 없다.",
        ),
        source_suitability=SourceSuitability(
            evidence_level=SourceEvidenceLevel.CONTEXTUAL_ANCHOR_ONLY,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_spans=(
                EvidenceSpan(block_id="p1:b0", quote="입찰 평가"),
            ),
            reason_code="ADMIN_CONTEXT",
            rationale="업무 맥락만 사용한다.",
        ),
        generation_route=GenerationRoute.ADMINISTRATIVE_AUGMENTED,
        generation_target=GenerationTarget(
            classification=TargetClassification.S,
            administrative_statuses=(AdminStatus.APPROVAL_PENDING,),
            generation_mode=GenerationMode.COUNTERFACTUAL,
        ),
        generated_document=generated,
    )
    phrase = "결재 진행 중"
    pass2 = Pass2Assessment(
        document_form=DocumentForm.BID_MATERIAL,
        classification=CsoClassification.O,
        rationale="법적 조항은 없고 행정상태만 있다.",
    )
    pipeline_result = DocumentPipelineResult(
        source_document_id="source-1",
        pass1_result=pass1,
        pass2_assessment=pass2,
        pass1_receipt=CallReceipt(
            stage=FailureStage.PASS1,
            model_id="generator-model",
            response_id="response-pass1",
        ),
        pass2_receipt=CallReceipt(
            stage=FailureStage.PASS2,
            model_id="grader-model",
            response_id="response-pass2",
        ),
        comparison=GradeComparison(
            document_form_match=True,
            classification_match=True,
            clause_match=True,
            subclause_match=True,
        ),
    )

    row, artifact = bridge_document_to_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        document=_bridge_document(cell, pipeline_result=pipeline_result),
    )

    assert row.cso_classification == TargetClassification.S
    assert row.clause_no == ""
    assert row.cso_subclause_key == ""
    assert row.document_status == AdminStatus.APPROVAL_PENDING.value
    assert artifact.pass2_assessment.classification == CsoClassification.O
    # 행정상태는 선언값이므로 최종 민감도는 코드가 합쳐 계산한다.
    from rd2.source_generation.contracts import effective_classification
    assert effective_classification(
        artifact.pass2_assessment.classification,
        artifact.generation_target.administrative_statuses,
    ) == CsoClassification.S
    assert artifact.requires_review is False


def test_bridge_rejects_source_target_swap_and_agency_cell_mismatch(tmp_path):
    plan = _plan()
    target_cell = _target_cell(plan)
    wrong_target_cell = next(
        cell
        for cell in plan.cells
        if (
            cell.classification == "C"
            and cell.agency_category == "public_corporation"
            and cell.requested_target > 0
        )
    )
    swapped = _bridge_document(wrong_target_cell)
    with pytest.raises(AuditContractError, match="target label"):
        run_source_generation_audit(
            run_manifest=_run_manifest(),
            plan=plan,
            documents=(swapped,),
            sample_count=10,
            output_dir=tmp_path / "swapped",
        )

    agency_mismatch = _bridge_document(target_cell).model_copy(
        update={
            "assignment": AuditCoverageAssignment(
                row_id="row-source-1",
                coverage_cell_key=target_cell.coverage_cell_key,
                coverage_slot="0",
                ordering_agency="서울특별시교육청",
            )
        }
    )
    with pytest.raises(AuditContractError, match="agency category"):
        run_source_generation_audit(
            run_manifest=_run_manifest(),
            plan=plan,
            documents=(agency_mismatch,),
            sample_count=10,
            output_dir=tmp_path / "agency",
        )


def test_bridge_rejects_prompt_or_model_manifest_mismatch(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    with pytest.raises(AuditContractError, match="prompt hash"):
        run_source_generation_audit(
            run_manifest=_run_manifest(prompt_hash=HASH_E),
            plan=plan,
            documents=(_bridge_document(cell),),
            sample_count=10,
            output_dir=tmp_path / "prompt",
        )

    bad_result = _pipeline_result().model_copy(
        update={
            "pass2_receipt": CallReceipt(
                stage=FailureStage.PASS2,
                model_id="unexpected-grader",
                response_id="response-pass2",
            )
        }
    )
    with pytest.raises(AuditContractError, match="Pass 2 model ID"):
        run_source_generation_audit(
            run_manifest=_run_manifest(),
            plan=plan,
            documents=(
                _bridge_document(cell, pipeline_result=bad_result),
            ),
            sample_count=10,
            output_dir=tmp_path / "model",
        )


def test_failed_documents_may_be_absent_from_audited_subset(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    manifest = _run_manifest().model_copy(
        update={"source_document_ids": ("source-1", "source-failed")}
    )
    output_dir = tmp_path / "partial"

    run_source_generation_audit(
        run_manifest=manifest,
        plan=plan,
        documents=(_bridge_document(cell),),
        sample_count=10,
        output_dir=output_dir,
    )

    summary = json.loads(
        (output_dir / COMMON_SUMMARY_FILENAME).read_text(encoding="utf-8")
    )
    assert summary["source_document_count_planned"] == 2
    assert summary["source_document_count_audited"] == 1
    assert summary["source_document_count_not_audited"] == 1


def test_cli_loads_classification_sidecar_and_forces_review_sample(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    plan_dir = tmp_path / "plan"
    write_generation_plan_atomic(plan_dir, plan)
    bridge_dir = tmp_path / "bridge"
    run_source_generation_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        documents=(_bridge_document(cell),),
        sample_count=10,
        output_dir=bridge_dir,
    )

    cli_output = tmp_path / "cli-audit"
    exit_code = audit_cli.main(
        [
            "--plan",
            str(plan_dir / "generation_plan.json"),
            "--input",
            str(bridge_dir / "source_generation_audit_input.csv"),
            "--classification-artifacts",
            str(bridge_dir / CLASSIFICATION_ARTIFACTS_FILENAME),
            "--sample-count",
            "10",
            "--output-dir",
            str(cli_output),
        ]
    )
    assert exit_code == 0
    summary = json.loads(
        (cli_output / "diversity_summary.json").read_text(encoding="utf-8")
    )
    assert summary["classification_consistency"]["metrics"]["mismatch_count"] == 1
    with (cli_output / "review_samples.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert any(row["reason_code"] == "classification_disagreement" for row in rows)


def test_cli_sidecar_rejects_target_label_tampering(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    plan_dir = tmp_path / "plan"
    write_generation_plan_atomic(plan_dir, plan)
    bridge_dir = tmp_path / "bridge"
    run_source_generation_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        documents=(_bridge_document(cell),),
        sample_count=10,
        output_dir=bridge_dir,
    )
    input_path = bridge_dir / "source_generation_audit_input.csv"
    with input_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["cso_classification"] = "C"
    tampered_input = tmp_path / "tampered.csv"
    with tampered_input.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)

    exit_code = audit_cli.main(
        [
            "--plan",
            str(plan_dir / "generation_plan.json"),
            "--input",
            str(tampered_input),
            "--classification-artifacts",
            str(bridge_dir / CLASSIFICATION_ARTIFACTS_FILENAME),
            "--output-dir",
            str(tmp_path / "tampered-audit"),
        ]
    )
    assert exit_code == 1


def test_classification_sidecar_rejects_torn_and_duplicate_records(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    output_dir = tmp_path / "audit"
    run_source_generation_audit(
        run_manifest=_run_manifest(),
        plan=plan,
        documents=(_bridge_document(cell),),
        sample_count=10,
        output_dir=output_dir,
    )
    sidecar = output_dir / CLASSIFICATION_ARTIFACTS_FILENAME
    payload = sidecar.read_bytes()
    sidecar.write_bytes(payload.rstrip(b"\n"))
    with pytest.raises(AuditContractError, match="완결"):
        load_classification_sidecar(sidecar)

    sidecar.write_bytes(payload + payload)
    with pytest.raises(AuditContractError, match="중복"):
        load_classification_sidecar(sidecar)

    tampered = json.loads(payload.decode("utf-8"))
    tampered["comparison"]["document_form_match"] = True
    tampered["requires_review"] = False
    tampered["review_reasons"] = []
    sidecar.write_text(
        json.dumps(tampered, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(AuditContractError, match="contract"):
        load_classification_sidecar(sidecar)


def _snapshot() -> SourceDocumentSnapshot:
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
                        block_id="p1:b0",
                        text="공개 입찰 공고문이다.",
                    ),
                ),
            ),
        ),
    )


@dataclass
class FakeGateway:
    queued: list[Any]

    def __post_init__(self) -> None:
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = self.queued.pop(0)
        return StructuredCall(
            parsed=parsed,
            response_id=f"response-{len(self.calls)}",
        )


@dataclass
class FakeFullySyntheticGenerator:
    document: GeneratedDocumentIR

    def generate(self, **kwargs):
        return self.document


def test_common_audit_manifest_completes_document_journal(tmp_path):
    plan = _plan()
    cell = _target_cell(plan)
    sample_count = 10
    audit_config_hash = compute_audit_config_sha256(
        plan=plan,
        sample_count=sample_count,
    )
    snapshot = _snapshot()
    selection = prepare_document_selection(snapshot).selection
    assert selection is not None
    prompt_bundle = build_prompt_bundle()
    config = PipelineConfig(
        generator_model="generator-model",
        grader_model="grader-model",
    )
    journal_dir = tmp_path / "journal"
    journal_result = run_two_pass_with_journal(
        run_dir=journal_dir,
        run_id="source-generation-run-1",
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_pass1().generation_target,
        gateway=FakeGateway([_pass1(), _pass2()]),
        config=config,
        audit_config_sha256=audit_config_hash,
        prompt_bundle=prompt_bundle,
        fully_synthetic_generator=FakeFullySyntheticGenerator(
            _pass1().generated_document
        ),
        fully_synthetic_context=FullySyntheticContext(
            scenario_id="audit-bid-contract-001",
            ordering_agency="가상공공기관",
            production_date="2025-01-15",
        ),
    )
    assert journal_result.pipeline_result.succeeded is True
    assert journal_result.pipeline_result.generation_provenance is not None
    assert (
        journal_result.pipeline_result.generation_provenance.synthetic_scenario_id
        == "audit-bid-contract-001"
    )
    journal_records = read_journal(journal_dir / "journal.jsonl")
    pass1_record, pass2_record = journal_records

    run_manifest = _run_manifest(
        prompt_hash=prompt_bundle.sha256,
        selection_config_hash=prompt_bundle.selection_config.sha256,
    )
    document = AuditBridgeDocument(
        source_document_id=snapshot.source_document_id,
        source_manifest_key=snapshot.manifest_key,
        source_sha256=snapshot.source_sha256,
        selection=selection,
        selection_sha256=selection.selection_sha256,
        selection_config_sha256=prompt_bundle.selection_config.sha256,
        prompt_bundle_sha256=prompt_bundle.sha256,
        pass1_artifact_sha256=pass1_record.artifact_sha256,
        pass2_artifact_sha256=pass2_record.artifact_sha256,
        pipeline_result=journal_result.pipeline_result,
        assignment=AuditCoverageAssignment(
            row_id="row-source-1",
            coverage_cell_key=cell.coverage_cell_key,
            coverage_slot="0",
            ordering_agency="가상공공기관",
        ),
    )
    bridge_result = run_source_generation_audit(
        run_manifest=run_manifest,
        plan=plan,
        documents=(document,),
        sample_count=sample_count,
        output_dir=tmp_path / "audit",
    )
    identity = JournalIdentity(
        source_sha256=snapshot.source_sha256,
        selection_sha256=selection.selection_sha256,
        prompt_bundle_sha256=prompt_bundle.sha256,
        generator_model=config.generator_model,
        grader_model=config.grader_model,
        audit_config_sha256=audit_config_hash,
    )
    record_audit_bridge_success(
        result=bridge_result,
        journal_run_dir=journal_dir,
        journal_run_id="source-generation-run-1",
        identities={snapshot.source_document_id: identity},
    )

    no_calls = FakeGateway([])
    completed = run_two_pass_with_journal(
        run_dir=journal_dir,
        run_id="source-generation-run-1",
        snapshot=snapshot,
        selection=selection,
        counterfactual_target=_pass1().generation_target,
        gateway=no_calls,
        config=config,
        audit_config_sha256=audit_config_hash,
        prompt_bundle=prompt_bundle,
    )
    assert completed.completed_noop is True
    assert completed.next_stage is None
    assert completed.pipeline_result.generation_provenance is not None
    assert no_calls.calls == []
