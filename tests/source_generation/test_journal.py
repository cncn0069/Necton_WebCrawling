from __future__ import annotations

import json
from pathlib import Path

import pytest

from rd2.source_generation.contracts import (
    AuditStageArtifact,
    FailureCode,
    JournalStage,
    JournalStatus,
)
from rd2.source_generation.document_select import prepare_document_selection
from rd2.source_generation.journal import (
    ArtifactStore,
    JournalCorruptError,
    JournalIdentity,
    read_journal,
    record_audit_success,
    run_three_stage_with_journal,
)
from rd2.source_generation.pipeline import PipelineConfig
from rd2.source_generation.prompts import build_prompt_bundle

from .v2_fixtures import (
    FakeGateway,
    accepted_sensitive_assessment,
    generated_document,
    snapshot,
    source_assessment,
    target,
)


AUDIT_CONFIG_SHA256 = "f" * 64
SENSITIVE_SEED = "fixture-sensitive-seed"


def _selection():
    selection = prepare_document_selection(snapshot()).selection
    assert selection is not None
    return selection


def _config(
    *,
    classifier_model: str = "shared-model",
    generator_model: str = "shared-model",
    validator_model: str = "blind-validator",
) -> PipelineConfig:
    return PipelineConfig(
        classifier_model=classifier_model,
        generator_model=generator_model,
        validator_model=validator_model,
        source_sensitive_mode=True,
    )


def _identity(config: PipelineConfig | None = None) -> JournalIdentity:
    resolved = config or _config()
    return JournalIdentity.from_pipeline(
        snapshot=snapshot(),
        selection=_selection(),
        target=target(),
        config=resolved,
        prompt_bundle=build_prompt_bundle(),
        audit_config_sha256=AUDIT_CONFIG_SHA256,
    )


def _run(
    run_dir: Path,
    gateway: FakeGateway,
    *,
    config: PipelineConfig | None = None,
):
    return run_three_stage_with_journal(
        run_dir=run_dir,
        run_id="run-v2",
        snapshot=snapshot(),
        selection=_selection(),
        counterfactual_target=target(),
        gateway=gateway,
        config=config or _config(),
        audit_config_sha256=AUDIT_CONFIG_SHA256,
        sensitive_seed=SENSITIVE_SEED,
    )


def _success_gateway() -> FakeGateway:
    return FakeGateway(
        [
            source_assessment(),
            generated_document(),
            accepted_sensitive_assessment(),
        ]
    )


def test_first_run_persists_four_execution_checkpoints(tmp_path):
    result = _run(tmp_path, _success_gateway())

    assert result.pipeline_result.succeeded
    assert result.executed_stages == (
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
        JournalStage.GENERATED,
        JournalStage.VALIDATED,
    )
    assert result.resumed_stages == ()
    assert result.next_stage == JournalStage.AUDITED
    assert [record.stage for record in read_journal(tmp_path / "journal.jsonl")] == [
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
        JournalStage.GENERATED,
        JournalStage.VALIDATED,
    ]


def test_second_run_resumes_every_verified_execution_stage(tmp_path):
    _run(tmp_path, _success_gateway())
    gateway = FakeGateway([])

    result = _run(tmp_path, gateway)

    assert result.pipeline_result.succeeded
    assert result.executed_stages == ()
    assert result.resumed_stages == (
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
        JournalStage.GENERATED,
        JournalStage.VALIDATED,
    )
    assert result.next_stage == JournalStage.AUDITED
    assert gateway.calls == []


def test_audit_checkpoint_makes_following_run_a_noop(tmp_path):
    first = _run(tmp_path, _success_gateway())
    assert first.next_stage == JournalStage.AUDITED
    record_audit_success(
        run_dir=tmp_path,
        run_id="run-v2",
        source_document_id=snapshot().source_document_id,
        identity=_identity(),
        audit_artifact=AuditStageArtifact(
            audit_artifact_path="audit/classification.jsonl",
            audit_artifact_sha256="e" * 64,
        ),
    )

    result = _run(tmp_path, FakeGateway([]))

    assert result.completed_noop
    assert result.next_stage is None
    assert result.resumed_stages == tuple(JournalStage)
    records = read_journal(tmp_path / "journal.jsonl")
    assert records[-1].stage == JournalStage.AUDITED
    assert records[-1].status == JournalStatus.SUCCEEDED


