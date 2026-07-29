from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    AssessmentScope,
    AuditStageArtifact,
    EvidenceSpan,
    FailureCode,
    FailureStage,
    GeneratedDocumentIR,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    JournalStage,
    ParagraphBlock,
    Pass1Result,
    Pass2Assessment,
    SourceClassification,
    SourceEvidenceLevel,
    SourceSuitability,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
    StageFailure,
    TargetClassification,
    TokenUsage,
)
from rd2.source_generation.document_select import (
    SelectionConfig,
    prepare_document_selection,
)
from rd2.source_generation.journal import (
    ArtifactReference,
    JournalCorruptError,
    JournalIdentity,
    JournalWriter,
    JournalWriterConflict,
    read_journal,
    record_audit_failure,
    record_audit_success,
    run_two_pass_with_journal,
)
from rd2.source_generation.pipeline import (
    PipelineConfig,
    StructuredCall,
    StructuredCallError,
)
from rd2.source_generation.prompts import (
    PromptDefinition,
    build_prompt_bundle,
)

AUDIT_HASH = "f" * 64


def _snapshot(*, source_hash: str = "a" * 64) -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document_id="source-1",
        source="PRISM",
        manifest_key="manifest/source-1",
        source_sha256=source_hash,
        pages=(
            SourcePage(
                page_number=1,
                blocks=(
                    SourceTextBlock(
                        block_id="p1:b0",
                        text="입찰 평가 기준은 내부 검토 중이다.",
                    ),
                ),
            ),
        ),
    )


def _target() -> GenerationTarget:
    return GenerationTarget(
        classification=TargetClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        generation_mode=GenerationMode.COUNTERFACTUAL,
    )


def _pass1() -> Pass1Result:
    text = _snapshot().block_text("p1:b0")
    quote = "평가 기준"
    evidence = EvidenceSpan(block_id="p1:b0", quote=quote)
    return Pass1Result(
        source_classification=SourceClassification(
            document_type=SemanticDocumentType.BID_NOTICE,
            classification=CsoClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            evidence_spans=(evidence,),
            rationale="입찰 평가 기준이 포함되어 있다.",
        ),
        source_suitability=SourceSuitability(
            evidence_level=SourceEvidenceLevel.DIRECT_LEGAL_EVIDENCE,
            assessment_scope=AssessmentScope.FULL_DOCUMENT,
            evidence_spans=(evidence,),
            reason_code="DIRECT_SOURCE_LABEL",
            rationale="원문에 입찰계약 관련 직접 근거가 있다.",
        ),
        generation_route=GenerationRoute.SOURCE_ALIGNED,
        generation_target=GenerationTarget(
            classification=TargetClassification.S,
            clause_no=ClauseNumber.CLAUSE_5,
            subclause_key=SubclauseKey.BID_CONTRACT,
            generation_mode=GenerationMode.SOURCE_ALIGNED,
        ),
        generated_document=GeneratedDocumentIR(
            title="사업자 선정 평가 검토안",
            blocks=(
                ParagraphBlock(
                    block_id="generated-p1",
                    text="사업자 선정 평가 기준과 배점은 내부 검토 중이다.",
                ),
            ),
        ),
    )


def _pass2() -> Pass2Assessment:
    text = _pass1().generated_document.block_text("generated-p1")
    quote = "평가 기준"
    return Pass2Assessment(
        document_type=SemanticDocumentType.BID_NOTICE,
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
        evidence_spans=(
            EvidenceSpan(block_id="generated-p1", quote=quote),
        ),
        rationale="생성본에 입찰 평가 기준이 있다.",
    )


@dataclass
class FakeGateway:
    queued: list[Any]

    def __post_init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        result = self.queued.pop(0)
        if isinstance(result, Exception):
            raise result
        return StructuredCall(
            parsed=result,
            response_id=f"response-{len(self.calls)}",
            token_usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        )


def _selection(
    snapshot: SourceDocumentSnapshot,
    selection_config: SelectionConfig | None = None,
):
    selection = prepare_document_selection(snapshot, selection_config).selection
    assert selection is not None
    return selection


def _config(
    *,
    generator_model: str = "generator-model",
    grader_model: str = "grader-model",
) -> PipelineConfig:
    return PipelineConfig(
        generator_model=generator_model,
        grader_model=grader_model,
    )


