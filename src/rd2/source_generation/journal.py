"""Append-only five-stage journal, content-addressed artifacts, and resume."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Callable, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256
from rd2.source_generation.classification_taxonomy import ClauseNumber
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    AuditStageArtifact,
    ClassificationStageArtifact,
    DocumentPipelineResult,
    DocumentSelection,
    FailureCode,
    FailureStage,
    GenerationRoute,
    GenerationStageArtifact,
    GenerationTarget,
    JournalRecord,
    JournalStage,
    JournalStatus,
    PlanningStageArtifact,
    SourceDocumentSnapshot,
    StageFailure,
    ValidationStageArtifact,
)
from rd2.source_generation.document_select import SelectionConfig
from rd2.source_generation.legacy_synthetic import (
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.pipeline import (
    PLANNER_POLICY_SHA256,
    GenerationPlanningError,
    PipelineConfig,
    StructuredOutputGateway,
    build_generation_plan,
    execute_classification,
    execute_consistency_validation,
    execute_generation,
    model_sha256,
)
from rd2.source_generation.prompts import PromptBundle, build_prompt_bundle

ArtifactT = TypeVar("ArtifactT", bound=BaseModel)


class JournalError(RuntimeError):
    """Base class for durable execution-state failures."""


class JournalCorruptError(JournalError):
    """The journal or an artifact violates its persisted contract."""


class JournalWriterConflict(JournalError):
    """Another process owns the run-directory writer lock."""


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    sha256: str


def _validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class JournalIdentity:
    """Inputs that independently invalidate each persisted stage."""

    source_sha256: str
    selection_sha256: str
    classifier_prompt_sha256: str
    generator_prompt_sha256: str
    sensitive_generator_prompt_sha256: str
    validator_prompt_sha256: str
    sensitive_validator_prompt_sha256: str
    planner_policy_sha256: str
    target_sha256: str
    classifier_model: str
    generator_model: str
    validator_model: str
    audit_config_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "source_sha256",
            "selection_sha256",
            "classifier_prompt_sha256",
            "generator_prompt_sha256",
            "sensitive_generator_prompt_sha256",
            "validator_prompt_sha256",
            "sensitive_validator_prompt_sha256",
            "planner_policy_sha256",
            "target_sha256",
            "audit_config_sha256",
        ):
            _validate_sha256(name, getattr(self, name))
        for name in (
            "classifier_model",
            "generator_model",
            "validator_model",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.validator_model in {
            self.classifier_model,
            self.generator_model,
        }:
            raise ValueError(
                "validator_model must differ from classifier_model and generator_model"
            )

    @classmethod
    def from_pipeline(
        cls,
        *,
        snapshot: SourceDocumentSnapshot,
        selection: DocumentSelection,
        target: GenerationTarget,
        config: PipelineConfig,
        prompt_bundle: PromptBundle,
        audit_config_sha256: str,
    ) -> "JournalIdentity":
        return cls(
            source_sha256=snapshot.source_sha256,
            selection_sha256=selection.selection_sha256,
            classifier_prompt_sha256=prompt_bundle.definition("classifier").sha256,
            generator_prompt_sha256=prompt_bundle.definition("generator").sha256,
            sensitive_generator_prompt_sha256=prompt_bundle.definition(
                "sensitive_generator"
            ).sha256,
            validator_prompt_sha256=prompt_bundle.definition("validator").sha256,
            sensitive_validator_prompt_sha256=prompt_bundle.definition(
                "sensitive_validator"
            ).sha256,
            planner_policy_sha256=PLANNER_POLICY_SHA256,
            target_sha256=model_sha256(target),
            classifier_model=config.classifier_model,
            generator_model=config.generator_model,
            validator_model=config.validator_model,
            audit_config_sha256=audit_config_sha256,
        )

    @property
    def planning_config_sha256(self) -> str:
        return canonical_sha256(
            {
                "planner_policy_sha256": self.planner_policy_sha256,
                "target_sha256": self.target_sha256,
            },
            normalization_version=NORMALIZATION_VERSION,
        )

    def generation_prompt_for_clause(self, clause: ClauseNumber | None) -> str:
        if clause == ClauseNumber.CLAUSE_6:
            return self.sensitive_generator_prompt_sha256
        return self.generator_prompt_sha256

    def validation_prompt_for_clause(self, clause: ClauseNumber | None) -> str:
        if clause == ClauseNumber.CLAUSE_6:
            return self.sensitive_validator_prompt_sha256
        return self.validator_prompt_sha256


@dataclass(frozen=True)
class ResumePlan:
    classification_record: JournalRecord | None
    planning_record: JournalRecord | None
    generation_record: JournalRecord | None
    validation_record: JournalRecord | None
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


@dataclass(frozen=True)
class _JournalReadState:
    records: tuple[JournalRecord, ...]
    next_sequence: int
    legacy_contract_detected: bool


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
            raise JournalCorruptError(
                "journal artifact is missing or unreadable"
            ) from exc
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
        raise JournalCorruptError(
            "successful journal record has no artifact reference"
        )
    return ArtifactReference(
        path=record.artifact_path,
        sha256=record.artifact_sha256,
    )


def _success_identity(record: JournalRecord) -> tuple[object, ...]:
    identity = (
        record.run_id,
        record.source_document_id,
        record.stage,
        record.source_sha256,
        record.selection_sha256,
        record.prompt_sha256,
        record.model_id,
        record.upstream_artifact_sha256,
        record.stage_config_sha256,
    )
    # 같은 locked plan에서 생성만 다시 수행하는 것은 정상적인 repair lineage다.
    # 생성 attempt는 content-addressed artifact hash로 서로 구분한다.
    if record.stage == JournalStage.GENERATED:
        return (*identity, record.artifact_sha256)
    return identity


def _read_journal_state(path: Path | str) -> _JournalReadState:
    journal_path = Path(path)
    if not journal_path.exists():
        return _JournalReadState((), 1, False)
    try:
        payload = journal_path.read_bytes()
    except OSError as exc:
        raise JournalCorruptError("journal is unreadable") from exc

    complete_end = payload.rfind(b"\n")
    if complete_end < 0:
        return _JournalReadState((), 1, False)
    complete = payload[: complete_end + 1]

    records: list[JournalRecord] = []
    successful: dict[tuple[object, ...], ArtifactReference] = {}
    run_id: str | None = None
    expected_sequence = 1
    legacy_contract_detected = False
    for line in complete.splitlines():
        if not line:
            raise JournalCorruptError("journal contains an empty record")
        try:
            raw = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise JournalCorruptError(
                "journal contains invalid complete JSON"
            ) from exc
        if not isinstance(raw, dict):
            raise JournalCorruptError("journal record must be a JSON object")
        sequence = raw.get("sequence")
        if sequence != expected_sequence:
            raise JournalCorruptError("journal sequence is not contiguous")
        expected_sequence += 1
        raw_run_id = raw.get("run_id")
        if not isinstance(raw_run_id, str) or not raw_run_id.strip():
            raise JournalCorruptError("journal record has no valid run ID")
        if run_id is None:
            run_id = raw_run_id
        elif raw_run_id != run_id:
            raise JournalCorruptError("journal mixes multiple run IDs")

        if raw.get("contract_version") != CONTRACT_SCHEMA_VERSION:
            legacy_contract_detected = True
            continue
        try:
            record = JournalRecord.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            raise JournalCorruptError(
                "journal contains an invalid v2 record"
            ) from exc
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
    return _JournalReadState(
        records=tuple(records),
        next_sequence=expected_sequence,
        legacy_contract_detected=legacy_contract_detected,
    )


def read_journal(path: Path | str) -> tuple[JournalRecord, ...]:
    """Read verified v2 records and retain, but never reuse, legacy lines."""

    return _read_journal_state(path).records


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
        self._next_sequence = 1
        self._legacy_contract_detected = False

    @property
    def records(self) -> tuple[JournalRecord, ...]:
        if not self._entered:
            raise RuntimeError("journal writer is not active")
        return self._records

    @property
    def next_sequence(self) -> int:
        if not self._entered:
            raise RuntimeError("journal writer is not active")
        return self._next_sequence

    @property
    def legacy_contract_detected(self) -> bool:
        if not self._entered:
            raise RuntimeError("journal writer is not active")
        return self._legacy_contract_detected

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
            state = _read_journal_state(self.journal_path)
            self._records = state.records
            self._next_sequence = state.next_sequence
            self._legacy_contract_detected = state.legacy_contract_detected
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
            raise JournalWriterConflict(
                "writer lock ownership cannot be verified"
            ) from exc
        if owner.get("owner_token") != self._owner_token:
            raise JournalWriterConflict("writer lock ownership changed")

    def append(self, record: JournalRecord) -> None:
        self._check_ownership()
        if record.run_id != self.run_id:
            raise ValueError("journal record run ID does not match writer")
        if record.sequence != self.next_sequence:
            raise ValueError("journal record sequence does not match next sequence")
        line = (
            record.model_dump_json(exclude_computed_fields=True).encode("utf-8")
            + b"\n"
        )
        with self.journal_path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self._records = (*self._records, record)
        self._next_sequence += 1

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


def _latest_success(
    records: tuple[JournalRecord, ...],
    *,
    source_document_id: str,
    stage: JournalStage,
    predicate: Callable[[JournalRecord], bool],
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


def _document_records(
    records: tuple[JournalRecord, ...],
    source_document_id: str,
) -> tuple[JournalRecord, ...]:
    return tuple(
        record
        for record in records
        if record.source_document_id == source_document_id
    )


def _base_matches(record: JournalRecord, identity: JournalIdentity) -> bool:
    return (
        record.source_sha256 == identity.source_sha256
        and record.selection_sha256 == identity.selection_sha256
    )


def _load_verified_chain(
    *,
    records: tuple[JournalRecord, ...],
    source_document_id: str,
    identity: JournalIdentity,
    artifact_store: ArtifactStore,
) -> tuple[
    JournalRecord | None,
    JournalRecord | None,
    JournalRecord | None,
    JournalRecord | None,
    JournalRecord | None,
]:
    classified = _latest_success(
        records,
        source_document_id=source_document_id,
        stage=JournalStage.CLASSIFIED,
        predicate=lambda record: (
            _base_matches(record, identity)
            and record.prompt_sha256 == identity.classifier_prompt_sha256
            and record.model_id == identity.classifier_model
        ),
    )
    if classified is None:
        return None, None, None, None, None
    classified_artifact = artifact_store.read(
        _artifact_reference(classified),
        ClassificationStageArtifact,
    )

    planned = _latest_success(
        records,
        source_document_id=source_document_id,
        stage=JournalStage.PLANNED,
        predicate=lambda record: (
            _base_matches(record, identity)
            and record.upstream_artifact_sha256 == classified.artifact_sha256
            and record.stage_config_sha256 == identity.planning_config_sha256
        ),
    )
    if planned is None:
        return classified, None, None, None, None
    planned_artifact = artifact_store.read(
        _artifact_reference(planned),
        PlanningStageArtifact,
    )
    if (
        planned_artifact.generation_plan.source_assessment_sha256
        != model_sha256(classified_artifact.source_assessment)
    ):
        raise JournalCorruptError(
            "planning artifact does not reference the classified assessment"
        )

    plan = planned_artifact.generation_plan
    source_free = plan.generation_route == GenerationRoute.FULLY_SYNTHETIC
    expected_generation_prompt = (
        None
        if source_free
        else identity.generation_prompt_for_clause(plan.final_target.clause_no)
    )
    expected_generation_model = None if source_free else identity.generator_model
    generated = _latest_success(
        records,
        source_document_id=source_document_id,
        stage=JournalStage.GENERATED,
        predicate=lambda record: (
            _base_matches(record, identity)
            and record.upstream_artifact_sha256 == planned.artifact_sha256
            and record.prompt_sha256 == expected_generation_prompt
            and record.model_id == expected_generation_model
        ),
    )
    if generated is None:
        return classified, planned, None, None, None
    generated_artifact = artifact_store.read(
        _artifact_reference(generated),
        GenerationStageArtifact,
    )
    if (
        generated_artifact.generation_artifact.plan_sha256
        != model_sha256(plan)
    ):
        raise JournalCorruptError(
            "generation artifact does not reference the locked plan"
        )

    expected_validation_prompt = identity.validation_prompt_for_clause(
        plan.final_target.clause_no
    )
    validated = _latest_success(
        records,
        source_document_id=source_document_id,
        stage=JournalStage.VALIDATED,
        predicate=lambda record: (
            _base_matches(record, identity)
            and record.upstream_artifact_sha256 == generated.artifact_sha256
            and record.prompt_sha256 == expected_validation_prompt
            and record.model_id == identity.validator_model
        ),
    )
    if validated is None:
        return classified, planned, generated, None, None
    validated_artifact = artifact_store.read(
        _artifact_reference(validated),
        ValidationStageArtifact,
    )
    if (
        validated_artifact.generated_document_sha256
        != model_sha256(
            generated_artifact.generation_artifact.generated_document
        )
    ):
        raise JournalCorruptError(
            "validation artifact does not reference the generated document"
        )

    audited = _latest_success(
        records,
        source_document_id=source_document_id,
        stage=JournalStage.AUDITED,
        predicate=lambda record: (
            _base_matches(record, identity)
            and record.upstream_artifact_sha256 == validated.artifact_sha256
            and record.stage_config_sha256 == identity.audit_config_sha256
        ),
    )
    if audited is not None:
        artifact_store.read(
            _artifact_reference(audited),
            AuditStageArtifact,
        )
    return classified, planned, generated, validated, audited


def _invalidation_reason(
    *,
    records: tuple[JournalRecord, ...],
    source_document_id: str,
    identity: JournalIdentity,
    chain: tuple[
        JournalRecord | None,
        JournalRecord | None,
        JournalRecord | None,
        JournalRecord | None,
        JournalRecord | None,
    ],
    legacy_contract_detected: bool,
) -> str | None:
    if legacy_contract_detected:
        return FailureCode.CONTRACT_VERSION_CHANGED.value
    document_records = _document_records(records, source_document_id)
    if not document_records:
        return None
    classified, planned, generated, validated, audited = chain
    if classified is None:
        latest = document_records[-1]
        if latest.source_sha256 != identity.source_sha256:
            return "source_sha256_changed"
        if latest.selection_sha256 != identity.selection_sha256:
            return "selection_sha256_changed"
        return "classifier_prompt_or_model_changed"
    if planned is None:
        return "target_or_planner_policy_changed"
    if generated is None:
        return "generator_prompt_model_or_plan_changed"
    if validated is None:
        return "validator_prompt_model_or_generation_changed"
    if audited is None and any(
        record.stage == JournalStage.AUDITED for record in document_records
    ):
        return "audit_config_changed"
    return None


def build_resume_plan(
    *,
    records: tuple[JournalRecord, ...],
    source_document_id: str,
    identity: JournalIdentity,
    artifact_store: ArtifactStore,
    legacy_contract_detected: bool = False,
) -> ResumePlan:
    chain = _load_verified_chain(
        records=records,
        source_document_id=source_document_id,
        identity=identity,
        artifact_store=artifact_store,
    )
    classified, planned, generated, validated, audited = chain
    if classified is None:
        next_stage = JournalStage.CLASSIFIED
    elif planned is None:
        next_stage = JournalStage.PLANNED
    elif generated is None:
        next_stage = JournalStage.GENERATED
    elif validated is None:
        next_stage = JournalStage.VALIDATED
    elif audited is None:
        next_stage = JournalStage.AUDITED
    else:
        next_stage = None
    return ResumePlan(
        classification_record=classified,
        planning_record=planned,
        generation_record=generated,
        validation_record=validated,
        audit_record=audited,
        next_stage=next_stage,
        completed_noop=next_stage is None,
        invalidation_reason=_invalidation_reason(
            records=records,
            source_document_id=source_document_id,
            identity=identity,
            chain=chain,
            legacy_contract_detected=legacy_contract_detected,
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


def _stage_record(
    *,
    writer: JournalWriter,
    run_id: str,
    source_document_id: str,
    identity: JournalIdentity,
    stage: JournalStage,
    status: JournalStatus,
    prompt_sha256: str | None = None,
    model_id: str | None = None,
    artifact: ArtifactReference | None = None,
    upstream_artifact_sha256: str | None = None,
    stage_config_sha256: str | None = None,
    token_usage=None,
    failure: StageFailure | None = None,
) -> JournalRecord:
    return JournalRecord(
        run_id=run_id,
        sequence=writer.next_sequence,
        source_document_id=source_document_id,
        stage=stage,
        status=status,
        recorded_at=datetime.now(UTC),
        source_sha256=identity.source_sha256,
        selection_sha256=identity.selection_sha256,
        prompt_sha256=prompt_sha256,
        model_id=model_id,
        artifact_sha256=artifact.sha256 if artifact else None,
        artifact_path=artifact.path if artifact else None,
        upstream_artifact_sha256=upstream_artifact_sha256,
        stage_config_sha256=stage_config_sha256,
        token_usage=token_usage,
        failure=failure,
    )


def _pipeline_from_artifacts(
    *,
    source_document_id: str,
    classified: ClassificationStageArtifact,
    planned: PlanningStageArtifact,
    generated: GenerationStageArtifact,
    validated: ValidationStageArtifact,
) -> DocumentPipelineResult:
    return DocumentPipelineResult(
        source_document_id=source_document_id,
        source_assessment=classified.source_assessment,
        generation_plan=planned.generation_plan,
        generation_artifact=generated.generation_artifact,
        consistency_assessment=validated.consistency_assessment,
        classification_receipt=classified.receipt,
        generation_receipt=generated.receipt,
        validation_receipt=validated.receipt,
        comparison=validated.comparison,
    )


def run_three_stage_with_journal(
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
    sensitive_seed: str | None = None,
    fully_synthetic_generator: FullySyntheticDocumentGenerator | None = None,
    fully_synthetic_context: FullySyntheticContext | None = None,
) -> JournaledPipelineResult:
    """Resume one document from its last verified five-stage checkpoint."""

    resolved_selection_config = selection_config or SelectionConfig()
    resolved_prompt_bundle = prompt_bundle or build_prompt_bundle(
        resolved_selection_config
    )
    try:
        identity = JournalIdentity.from_pipeline(
            snapshot=snapshot,
            selection=selection,
            target=counterfactual_target,
            config=config,
            prompt_bundle=resolved_prompt_bundle,
            audit_config_sha256=audit_config_sha256,
        )
    except ValueError:
        return _journal_failure_result(
            snapshot.source_document_id,
            code=FailureCode.MANIFEST_INVALID,
            message="journal identity contains invalid hashes or model IDs",
        )

    artifact_store = ArtifactStore(run_dir)
    try:
        with JournalWriter(run_dir, run_id) as writer:
            resume = build_resume_plan(
                records=writer.records,
                source_document_id=snapshot.source_document_id,
                identity=identity,
                artifact_store=artifact_store,
                legacy_contract_detected=writer.legacy_contract_detected,
            )
            resumed: list[JournalStage] = []
            executed: list[JournalStage] = []

            if resume.classification_record is not None:
                classified_reference = _artifact_reference(
                    resume.classification_record
                )
                classified_artifact = artifact_store.read(
                    classified_reference,
                    ClassificationStageArtifact,
                )
                resumed.append(JournalStage.CLASSIFIED)
            else:
                execution = execute_classification(
                    snapshot=snapshot,
                    selection=selection,
                    gateway=gateway,
                    config=config,
                    selection_config=resolved_selection_config,
                    prompt_bundle=resolved_prompt_bundle,
                )
                executed.append(JournalStage.CLASSIFIED)
                if execution.failure is not None:
                    if execution.failure.stage == FailureStage.CLASSIFICATION:
                        writer.append(
                            _stage_record(
                                writer=writer,
                                run_id=run_id,
                                source_document_id=snapshot.source_document_id,
                                identity=identity,
                                stage=JournalStage.CLASSIFIED,
                                status=JournalStatus.FAILED,
                                prompt_sha256=identity.classifier_prompt_sha256,
                                model_id=identity.classifier_model,
                                token_usage=(
                                    execution.receipt.token_usage
                                    if execution.receipt is not None
                                    else None
                                ),
                                failure=execution.failure,
                            )
                        )
                    return JournaledPipelineResult(
                        pipeline_result=DocumentPipelineResult(
                            source_document_id=snapshot.source_document_id,
                            source_assessment=execution.assessment,
                            classification_receipt=execution.receipt,
                            failure=execution.failure,
                        ),
                        resumed_stages=tuple(resumed),
                        executed_stages=tuple(executed),
                        next_stage=JournalStage.CLASSIFIED,
                        completed_noop=False,
                        invalidation_reason=resume.invalidation_reason,
                    )
                assert execution.assessment is not None
                assert execution.receipt is not None
                classified_artifact = ClassificationStageArtifact(
                    source_assessment=execution.assessment,
                    receipt=execution.receipt,
                )
                classified_reference = artifact_store.write(classified_artifact)
                writer.append(
                    _stage_record(
                        writer=writer,
                        run_id=run_id,
                        source_document_id=snapshot.source_document_id,
                        identity=identity,
                        stage=JournalStage.CLASSIFIED,
                        status=JournalStatus.SUCCEEDED,
                        prompt_sha256=identity.classifier_prompt_sha256,
                        model_id=identity.classifier_model,
                        artifact=classified_reference,
                        token_usage=execution.receipt.token_usage,
                    )
                )

            if resume.planning_record is not None:
                planned_reference = _artifact_reference(resume.planning_record)
                planned_artifact = artifact_store.read(
                    planned_reference,
                    PlanningStageArtifact,
                )
                resumed.append(JournalStage.PLANNED)
            else:
                executed.append(JournalStage.PLANNED)
                try:
                    locked_plan = build_generation_plan(
                        assessment=classified_artifact.source_assessment,
                        requested_target=counterfactual_target,
                        snapshot=snapshot,
                        selection=selection,
                        sensitive_seed=sensitive_seed,
                        has_synthetic_generator=(
                            fully_synthetic_generator is not None
                            and fully_synthetic_context is not None
                        ),
                    )
                except GenerationPlanningError as exc:
                    failure = StageFailure(
                        stage=FailureStage.PLANNING,
                        code=exc.code,
                        retryable=exc.retryable,
                        message=str(exc),
                    )
                    writer.append(
                        _stage_record(
                            writer=writer,
                            run_id=run_id,
                            source_document_id=snapshot.source_document_id,
                            identity=identity,
                            stage=JournalStage.PLANNED,
                            status=JournalStatus.FAILED,
                            upstream_artifact_sha256=classified_reference.sha256,
                            stage_config_sha256=identity.planning_config_sha256,
                            failure=failure,
                        )
                    )
                    return JournaledPipelineResult(
                        pipeline_result=DocumentPipelineResult(
                            source_document_id=snapshot.source_document_id,
                            source_assessment=classified_artifact.source_assessment,
                            classification_receipt=classified_artifact.receipt,
                            failure=failure,
                        ),
                        resumed_stages=tuple(resumed),
                        executed_stages=tuple(executed),
                        next_stage=JournalStage.PLANNED,
                        completed_noop=False,
                        invalidation_reason=resume.invalidation_reason,
                    )
                planned_artifact = PlanningStageArtifact(
                    generation_plan=locked_plan
                )
                planned_reference = artifact_store.write(planned_artifact)
                writer.append(
                    _stage_record(
                        writer=writer,
                        run_id=run_id,
                        source_document_id=snapshot.source_document_id,
                        identity=identity,
                        stage=JournalStage.PLANNED,
                        status=JournalStatus.SUCCEEDED,
                        artifact=planned_reference,
                        upstream_artifact_sha256=classified_reference.sha256,
                        stage_config_sha256=identity.planning_config_sha256,
                    )
                )

            locked_plan = planned_artifact.generation_plan
            source_free = (
                locked_plan.generation_route
                == GenerationRoute.FULLY_SYNTHETIC
            )
            generation_prompt_sha256 = (
                None
                if source_free
                else identity.generation_prompt_for_clause(
                    locked_plan.final_target.clause_no
                )
            )
            generation_model = None if source_free else identity.generator_model

            if resume.generation_record is not None:
                generated_reference = _artifact_reference(
                    resume.generation_record
                )
                generated_artifact = artifact_store.read(
                    generated_reference,
                    GenerationStageArtifact,
                )
                resumed.append(JournalStage.GENERATED)
            else:
                execution = execute_generation(
                    snapshot=snapshot,
                    selection=selection,
                    assessment=classified_artifact.source_assessment,
                    plan=locked_plan,
                    gateway=gateway,
                    config=config,
                    selection_config=resolved_selection_config,
                    prompt_bundle=resolved_prompt_bundle,
                    sensitive_seed=sensitive_seed,
                    fully_synthetic_generator=fully_synthetic_generator,
                    fully_synthetic_context=fully_synthetic_context,
                )
                executed.append(JournalStage.GENERATED)
                if execution.failure is not None:
                    writer.append(
                        _stage_record(
                            writer=writer,
                            run_id=run_id,
                            source_document_id=snapshot.source_document_id,
                            identity=identity,
                            stage=JournalStage.GENERATED,
                            status=JournalStatus.FAILED,
                            prompt_sha256=generation_prompt_sha256,
                            model_id=generation_model,
                            upstream_artifact_sha256=planned_reference.sha256,
                            token_usage=(
                                execution.receipt.token_usage
                                if execution.receipt is not None
                                else None
                            ),
                            failure=execution.failure,
                        )
                    )
                    return JournaledPipelineResult(
                        pipeline_result=DocumentPipelineResult(
                            source_document_id=snapshot.source_document_id,
                            source_assessment=classified_artifact.source_assessment,
                            generation_plan=locked_plan,
                            generation_artifact=execution.artifact,
                            classification_receipt=classified_artifact.receipt,
                            generation_receipt=execution.receipt,
                            failure=execution.failure,
                        ),
                        resumed_stages=tuple(resumed),
                        executed_stages=tuple(executed),
                        next_stage=JournalStage.GENERATED,
                        completed_noop=False,
                        invalidation_reason=resume.invalidation_reason,
                    )
                assert execution.artifact is not None
                generated_artifact = GenerationStageArtifact(
                    generation_artifact=execution.artifact,
                    receipt=execution.receipt,
                )
                generated_reference = artifact_store.write(generated_artifact)
                writer.append(
                    _stage_record(
                        writer=writer,
                        run_id=run_id,
                        source_document_id=snapshot.source_document_id,
                        identity=identity,
                        stage=JournalStage.GENERATED,
                        status=JournalStatus.SUCCEEDED,
                        prompt_sha256=generation_prompt_sha256,
                        model_id=generation_model,
                        artifact=generated_reference,
                        upstream_artifact_sha256=planned_reference.sha256,
                        token_usage=(
                            execution.receipt.token_usage
                            if execution.receipt is not None
                            else None
                        ),
                    )
                )

            validation_prompt_sha256 = identity.validation_prompt_for_clause(
                locked_plan.final_target.clause_no
            )
            if resume.validation_record is not None:
                validated_reference = _artifact_reference(
                    resume.validation_record
                )
                validated_artifact = artifact_store.read(
                    validated_reference,
                    ValidationStageArtifact,
                )
                resumed.append(JournalStage.VALIDATED)
            else:
                execution = execute_consistency_validation(
                    assessment=classified_artifact.source_assessment,
                    plan=locked_plan,
                    artifact=generated_artifact.generation_artifact,
                    gateway=gateway,
                    config=config,
                    selection_config=resolved_selection_config,
                    prompt_bundle=resolved_prompt_bundle,
                )
                executed.append(JournalStage.VALIDATED)
                if execution.failure is not None:
                    writer.append(
                        _stage_record(
                            writer=writer,
                            run_id=run_id,
                            source_document_id=snapshot.source_document_id,
                            identity=identity,
                            stage=JournalStage.VALIDATED,
                            status=JournalStatus.FAILED,
                            prompt_sha256=validation_prompt_sha256,
                            model_id=identity.validator_model,
                            upstream_artifact_sha256=generated_reference.sha256,
                            token_usage=(
                                execution.receipt.token_usage
                                if execution.receipt is not None
                                else None
                            ),
                            failure=execution.failure,
                        )
                    )
                    return JournaledPipelineResult(
                        pipeline_result=DocumentPipelineResult(
                            source_document_id=snapshot.source_document_id,
                            source_assessment=classified_artifact.source_assessment,
                            generation_plan=locked_plan,
                            generation_artifact=(
                                generated_artifact.generation_artifact
                            ),
                            consistency_assessment=execution.assessment,
                            classification_receipt=classified_artifact.receipt,
                            generation_receipt=generated_artifact.receipt,
                            validation_receipt=execution.receipt,
                            failure=execution.failure,
                        ),
                        resumed_stages=tuple(resumed),
                        executed_stages=tuple(executed),
                        next_stage=JournalStage.VALIDATED,
                        completed_noop=False,
                        invalidation_reason=resume.invalidation_reason,
                    )
                assert execution.assessment is not None
                assert execution.receipt is not None
                assert execution.comparison is not None
                validated_artifact = ValidationStageArtifact(
                    generated_document_sha256=model_sha256(
                        generated_artifact.generation_artifact.generated_document
                    ),
                    consistency_assessment=execution.assessment,
                    receipt=execution.receipt,
                    comparison=execution.comparison,
                    repair_codes=execution.repair_codes,
                )
                validated_reference = artifact_store.write(validated_artifact)
                writer.append(
                    _stage_record(
                        writer=writer,
                        run_id=run_id,
                        source_document_id=snapshot.source_document_id,
                        identity=identity,
                        stage=JournalStage.VALIDATED,
                        status=JournalStatus.SUCCEEDED,
                        prompt_sha256=validation_prompt_sha256,
                        model_id=identity.validator_model,
                        artifact=validated_reference,
                        upstream_artifact_sha256=generated_reference.sha256,
                        token_usage=execution.receipt.token_usage,
                    )
                )

            result = _pipeline_from_artifacts(
                source_document_id=snapshot.source_document_id,
                classified=classified_artifact,
                planned=planned_artifact,
                generated=generated_artifact,
                validated=validated_artifact,
            )
            if resume.audit_record is not None:
                resumed.append(JournalStage.AUDITED)
            return JournaledPipelineResult(
                pipeline_result=result,
                resumed_stages=tuple(resumed),
                executed_stages=tuple(executed),
                next_stage=(
                    None
                    if resume.audit_record is not None
                    else JournalStage.AUDITED
                ),
                completed_noop=resume.audit_record is not None,
                invalidation_reason=resume.invalidation_reason,
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


def _matching_validation_record(
    *,
    writer: JournalWriter,
    source_document_id: str,
    identity: JournalIdentity,
    artifact_store: ArtifactStore,
) -> JournalRecord:
    resume = build_resume_plan(
        records=writer.records,
        source_document_id=source_document_id,
        identity=identity,
        artifact_store=artifact_store,
        legacy_contract_detected=writer.legacy_contract_detected,
    )
    if resume.validation_record is None:
        raise JournalCorruptError(
            "cannot record audit before a verified validation stage"
        )
    return resume.validation_record


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
        validation_record = _matching_validation_record(
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
                and _base_matches(record, identity)
                and record.upstream_artifact_sha256
                == validation_record.artifact_sha256
                and record.stage_config_sha256
                == identity.audit_config_sha256
                and _artifact_reference(record) == reference
            ):
                return record
        record = _stage_record(
            writer=writer,
            run_id=run_id,
            source_document_id=source_document_id,
            identity=identity,
            stage=JournalStage.AUDITED,
            status=JournalStatus.SUCCEEDED,
            artifact=reference,
            upstream_artifact_sha256=validation_record.artifact_sha256,
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
        validation_record = _matching_validation_record(
            writer=writer,
            source_document_id=source_document_id,
            identity=identity,
            artifact_store=artifact_store,
        )
        record = _stage_record(
            writer=writer,
            run_id=run_id,
            source_document_id=source_document_id,
            identity=identity,
            stage=JournalStage.AUDITED,
            status=JournalStatus.FAILED,
            upstream_artifact_sha256=validation_record.artifact_sha256,
            stage_config_sha256=identity.audit_config_sha256,
            failure=failure,
        )
        writer.append(record)
        return record