def test_generation_failure_resumes_after_locked_plan(tmp_path):
    failed = _run(
        tmp_path,
        FakeGateway([source_assessment(), RuntimeError("generation failed")]),
    )
    assert not failed.pipeline_result.succeeded
    assert failed.next_stage == JournalStage.GENERATED

    gateway = FakeGateway(
        [generated_document(), accepted_sensitive_assessment()]
    )
    resumed = _run(tmp_path, gateway)

    assert resumed.pipeline_result.succeeded
    assert resumed.resumed_stages == (
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
    )
    assert resumed.executed_stages == (
        JournalStage.GENERATED,
        JournalStage.VALIDATED,
    )
    assert [call["model"] for call in gateway.calls] == [
        "shared-model",
        "blind-validator",
    ]


def test_validation_failure_reuses_generated_artifact(tmp_path):
    failed = _run(
        tmp_path,
        FakeGateway(
            [
                source_assessment(),
                generated_document(),
                RuntimeError("validation failed"),
            ]
        ),
    )
    assert not failed.pipeline_result.succeeded
    assert failed.next_stage == JournalStage.VALIDATED

    gateway = FakeGateway([accepted_sensitive_assessment()])
    resumed = _run(tmp_path, gateway)

    assert resumed.pipeline_result.succeeded
    assert resumed.resumed_stages == (
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
        JournalStage.GENERATED,
    )
    assert resumed.executed_stages == (JournalStage.VALIDATED,)
    assert [call["model"] for call in gateway.calls] == ["blind-validator"]


def test_generator_model_change_invalidates_generation_and_validation(tmp_path):
    _run(tmp_path, _success_gateway())
    changed = _config(generator_model="generator-v2")
    gateway = FakeGateway(
        [generated_document(), accepted_sensitive_assessment()]
    )

    result = _run(tmp_path, gateway, config=changed)

    assert result.resumed_stages == (
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
    )
    assert result.executed_stages == (
        JournalStage.GENERATED,
        JournalStage.VALIDATED,
    )
    assert result.invalidation_reason == (
        "generator_prompt_model_or_plan_changed"
    )


def test_validator_model_change_invalidates_only_validation(tmp_path):
    _run(tmp_path, _success_gateway())
    changed = _config(validator_model="blind-validator-v2")
    gateway = FakeGateway([accepted_sensitive_assessment()])

    result = _run(tmp_path, gateway, config=changed)

    assert result.resumed_stages == (
        JournalStage.CLASSIFIED,
        JournalStage.PLANNED,
        JournalStage.GENERATED,
    )
    assert result.executed_stages == (JournalStage.VALIDATED,)
    assert result.invalidation_reason == (
        "validator_prompt_model_or_generation_changed"
    )


def test_legacy_contract_is_retained_but_never_reused(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    legacy = {
        "contract_version": "1.0.0",
        "run_id": "run-v2",
        "sequence": 1,
    }
    (tmp_path / "journal.jsonl").write_text(
        json.dumps(legacy) + "\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, _success_gateway())

    assert result.pipeline_result.succeeded
    assert result.invalidation_reason == (
        FailureCode.CONTRACT_VERSION_CHANGED.value
    )
    records = read_journal(tmp_path / "journal.jsonl")
    assert records[0].sequence == 2
    assert all(record.contract_version == "2.1.0" for record in records)


def test_tampered_artifact_returns_typed_journal_failure(tmp_path):
    _run(tmp_path, _success_gateway())
    classified = read_journal(tmp_path / "journal.jsonl")[0]
    artifact_path = tmp_path / classified.artifact_path
    artifact_path.write_bytes(artifact_path.read_bytes() + b"tampered")

    result = _run(tmp_path, FakeGateway([]))

    assert not result.pipeline_result.succeeded
    assert result.pipeline_result.failure is not None
    assert result.pipeline_result.failure.code == FailureCode.JOURNAL_CORRUPT


def test_content_addressed_store_rejects_hash_mismatch(tmp_path):
    store = ArtifactStore(tmp_path)
    reference = store.write(
        AuditStageArtifact(
            audit_artifact_path="audit/result.json",
            audit_artifact_sha256="a" * 64,
        )
    )
    path = tmp_path / reference.path
    path.write_bytes(path.read_bytes() + b"x")

    with pytest.raises(JournalCorruptError, match="hash"):
        store.read(reference, AuditStageArtifact)
