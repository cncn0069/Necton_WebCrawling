"""Append-only stage journal, content-addressed artifacts, and resume planning."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from rd2.source_generation.contracts import (
    AuditStageArtifact,
    DocumentPipelineResult,
    DocumentSelection,
    FailureCode,
    FailureStage,
    GenerationTarget,
    JournalRecord,
    JournalStage,
    JournalStatus,
    Pass1StageArtifact,
    Pass2StageArtifact,
    SourceDocumentSnapshot,
    StageFailure,
)
from rd2.source_generation.document_select import SelectionConfig
from rd2.source_generation.pipeline import (
    PipelineConfig,
    StructuredOutputGateway,
    execute_pass1,
    execute_pass2,
)
from rd2.source_generation.legacy_synthetic import (
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.prompts import (
    PromptBundle,
    build_prompt_bundle,
)

ArtifactT = TypeVar("ArtifactT", bound=BaseModel)


class JournalError(RuntimeError):
    """Base class for durable execution-state failures."""


class JournalCorruptError(JournalError):
    """The journal or an artifact violates its persisted contract."""


class JournalWriterConflict(JournalError):
    """Another process owns the run directory writer lock."""


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    sha256: str


@dataclass(frozen=True)
class JournalIdentity:
    source_sha256: str
    selection_sha256: str
    prompt_bundle_sha256: str
    generator_model: str
    grader_model: str
    audit_config_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "source_sha256",
            "selection_sha256",
            "prompt_bundle_sha256",
            "audit_config_sha256",
        ):
            value = getattr(self, name)
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        if not self.generator_model.strip() or not self.grader_model.strip():
            raise ValueError("journal model IDs must not be blank")


@dataclass(frozen=True)
class ResumePlan:
    pass1_record: JournalRecord | None
    pass2_record: JournalRecord | None
    audit_record: JournalRecord | None
    next_stage: JournalStage | None
    completed_noop: bool
    invalidation_reason: str | None = None


@dataclass(frozen=True)
class JournaledPipelineResult:
    pipeline_result: DocumentPipelineResult
    resumed_stages: tuple[JournalStage, ...]
    executed_stages: tuple[JournalStage, ...]
    next_stage: JournalStage | None
    completed_noop: bool
    invalidation_reason: str | None = None


def _canonical_model_bytes(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", exclude_computed_fields=True)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ArtifactStore:
    """Content-addressed JSON artifacts rooted in one run directory."""

    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir)
        self.artifacts_dir = self.run_dir / "artifacts"

    def write(self, artifact: BaseModel) -> ArtifactReference:
        payload = _canonical_model_bytes(artifact)
        digest = sha256(payload).hexdigest()
        relative_path = PurePosixPath("artifacts", f"{digest}.json").as_posix()
        target = self.run_dir / Path(relative_path)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        if target.exists():
            if target.read_bytes() != payload:
                raise JournalCorruptError(
                    "content-addressed artifact path contains different bytes"
                )
            return ArtifactReference(path=relative_path, sha256=digest)

        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return ArtifactReference(path=relative_path, sha256=digest)

    def _resolve(self, reference: ArtifactReference) -> Path:
        pure_path = PurePosixPath(reference.path)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise JournalCorruptError("artifact path escapes the run directory")
        target = (self.run_dir / Path(*pure_path.parts)).resolve()
        root = self.run_dir.resolve()
        if not target.is_relative_to(root):
            raise JournalCorruptError("artifact path escapes the run directory")
        return target

    def read_bytes(self, reference: ArtifactReference) -> bytes:
        target = self._resolve(reference)
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise JournalCorruptError("journal artifact is missing or unreadable") from exc
        if sha256(payload).hexdigest() != reference.sha256:
            raise JournalCorruptError("journal artifact hash mismatch")
        return payload

    def read(
        self,
        reference: ArtifactReference,
        artifact_model: type[ArtifactT],
    ) -> ArtifactT:
        payload = self.read_bytes(reference)
        try:
            return artifact_model.model_validate_json(payload)
        except ValidationError as exc:
            raise JournalCorruptError(
                "journal artifact violates its typed contract"
            ) from exc


def _artifact_reference(record: JournalRecord) -> ArtifactReference:
    if record.artifact_path is None or record.artifact_sha256 is None:
        raise JournalCorruptError("successful journal record has no artifact reference")
    return ArtifactReference(path=record.artifact_path, sha256=record.artifact_sha256)


def _success_identity(record: JournalRecord) -> tuple[object, ...]:
    return (
        record.run_id,
        record.source_document_id,
        record.stage,
        record.source_sha256,
        record.selection_sha256,
        record.prompt_bundle_sha256,
        record.model_id,
        record.upstream_artifact_sha256,
        record.stage_config_sha256,
    )


def read_journal(path: Path | str) -> tuple[JournalRecord, ...]:
    """Read complete JSONL records; ignore only a torn final non-newline fragment."""

    journal_path = Path(path)
    if not journal_path.exists():
        return ()
    try:
        payload = journal_path.read_bytes()
    except OSError as exc:
        raise JournalCorruptError("journal is unreadable") from exc
    complete_end = payload.rfind(b"\n")
    if complete_end < 0:
        return ()
    complete = payload[: complete_end + 1]

    records: list[JournalRecord] = []
    successful: dict[tuple[object, ...], ArtifactReference] = {}
    run_id: str | None = None
    expected_sequence = 1
    for line in complete.splitlines():
        if not line:
            raise JournalCorruptError("journal contains an empty record")
        try:
            record = JournalRecord.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise JournalCorruptError("journal contains an invalid complete record") from exc
        if record.sequence != expected_sequence:
            raise JournalCorruptError("journal sequence is not contiguous")
        expected_sequence += 1
        if run_id is None:
            run_id = record.run_id
        elif record.run_id != run_id:
            raise JournalCorruptError("journal mixes multiple run IDs")
        if record.status == JournalStatus.SUCCEEDED:
            identity = _success_identity(record)
            reference = _artifact_reference(record)
            previous = successful.get(identity)
            if previous is not None and previous != reference:
                raise JournalCorruptError(
                    "journal contains conflicting successful stage records"
                )
            successful[identity] = reference
        records.append(record)
    return tuple(records)


class JournalWriter:
    """Exclusive writer for one append-only run journal."""

    def __init__(self, run_dir: Path | str, run_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.journal_path = self.run_dir / "journal.jsonl"
        self.lock_path = self.run_dir / ".writer.lock"
        self._owner_token = uuid4().hex
        self._entered = False
        self._records: tuple[JournalRecord, ...] = ()

    @property
    def records(self) -> tuple[JournalRecord, ...]:
        if not self._entered:
            raise RuntimeError("journal writer is not active")
        return self._records

    @property
    def next_sequence(self) -> int:
        return len(self.records) + 1

    def __enter__(self) -> "JournalWriter":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        owner = json.dumps(
            {
                "run_id": self.run_id,
                "pid": os.getpid(),
                "owner_token": self._owner_token,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise JournalWriterConflict(
                "run directory already has an active writer lock"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(owner)
                stream.flush()
                os.fsync(stream.fileno())
            self._entered = True
            self._truncate_torn_tail()
            self._records = read_journal(self.journal_path)
            if self._records and self._records[0].run_id != self.run_id:
                raise JournalCorruptError("run ID does not match existing journal")
            return self
        except Exception:
            self._release_lock()
            raise

    def _truncate_torn_tail(self) -> None:
        if not self.journal_path.exists():
            return
        payload = self.journal_path.read_bytes()
        if not payload or payload.endswith(b"\n"):
            return
        complete_end = payload.rfind(b"\n")
        new_size = complete_end + 1 if complete_end >= 0 else 0
        with self.journal_path.open("r+b") as stream:
            stream.truncate(new_size)
            stream.flush()
            os.fsync(stream.fileno())

    def _check_ownership(self) -> None:
        if not self._entered:
            raise RuntimeError("journal writer is not active")
        try:
            owner = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JournalWriterConflict("writer lock ownership cannot be verified") from exc
        if owner.get("owner_token") != self._owner_token:
            raise JournalWriterConflict("writer lock ownership changed")

    def append(self, record: JournalRecord) -> None:
        self._check_ownership()
        if record.run_id != self.run_id:
            raise ValueError("journal record run ID does not match writer")
        if record.sequence != self.next_sequence:
            raise ValueError("journal record sequence does not match next sequence")
        line = record.model_dump_json(exclude_computed_fields=True).encode("utf-8") + b"\n"
        with self.journal_path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self._records = (*self._records, record)

    def _release_lock(self) -> None:
        if not self._entered:
            return
        try:
            owner = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if owner.get("owner_token") == self._owner_token:
                self.lock_path.unlink()
        except (OSError, ValueError):
            pass
        self._entered = False

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._release_lock()


def _matches_common(record: JournalRecord, identity: JournalIdentity) -> bool:
    return (
        record.source_sha256 == identity.source_sha256
        and record.selection_sha256 == identity.selection_sha256
        and record.prompt_bundle_sha256 == identity.prompt_bundle_sha256
    )


def _latest_success(
    records: tuple[JournalRecord, ...],
    *,
    source_document_id: str,
    stage: JournalStage,
    predicate,
) -> JournalRecord | None:
    for record in reversed(records):
        if (
            record.source_document_id == source_document_id
            and record.stage == stage
            and record.status == JournalStatus.SUCCEEDED
            and predicate(record)
        ):
            return record
    return None


def _invalidation_reason(
    records: tuple[JournalRecord, ...],
    source_document_id: str,
    identity: JournalIdentity,
    pass1_record: JournalRecord | None,
    pass2_record: JournalRecord | None,
    audit_record: JournalRecord | None,
) -> str | None:
    document_records = tuple(
        record for record in records if record.source_document_id == source_document_id
    )
    if not document_records:
        return None
    if pass1_record is None:
        pass1_records = tuple(
            record
            for record in document_records
            if record.stage == JournalStage.PASS1_GENERATED
        )
        if not pass1_records:
            return None
        latest = pass1_records[-1]
        if (
            latest.status == JournalStatus.FAILED
            and _matches_common(latest, identity)
            and latest.model_id == identity.generator_model
        ):
            return "pass1_retry_after_failure"
        if latest.source_sha256 != identity.source_sha256:
            return "source_sha256_changed"
        if latest.selection_sha256 != identity.selection_sha256:
            return "selection_sha256_changed"
        if latest.prompt_bundle_sha256 != identity.prompt_bundle_sha256:
            return "prompt_bundle_sha256_changed"
        return "generator_model_changed"
    if pass2_record is None:
        matching_failures = tuple(
            record
            for record in document_records
            if (
                record.stage == JournalStage.PASS2_GRADED
                and record.status == JournalStatus.FAILED
                and _matches_common(record, identity)
                and record.model_id == identity.grader_model
                and record.upstream_artifact_sha256 == pass1_record.artifact_sha256
            )
        )
        if matching_failures:
            return "pass2_retry_after_failure"
        if not any(
            record.stage == JournalStage.PASS2_GRADED
            for record in document_records
        ):
            return None
        return "grader_model_or_pass1_artifact_changed"
    if audit_record is None:
        matching_audits = tuple(
            record
            for record in document_records
            if (
                record.stage == JournalStage.AUDITED
                and _matches_common(record, identity)
                and record.upstream_artifact_sha256 == pass2_record.artifact_sha256
                and record.stage_config_sha256 == identity.audit_config_sha256
            )
        )
        if matching_audits and matching_audits[-1].status == JournalStatus.FAILED:
            return "audit_retry_after_failure"
        if any(record.stage == JournalStage.AUDITED for record in document_records):
            return "audit_config_changed"
        return None
    return None


def build_resume_plan(
    *,
    records: tuple[JournalRecord, ...],
    source_document_id: str,
    identity: JournalIdentity,
    artifact_store: ArtifactStore,
) -> ResumePlan:
    pass1_record = _latest_success(
        records,
        source_document_id=source_document_id,
        stage=JournalStage.PASS1_GENERATED,
        predicate=lambda record: (
            _matches_common(record, identity)
            and record.model_id == identity.generator_model
        ),
    )
    if pass1_record is not None:
        artifact_store.read(_artifact_reference(pass1_record), Pass1StageArtifact)

    pass2_record = None
    if pass1_record is not None:
        pass2_record = _latest_success(
            records,
            source_document_id=source_document_id,
            stage=JournalStage.PASS2_GRADED,
            predicate=lambda record: (
                _matches_common(record, identity)
                and record.model_id == identity.grader_model
                and record.upstream_artifact_sha256
                == pass1_record.artifact_sha256
            ),
        )
        if pass2_record is not None:
            artifact_store.read(_artifact_reference(pass2_record), Pass2StageArtifact)

    audit_record = None
    if pass2_record is not None:
        matching_audit = tuple(
            record
            for record in records
            if (
                record.source_document_id == source_document_id
                and record.stage == JournalStage.AUDITED
                and _matches_common(record, identity)
                and record.upstream_artifact_sha256 == pass2_record.artifact_sha256
                and record.stage_config_sha256 == identity.audit_config_sha256
            )
        )
        if matching_audit and matching_audit[-1].status == JournalStatus.SUCCEEDED:
            audit_record = matching_audit[-1]
            audit_marker = artifact_store.read(
                _artifact_reference(audit_record),
                AuditStageArtifact,
            )
            audit_path = Path(audit_marker.audit_artifact_path)
            if not audit_path.is_absolute():
                audit_path = artifact_store.run_dir / audit_path
            try:
                audit_payload = audit_path.read_bytes()
            except OSError as exc:
                raise JournalCorruptError(
                    "completed audit artifact is missing or unreadable"
                ) from exc
            if (
                sha256(audit_payload).hexdigest()
                != audit_marker.audit_artifact_sha256
            ):
                raise JournalCorruptError("completed audit artifact hash mismatch")

    if pass1_record is None:
        next_stage = JournalStage.PASS1_GENERATED
    elif pass2_record is None:
        next_stage = JournalStage.PASS2_GRADED
    elif audit_record is None:
        next_stage = JournalStage.AUDITED
    else:
        next_stage = None
    return ResumePlan(
        pass1_record=pass1_record,
        pass2_record=pass2_record,
        audit_record=audit_record,
        next_stage=next_stage,
        completed_noop=next_stage is None,
        invalidation_reason=_invalidation_reason(
            records,
            source_document_id,
            identity,
            pass1_record,
            pass2_record,
            audit_record,
        ),
    )


def _journal_failure_result(
    source_document_id: str,
    *,
    code: FailureCode,
    message: str,
) -> JournaledPipelineResult:
    return JournaledPipelineResult(
        pipeline_result=DocumentPipelineResult(
            source_document_id=source_document_id,
            failure=StageFailure(
                stage=FailureStage.MANIFEST,
                code=code,
                retryable=False,
                message=message,
            ),
        ),
        resumed_stages=(),
        executed_stages=(),
        next_stage=None,
        completed_noop=False,
    )


def _pass1_record(
    *,
    writer: JournalWriter,
    run_id: str,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    identity: JournalIdentity,
    status: JournalStatus,
    artifact: ArtifactReference | None = None,
    failure: StageFailure | None = None,
    token_usage=None,
) -> JournalRecord:
    return JournalRecord(
        run_id=run_id,
        sequence=writer.next_sequence,
        source_document_id=snapshot.source_document_id,
        stage=JournalStage.PASS1_GENERATED,
        status=status,
        recorded_at=datetime.now(UTC),
        source_sha256=snapshot.source_sha256,
        selection_sha256=selection.selection_sha256,
        prompt_bundle_sha256=identity.prompt_bundle_sha256,
        model_id=identity.generator_model,
        artifact_sha256=artifact.sha256 if artifact else None,
        artifact_path=artifact.path if artifact else None,
        token_usage=token_usage,
        failure=failure,
    )


def _pass2_record(
    *,
    writer: JournalWriter,
    run_id: str,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    identity: JournalIdentity,
    pass1_artifact_sha256: str,
    status: JournalStatus,
    artifact: ArtifactReference | None = None,
    failure: StageFailure | None = None,
    token_usage=None,
) -> JournalRecord:
    return JournalRecord(
        run_id=run_id,
        sequence=writer.next_sequence,
        source_document_id=snapshot.source_document_id,
        stage=JournalStage.PASS2_GRADED,
        status=status,
        recorded_at=datetime.now(UTC),
        source_sha256=snapshot.source_sha256,
        selection_sha256=selection.selection_sha256,
        prompt_bundle_sha256=identity.prompt_bundle_sha256,
        model_id=identity.grader_model,
        artifact_sha256=artifact.sha256 if artifact else None,
        artifact_path=artifact.path if artifact else None,
        upstream_artifact_sha256=pass1_artifact_sha256,
        token_usage=token_usage,
        failure=failure,
    )


def _pipeline_from_artifacts(
    *,
    source_document_id: str,
    pass1_artifact: Pass1StageArtifact,
    pass2_artifact: Pass2StageArtifact,
) -> DocumentPipelineResult:
    return DocumentPipelineResult(
        source_document_id=source_document_id,
        pass1_result=pass1_artifact.pass1_result,
        pass2_assessment=pass2_artifact.pass2_assessment,
        pass1_receipt=pass1_artifact.receipt,
        pass2_receipt=pass2_artifact.receipt,
        generation_provenance=pass1_artifact.generation_provenance,
        comparison=pass2_artifact.comparison,
    )


def run_two_pass_with_journal(
    *,
    run_dir: Path | str,
    run_id: str,
    snapshot: SourceDocumentSnapshot,
    selection: DocumentSelection,
    counterfactual_target: GenerationTarget,
    gateway: StructuredOutputGateway,
    config: PipelineConfig,
    audit_config_sha256: str,
    selection_config: SelectionConfig | None = None,
    prompt_bundle: PromptBundle | None = None,
    fully_synthetic_generator: FullySyntheticDocumentGenerator | None = None,
    fully_synthetic_context: FullySyntheticContext | None = None,
) -> JournaledPipelineResult:
    """Resume one document from its last verified stage without replaying paid calls."""

    resolved_selection_config = selection_config or SelectionConfig()
    resolved_prompt_bundle = prompt_bundle or build_prompt_bundle(
        resolved_selection_config
    )
    try:
        identity = JournalIdentity(
            source_sha256=snapshot.source_sha256,
            selection_sha256=selection.selection_sha256,
            prompt_bundle_sha256=resolved_prompt_bundle.sha256,
            generator_model=config.generator_model,
            grader_model=config.grader_model,
            audit_config_sha256=audit_config_sha256,
        )
    except ValueError:
        return _journal_failure_result(
            snapshot.source_document_id,
            code=FailureCode.MANIFEST_INVALID,
            message="journal identity contains an invalid hash or model ID",
        )
    artifact_store = ArtifactStore(run_dir)

    try:
        with JournalWriter(run_dir, run_id) as writer:
            plan = build_resume_plan(
                records=writer.records,
                source_document_id=snapshot.source_document_id,
                identity=identity,
                artifact_store=artifact_store,
            )
            resumed: list[JournalStage] = []
            executed: list[JournalStage] = []

            if plan.pass1_record is not None:
                pass1_reference = _artifact_reference(plan.pass1_record)
                pass1_artifact = artifact_store.read(
                    pass1_reference,
                    Pass1StageArtifact,
                )
                resumed.append(JournalStage.PASS1_GENERATED)
            else:
                pass1 = execute_pass1(
                    snapshot=snapshot,
                    selection=selection,
                    counterfactual_target=counterfactual_target,
                    gateway=gateway,
                    config=config,
                    selection_config=resolved_selection_config,
                    prompt_bundle=resolved_prompt_bundle,
                    fully_synthetic_generator=fully_synthetic_generator,
                    fully_synthetic_context=fully_synthetic_context,
                )
                executed.append(JournalStage.PASS1_GENERATED)
                if pass1.failure is not None:
                    if pass1.failure.stage == FailureStage.PASS1:
                        writer.append(
                            _pass1_record(
                                writer=writer,
                                run_id=run_id,
                                snapshot=snapshot,
                                selection=selection,
                                identity=identity,
                                status=JournalStatus.FAILED,
                                failure=pass1.failure,
                                token_usage=(
                                    pass1.receipt.token_usage
                                    if pass1.receipt is not None
                                    else None
                                ),
                            )
                        )
                    return JournaledPipelineResult(
                        pipeline_result=DocumentPipelineResult(
                            source_document_id=snapshot.source_document_id,
                            pass1_result=pass1.result,
                            pass1_receipt=pass1.receipt,
                            failure=pass1.failure,
                        ),
                        resumed_stages=tuple(resumed),
                        executed_stages=tuple(executed),
                        next_stage=JournalStage.PASS1_GENERATED,
                        completed_noop=False,
                        invalidation_reason=plan.invalidation_reason,
                    )
                assert pass1.result is not None
                assert pass1.receipt is not None
                pass1_artifact = Pass1StageArtifact(
                    pass1_result=pass1.result,
                    receipt=pass1.receipt,
                    generation_provenance=pass1.provenance,
                )
                pass1_reference = artifact_store.write(pass1_artifact)
                writer.append(
                    _pass1_record(
                        writer=writer,
                        run_id=run_id,
                        snapshot=snapshot,
                        selection=selection,
                        identity=identity,
                        status=JournalStatus.SUCCEEDED,
                        artifact=pass1_reference,
                        token_usage=pass1.receipt.token_usage,
                    )
                )

            if plan.pass2_record is not None:
                pass2_artifact = artifact_store.read(
                    _artifact_reference(plan.pass2_record),
                    Pass2StageArtifact,
                )
                resumed.append(JournalStage.PASS2_GRADED)
            else:
                pass2 = execute_pass2(
                    pass1_result=pass1_artifact.pass1_result,
                    gateway=gateway,
                    config=config,
                    selection_config=resolved_selection_config,
                    prompt_bundle=resolved_prompt_bundle,
                )
                executed.append(JournalStage.PASS2_GRADED)
                if pass2.failure is not None:
                    writer.append(
                        _pass2_record(
                            writer=writer,
                            run_id=run_id,
                            snapshot=snapshot,
                            selection=selection,
                            identity=identity,
                            pass1_artifact_sha256=pass1_reference.sha256,
                            status=JournalStatus.FAILED,
                            failure=pass2.failure,
                            token_usage=(
                                pass2.receipt.token_usage
                                if pass2.receipt is not None
                                else None
                            ),
                        )
                    )
                    return JournaledPipelineResult(
                        pipeline_result=DocumentPipelineResult(
                            source_document_id=snapshot.source_document_id,
                            pass1_result=pass1_artifact.pass1_result,
                            pass2_assessment=pass2.assessment,
                            pass1_receipt=pass1_artifact.receipt,
                            pass2_receipt=pass2.receipt,
                            generation_provenance=(
                                pass1_artifact.generation_provenance
                            ),
                            failure=pass2.failure,
                        ),
                        resumed_stages=tuple(resumed),
                        executed_stages=tuple(executed),
                        next_stage=JournalStage.PASS2_GRADED,
                        completed_noop=False,
                        invalidation_reason=plan.invalidation_reason,
                    )
                assert pass2.assessment is not None
                assert pass2.receipt is not None
                assert pass2.comparison is not None
                pass2_artifact = Pass2StageArtifact(
                    pass2_assessment=pass2.assessment,
                    receipt=pass2.receipt,
                    comparison=pass2.comparison,
                )
                pass2_reference = artifact_store.write(pass2_artifact)
                writer.append(
                    _pass2_record(
                        writer=writer,
                        run_id=run_id,
                        snapshot=snapshot,
                        selection=selection,
                        identity=identity,
                        pass1_artifact_sha256=pass1_reference.sha256,
                        status=JournalStatus.SUCCEEDED,
                        artifact=pass2_reference,
                        token_usage=pass2.receipt.token_usage,
                    )
                )

            result = _pipeline_from_artifacts(
                source_document_id=snapshot.source_document_id,
                pass1_artifact=pass1_artifact,
                pass2_artifact=pass2_artifact,
            )
            if plan.audit_record is not None:
                resumed.append(JournalStage.AUDITED)
            return JournaledPipelineResult(
                pipeline_result=result,
                resumed_stages=tuple(resumed),
                executed_stages=tuple(executed),
                next_stage=None if plan.audit_record is not None else JournalStage.AUDITED,
                completed_noop=plan.audit_record is not None,
                invalidation_reason=plan.invalidation_reason,
            )
    except JournalWriterConflict:
        return _journal_failure_result(
            snapshot.source_document_id,
            code=FailureCode.WRITER_CONFLICT,
            message="run directory is owned by another writer",
        )
    except (JournalCorruptError, OSError):
        return _journal_failure_result(
            snapshot.source_document_id,
            code=FailureCode.JOURNAL_CORRUPT,
            message="journal or stage artifact failed integrity validation",
        )


def _matching_pass2_record(
    *,
    writer: JournalWriter,
    source_document_id: str,
    identity: JournalIdentity,
    artifact_store: ArtifactStore,
) -> JournalRecord:
    plan = build_resume_plan(
        records=writer.records,
        source_document_id=source_document_id,
        identity=identity,
        artifact_store=artifact_store,
    )
    if plan.pass2_record is None:
        raise JournalCorruptError("cannot record audit before a verified Pass 2")
    return plan.pass2_record


def record_audit_success(
    *,
    run_dir: Path | str,
    run_id: str,
    source_document_id: str,
    identity: JournalIdentity,
    audit_artifact: AuditStageArtifact,
) -> JournalRecord:
    artifact_store = ArtifactStore(run_dir)
    with JournalWriter(run_dir, run_id) as writer:
        pass2_record = _matching_pass2_record(
            writer=writer,
            source_document_id=source_document_id,
            identity=identity,
            artifact_store=artifact_store,
        )
        reference = artifact_store.write(audit_artifact)
        for record in reversed(writer.records):
            if (
                record.source_document_id == source_document_id
                and record.stage == JournalStage.AUDITED
                and record.status == JournalStatus.SUCCEEDED
                and _matches_common(record, identity)
                and record.upstream_artifact_sha256 == pass2_record.artifact_sha256
                and record.stage_config_sha256 == identity.audit_config_sha256
                and _artifact_reference(record) == reference
            ):
                return record
        record = JournalRecord(
            run_id=run_id,
            sequence=writer.next_sequence,
            source_document_id=source_document_id,
            stage=JournalStage.AUDITED,
            status=JournalStatus.SUCCEEDED,
            recorded_at=datetime.now(UTC),
            source_sha256=identity.source_sha256,
            selection_sha256=identity.selection_sha256,
            prompt_bundle_sha256=identity.prompt_bundle_sha256,
            artifact_sha256=reference.sha256,
            artifact_path=reference.path,
            upstream_artifact_sha256=pass2_record.artifact_sha256,
            stage_config_sha256=identity.audit_config_sha256,
        )
        writer.append(record)
        return record


def record_audit_failure(
    *,
    run_dir: Path | str,
    run_id: str,
    source_document_id: str,
    identity: JournalIdentity,
    failure: StageFailure,
) -> JournalRecord:
    if failure.stage != FailureStage.AUDIT:
        raise ValueError("audit journal failure requires FailureStage.AUDIT")
    artifact_store = ArtifactStore(run_dir)
    with JournalWriter(run_dir, run_id) as writer:
        pass2_record = _matching_pass2_record(
            writer=writer,
            source_document_id=source_document_id,
            identity=identity,
            artifact_store=artifact_store,
        )
        record = JournalRecord(
            run_id=run_id,
            sequence=writer.next_sequence,
            source_document_id=source_document_id,
            stage=JournalStage.AUDITED,
            status=JournalStatus.FAILED,
            recorded_at=datetime.now(UTC),
            source_sha256=identity.source_sha256,
            selection_sha256=identity.selection_sha256,
            prompt_bundle_sha256=identity.prompt_bundle_sha256,
            upstream_artifact_sha256=pass2_record.artifact_sha256,
            stage_config_sha256=identity.audit_config_sha256,
            failure=failure,
        )
        writer.append(record)
        return record