def _identity(
    snapshot: SourceDocumentSnapshot,
    *,
    selection=None,
    config: PipelineConfig | None = None,
    audit_hash: str = AUDIT_HASH,
    prompt_bundle=None,
) -> JournalIdentity:
    resolved_selection = selection or _selection(snapshot)
    resolved_config = config or _config()
    resolved_bundle = prompt_bundle or build_prompt_bundle()
    return JournalIdentity(
        source_sha256=snapshot.source_sha256,
        selection_sha256=resolved_selection.selection_sha256,
        prompt_bundle_sha256=resolved_bundle.sha256,
        generator_model=resolved_config.generator_model,
        grader_model=resolved_config.grader_model,
        audit_config_sha256=audit_hash,
    )


def _audit_artifact(run_dir: Path) -> AuditStageArtifact:
    audit_path = run_dir / "audit" / "report.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'{"status":"complete"}\n'
    audit_path.write_bytes(payload)
    return AuditStageArtifact(
        audit_artifact_path=str(audit_path),
        audit_artifact_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _run(
    run_dir: Path,
    gateway: FakeGateway,
    *,
    snapshot: SourceDocumentSnapshot | None = None,
    config: PipelineConfig | None = None,
    audit_hash: str = AUDIT_HASH,
    prompt_bundle=None,
    selection_config: SelectionConfig | None = None,
):
    resolved_snapshot = snapshot or _snapshot()
    return run_two_pass_with_journal(
        run_dir=run_dir,
        run_id="run-1",
        snapshot=resolved_snapshot,
        selection=_selection(resolved_snapshot, selection_config),
        counterfactual_target=_target(),
        gateway=gateway,
        config=config or _config(),
        audit_config_sha256=audit_hash,
        selection_config=selection_config,
        prompt_bundle=prompt_bundle,
    )


def test_success_appends_both_stages_with_verified_usage_and_artifacts(tmp_path):
    gateway = FakeGateway([_pass1(), _pass2()])
    result = _run(tmp_path, gateway)

    assert result.pipeline_result.succeeded is True
    assert result.executed_stages == (
        JournalStage.PASS1_GENERATED,
        JournalStage.PASS2_GRADED,
    )
    assert result.next_stage == JournalStage.AUDITED
    records = read_journal(tmp_path / "journal.jsonl")
    assert [record.stage for record in records] == [
        JournalStage.PASS1_GENERATED,
        JournalStage.PASS2_GRADED,
    ]
    assert [record.token_usage.total_tokens for record in records] == [15, 15]
    assert records[1].upstream_artifact_sha256 == records[0].artifact_sha256
    for record in records:
        assert record.artifact_path is not None
        assert (tmp_path / record.artifact_path).is_file()


def test_pass2_failure_resumes_from_pass2_without_replaying_pass1(tmp_path):
    failed = FakeGateway(
        [
            _pass1(),
            StructuredCallError(
                FailureCode.SDK_ERROR,
                "SDK retries exhausted",
                retryable=True,
            ),
        ]
    )
    first = _run(tmp_path, failed)
    assert first.pipeline_result.failure.code == FailureCode.SDK_ERROR

    resumed = FakeGateway([_pass2()])
    second = _run(tmp_path, resumed)

    assert second.pipeline_result.succeeded is True
    assert [call["model"] for call in resumed.calls] == ["grader-model"]
    assert second.resumed_stages == (JournalStage.PASS1_GENERATED,)
    assert second.invalidation_reason == "pass2_retry_after_failure"
    records = read_journal(tmp_path / "journal.jsonl")
    assert [record.stage for record in records] == [
        JournalStage.PASS1_GENERATED,
        JournalStage.PASS2_GRADED,
        JournalStage.PASS2_GRADED,
    ]


def test_audit_failure_resumes_at_audit_with_zero_llm_calls(tmp_path):
    _run(tmp_path, FakeGateway([_pass1(), _pass2()]))
    snapshot = _snapshot()
    record_audit_failure(
        run_dir=tmp_path,
        run_id="run-1",
        source_document_id=snapshot.source_document_id,
        identity=_identity(snapshot),
        failure=StageFailure(
            stage=FailureStage.AUDIT,
            code=FailureCode.AUDIT_FAILED,
            retryable=False,
            message="deterministic audit failed",
        ),
    )

    gateway = FakeGateway([])
    resumed = _run(tmp_path, gateway)

    assert resumed.pipeline_result.succeeded is True
    assert gateway.calls == []
    assert resumed.resumed_stages == (
        JournalStage.PASS1_GENERATED,
        JournalStage.PASS2_GRADED,
    )
    assert resumed.next_stage == JournalStage.AUDITED
    assert resumed.completed_noop is False
    assert resumed.invalidation_reason == "audit_retry_after_failure"


def test_completed_audit_is_an_idempotent_noop(tmp_path):
    _run(tmp_path, FakeGateway([_pass1(), _pass2()]))
    snapshot = _snapshot()
    identity = _identity(snapshot)
    audit_artifact = _audit_artifact(tmp_path)
    first_audit = record_audit_success(
        run_dir=tmp_path,
        run_id="run-1",
        source_document_id=snapshot.source_document_id,
        identity=identity,
        audit_artifact=audit_artifact,
    )
    duplicate_audit = record_audit_success(
        run_dir=tmp_path,
        run_id="run-1",
        source_document_id=snapshot.source_document_id,
        identity=identity,
        audit_artifact=audit_artifact,
    )
    assert duplicate_audit.sequence == first_audit.sequence

    gateway = FakeGateway([])
    completed = _run(tmp_path, gateway)
    assert completed.completed_noop is True
    assert completed.next_stage is None
    assert gateway.calls == []
    assert completed.resumed_stages == (
        JournalStage.PASS1_GENERATED,
        JournalStage.PASS2_GRADED,
        JournalStage.AUDITED,
    )

    Path(audit_artifact.audit_artifact_path).write_text(
        "tampered",
        encoding="utf-8",
    )
    corrupt_gateway = FakeGateway([])
    corrupt = _run(tmp_path, corrupt_gateway)
    assert corrupt.pipeline_result.failure.code == FailureCode.JOURNAL_CORRUPT
    assert corrupt_gateway.calls == []


@pytest.mark.parametrize(
    ("change", "expected_models", "reason"),
    [
        ("generator", ["generator-v2", "grader-model"], "generator_model_changed"),
        ("grader", ["grader-v2"], "grader_model_or_pass1_artifact_changed"),
        (
            "source",
            ["generator-model", "grader-model"],
            "source_sha256_changed",
        ),
        (
            "selection",
            ["generator-model", "grader-model"],
            "selection_sha256_changed",
        ),
        (
            "prompt",
            ["generator-model", "grader-model"],
            "prompt_bundle_sha256_changed",
        ),
    ],
)
def test_hash_and_model_changes_invalidate_only_required_stages(
    tmp_path,
    change,
    expected_models,
    reason,
):
    _run(tmp_path, FakeGateway([_pass1(), _pass2()]))
    snapshot = _snapshot()
    config = _config()
    prompt_bundle = build_prompt_bundle()
    selection_config = None
    if change == "generator":
        config = _config(generator_model="generator-v2")
    elif change == "grader":
        config = _config(grader_model="grader-v2")
    elif change == "source":
        snapshot = _snapshot(source_hash="b" * 64)
    elif change == "selection":
        selection_config = SelectionConfig(page_threshold=84)
        prompt_bundle = build_prompt_bundle(selection_config)
    elif change == "prompt":
        original = prompt_bundle.definition("pass1")
        prompt_bundle = prompt_bundle.with_definition(
            PromptDefinition(
                name=original.name,
                system_prompt=original.system_prompt + "\nchanged",
                user_template=original.user_template,
                response_model=original.response_model,
            )
        )

    gateway = FakeGateway(
        [_pass2()] if change == "grader" else [_pass1(), _pass2()]
    )
    result = _run(
        tmp_path,
        gateway,
        snapshot=snapshot,
        config=config,
        prompt_bundle=prompt_bundle,
        selection_config=selection_config,
    )

    assert result.pipeline_result.succeeded is True
    assert [call["model"] for call in gateway.calls] == expected_models
    assert result.invalidation_reason == reason


def test_audit_config_change_keeps_both_llm_stages(tmp_path):
    _run(tmp_path, FakeGateway([_pass1(), _pass2()]))
    snapshot = _snapshot()
    record_audit_success(
        run_dir=tmp_path,
        run_id="run-1",
        source_document_id=snapshot.source_document_id,
        identity=_identity(snapshot),
        audit_artifact=_audit_artifact(tmp_path),
    )
    gateway = FakeGateway([])

    resumed = _run(tmp_path, gateway, audit_hash="d" * 64)

    assert resumed.pipeline_result.succeeded is True
    assert gateway.calls == []
    assert resumed.next_stage == JournalStage.AUDITED
    assert resumed.invalidation_reason == "audit_config_changed"


def test_torn_final_jsonl_is_ignored_then_truncated_before_append(tmp_path):
    first = FakeGateway(
        [
            _pass1(),
            StructuredCallError(
                FailureCode.SDK_ERROR,
                "retry later",
                retryable=True,
            ),
        ]
    )
    _run(tmp_path, first)
    journal_path = tmp_path / "journal.jsonl"
    with journal_path.open("ab") as stream:
        stream.write(b'{"sequence":999')

    assert len(read_journal(journal_path)) == 2
    _run(tmp_path, FakeGateway([_pass2()]))

    records = read_journal(journal_path)
    assert [record.sequence for record in records] == [1, 2, 3]
    assert journal_path.read_bytes().endswith(b"\n")


def test_invalid_complete_record_and_conflicting_duplicate_are_corrupt(tmp_path):
    _run(tmp_path, FakeGateway([_pass1(), _pass2()]))
    journal_path = tmp_path / "journal.jsonl"
    with journal_path.open("ab") as stream:
        stream.write(b"not-json\n")
    with pytest.raises(JournalCorruptError, match="invalid complete"):
        read_journal(journal_path)

    other = tmp_path / "duplicate"
    _run(other, FakeGateway([_pass1(), _pass2()]))
    records = read_journal(other / "journal.jsonl")
    duplicate = records[0].model_copy(update={"sequence": 3})
    with JournalWriter(other, "run-1") as writer:
        writer.append(duplicate)
    assert len(read_journal(other / "journal.jsonl")) == 3

    conflicting = duplicate.model_copy(
        update={
            "sequence": 4,
            "artifact_sha256": "d" * 64,
            "artifact_path": "artifacts/conflicting.json",
        }
    )
    with JournalWriter(other, "run-1") as writer:
        writer.append(conflicting)
    with pytest.raises(JournalCorruptError, match="conflicting successful"):
        read_journal(other / "journal.jsonl")


def test_concurrent_writer_is_rejected_and_runner_returns_typed_failure(tmp_path):
    with JournalWriter(tmp_path, "run-1"):
        with pytest.raises(JournalWriterConflict):
            with JournalWriter(tmp_path, "run-1"):
                pass
        result = _run(tmp_path, FakeGateway([_pass1(), _pass2()]))

    assert result.pipeline_result.failure.code == FailureCode.WRITER_CONFLICT


def test_tampered_artifact_returns_typed_corruption_without_llm_call(tmp_path):
    _run(tmp_path, FakeGateway([_pass1(), _pass2()]))
    first = read_journal(tmp_path / "journal.jsonl")[0]
    reference = ArtifactReference(
        path=first.artifact_path,
        sha256=first.artifact_sha256,
    )
    (tmp_path / reference.path).write_text("tampered", encoding="utf-8")
    gateway = FakeGateway([])

    result = _run(tmp_path, gateway)

    assert result.pipeline_result.failure.code == FailureCode.JOURNAL_CORRUPT
    assert gateway.calls == []


def test_unexpected_gateway_exception_does_not_leak_sensitive_message_to_journal(
    tmp_path,
):
    gateway = FakeGateway([RuntimeError("SECRET-SOURCE-CONTENT")])
    result = _run(tmp_path, gateway)

    assert result.pipeline_result.failure.code == FailureCode.SDK_ERROR
    records = read_journal(tmp_path / "journal.jsonl")
    serialized = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    assert len(records) == 1
    assert "SECRET-SOURCE-CONTENT" not in serialized
