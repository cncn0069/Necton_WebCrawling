from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from rd2.schema.models import CsoClassification
from rd2.source_generation.classification_taxonomy import (
    ClauseNumber,
    SemanticDocumentType,
    SubclauseKey,
)
from rd2.source_generation.contracts import (
    CONTRACT_SCHEMA_VERSION,
    EvidenceSpan,
    FailureCode,
    Pass2Assessment,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
    TokenUsage,
)
from rd2.source_generation.holdout_eval import (
    HoldoutCase,
    HoldoutEvalConfig,
    HoldoutEvalError,
    HoldoutManifest,
    build_stratified_manifest,
    classify_case,
    run_holdout_eval,
    snapshot_to_document_ir,
    summarize_outcomes,
)
from rd2.source_generation.pipeline import (
    PipelineConfig,
    StructuredCall,
    StructuredCallError,
)
from rd2.source_generation.prompts import build_prompt_bundle

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


@dataclass
class FakeGateway:
    queued: list[Any]

    def __post_init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> StructuredCall[Any]:
        self.calls.append(kwargs)
        next_result = self.queued.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return StructuredCall(
            parsed=next_result,
            response_id=f"response-{len(self.calls)}",
            token_usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )


def _case(
    case_id: str,
    *,
    document_id: str | None = None,
    subclause: SubclauseKey | None = SubclauseKey.BID_CONTRACT,
    clause: ClauseNumber | None = ClauseNumber.CLAUSE_5,
    classification: CsoClassification = CsoClassification.S,
    source_sha256: str = SHA_A,
) -> HoldoutCase:
    return HoldoutCase(
        case_id=case_id,
        source_document_id=document_id or f"doc-{case_id}",
        source_sha256=source_sha256,
        document_type=SemanticDocumentType.OFFICIAL_DOCUMENT,
        classification=classification,
        clause_no=clause,
        subclause_key=subclause,
    )


def _o_case(case_id: str) -> HoldoutCase:
    return _case(
        case_id,
        subclause=None,
        clause=None,
        classification=CsoClassification.O,
    )


def _snapshot(document_id: str, *, sha: str = SHA_A) -> SourceDocumentSnapshot:
    return SourceDocumentSnapshot(
        source_document_id=document_id,
        source="PRISM",
        manifest_key="holdout",
        source_sha256=sha,
        pages=(
            SourcePage(
                page_number=1,
                blocks=(
                    SourceTextBlock(block_id="p1:b0", text="예정가격 산정 근거를 검토한다"),
                    SourceTextBlock(block_id="p1:b1", text="평가위원 배점표는 비공개다"),
                ),
            ),
        ),
    )


def _assessment(
    *,
    subclause: SubclauseKey | None = SubclauseKey.BID_CONTRACT,
    clause: ClauseNumber | None = ClauseNumber.CLAUSE_5,
    classification: CsoClassification = CsoClassification.S,
    document_type: SemanticDocumentType = SemanticDocumentType.OFFICIAL_DOCUMENT,
) -> Pass2Assessment:
    spans = ()
    if classification != CsoClassification.O:
        # "예정가격 산정 근거를 검토한다"의 0:4 — 실제 원문과 정확히 일치해야
        # validate_against_document를 통과한다.
        spans = (
            EvidenceSpan(block_id="p1:b0", start=0, end=4, quote="예정가격"),
        )
    return Pass2Assessment(
        document_type=document_type,
        evidence_spans=spans,
        rationale="근거를 확인했다",
        classification=classification,
        clause_no=clause,
        subclause_key=subclause,
    )


def test_snapshot_to_document_ir_preserves_block_ids_and_text():
    """형식만 맞추고 내용은 손대지 않아야 난이도 비교가 성립한다."""

    document = snapshot_to_document_ir(_snapshot("doc-1"), title="계약 검토")

    assert document.title == "계약 검토"
    assert [block.block_id for block in document.blocks] == ["p1:b0", "p1:b1"]
    assert document.block_text("p1:b1") == "평가위원 배점표는 비공개다"


def test_holdout_case_rejects_labels_that_contradict_the_clause_map():
    with pytest.raises(ValidationError):
        _case("c1", clause=ClauseNumber.CLAUSE_1, subclause=SubclauseKey.BID_CONTRACT)
    with pytest.raises(ValidationError):
        _case("c2", classification=CsoClassification.O)


def test_manifest_rejects_documents_already_used_for_generation():
    """평가셋이 생성 입력과 겹치면 학습셋을 채점하게 된다."""

    with pytest.raises(ValidationError, match="overlaps generation inputs"):
        HoldoutManifest(
            manifest_id="m1",
            created_at=NOW,
            seed=7,
            cases=(_case("c1", document_id="doc-shared"),),
            excluded_document_ids=("doc-shared",),
        )


def test_stratified_sampling_is_deterministic_and_capped_per_stratum():
    candidates = [
        _case(f"bid-{index:02d}", document_id=f"doc-bid-{index:02d}")
        for index in range(10)
    ] + [_o_case(f"open-{index:02d}") for index in range(10)]

    first = build_stratified_manifest(
        candidates,
        manifest_id="m1",
        created_at=NOW,
        seed=42,
        per_stratum=3,
    )
    second = build_stratified_manifest(
        list(reversed(candidates)),
        manifest_id="m1",
        created_at=NOW,
        seed=42,
        per_stratum=3,
    )

    assert first.stratum_counts() == {"bid_contract": 3, "O": 3}
    # 입력 순서가 달라도 같은 seed면 같은 표본이어야 재현이 성립한다.
    assert [case.case_id for case in first.cases] == [
        case.case_id for case in second.cases
    ]
    assert first.sha256 == second.sha256

    different_seed = build_stratified_manifest(
        candidates,
        manifest_id="m1",
        created_at=NOW,
        seed=43,
        per_stratum=3,
    )
    assert different_seed.sha256 != first.sha256


def test_sparse_strata_are_kept_whole_rather_than_dropped():
    """희소 조항을 표본에서 잃으면 그 조항 정확도를 영영 못 잰다."""

    candidates = [
        _case(f"bid-{index}", document_id=f"doc-bid-{index}") for index in range(5)
    ] + [
        _case(
            "cornering-0",
            document_id="doc-corner-0",
            clause=ClauseNumber.CLAUSE_8,
            subclause=SubclauseKey.CORNERING,
        )
    ]

    manifest = build_stratified_manifest(
        candidates,
        manifest_id="m1",
        created_at=NOW,
        seed=1,
        per_stratum=3,
    )

    assert manifest.stratum_counts() == {"bid_contract": 3, "cornering": 1}


def test_stratified_sampling_drops_generation_inputs_before_sampling():
    candidates = [_case("c1", document_id="doc-used"), _case("c2", document_id="doc-free")]

    manifest = build_stratified_manifest(
        candidates,
        manifest_id="m1",
        created_at=NOW,
        seed=1,
        per_stratum=5,
        excluded_document_ids=("doc-used",),
    )

    assert [case.source_document_id for case in manifest.cases] == ["doc-free"]

    with pytest.raises(HoldoutEvalError) as excinfo:
        build_stratified_manifest(
            candidates,
            manifest_id="m1",
            created_at=NOW,
            seed=1,
            per_stratum=5,
            excluded_document_ids=("doc-used", "doc-free"),
        )
    assert excinfo.value.code == FailureCode.MANIFEST_INVALID


def test_classify_case_records_evidence_failures_instead_of_raising():
    case = _case("c1")
    document = snapshot_to_document_ir(_snapshot("doc-c1"), title="제목")
    bad = Pass2Assessment(
        document_type=SemanticDocumentType.OFFICIAL_DOCUMENT,
        evidence_spans=(
            EvidenceSpan(block_id="p1:b0", start=0, end=6, quote="없는인용구"),
        ),
        rationale="근거",
        classification=CsoClassification.S,
        clause_no=ClauseNumber.CLAUSE_5,
        subclause_key=SubclauseKey.BID_CONTRACT,
    )
    gateway = FakeGateway([bad])

    outcome = classify_case(
        case,
        document,
        gateway,
        model_id="grader",
        prompt_bundle=build_prompt_bundle(),
        config=HoldoutEvalConfig(),
    )

    assert outcome.succeeded is False
    assert outcome.failure is not None
    assert outcome.failure.code == FailureCode.EVIDENCE_INVALID


def test_classify_case_records_sdk_failures():
    gateway = FakeGateway(
        [StructuredCallError(FailureCode.SDK_ERROR, "boom", retryable=True)]
    )

    outcome = classify_case(
        _case("c1"),
        snapshot_to_document_ir(_snapshot("doc-c1"), title="제목"),
        gateway,
        model_id="grader",
        prompt_bundle=build_prompt_bundle(),
        config=HoldoutEvalConfig(),
    )

    assert outcome.succeeded is False
    assert outcome.failure is not None
    assert outcome.failure.code == FailureCode.SDK_ERROR


def test_summary_scores_each_axis_and_counts_boundary_confusions():
    manifest = HoldoutManifest(
        manifest_id="m1",
        created_at=NOW,
        seed=1,
        cases=(
            _case("c1", document_id="doc-1"),
            _case("c2", document_id="doc-2"),
            _case(
                "c3",
                document_id="doc-3",
                subclause=SubclauseKey.DECISION_REVIEW,
            ),
        ),
    )
    outcomes = [
        # 정답
        _outcome(manifest, "c1", _assessment()),
        # bid_contract를 decision_review로 오분류 (경계쌍)
        _outcome(
            manifest,
            "c2",
            _assessment(subclause=SubclauseKey.DECISION_REVIEW),
        ),
        # 정답
        _outcome(
            manifest,
            "c3",
            _assessment(subclause=SubclauseKey.DECISION_REVIEW),
        ),
    ]

    result = summarize_outcomes(manifest, outcomes, model_id="grader")

    assert result.accuracy.scored == 3
    assert result.accuracy.subclause_correct == 2
    assert result.accuracy.subclause_accuracy == pytest.approx(2 / 3)
    # 조항은 셋 다 제5호라 전부 맞는다 — 축을 따로 봐야 하는 이유.
    assert result.accuracy.clause_correct == 3

    pair = next(
        item
        for item in result.boundary_pairs
        if item.left == "bid_contract" and item.right == "decision_review"
    )
    assert pair.left_predicted_as_right == 1
    assert pair.right_predicted_as_left == 0
    assert pair.confusions == 1


def _outcome(manifest: HoldoutManifest, case_id: str, assessment: Pass2Assessment):
    from rd2.source_generation.contracts import CallReceipt, FailureStage
    from rd2.source_generation.holdout_eval import CaseOutcome

    return CaseOutcome(
        case_id=case_id,
        model_id="grader",
        assessment=assessment,
        receipt=CallReceipt(
            stage=FailureStage.PASS2,
            model_id="grader",
            response_id="r1",
        ),
    )


def test_run_holdout_eval_classifies_every_case_with_both_models():
    manifest = HoldoutManifest(
        manifest_id="m1",
        created_at=NOW,
        seed=1,
        cases=(_case("c1", document_id="doc-1"), _case("c2", document_id="doc-2")),
    )
    snapshots = {"doc-1": _snapshot("doc-1"), "doc-2": _snapshot("doc-2")}
    gateway = FakeGateway([_assessment() for _ in range(4)])

    report = run_holdout_eval(
        manifest,
        snapshots,
        {},
        gateway,
        pipeline_config=PipelineConfig(
            generator_model="generator",
            grader_model="grader",
        ),
        generated_at=NOW,
    )

    # 사례 2건 x 모델 2개
    assert len(gateway.calls) == 4
    assert {result.model_id for result in report.results} == {"generator", "grader"}
    assert all(result.accuracy.scored == 2 for result in report.results)
    assert report.stratum_counts == {"bid_contract": 2}
    # P2 프롬프트로 분류하므로 route/target 어휘가 입력에 없어야 한다.
    for call in gateway.calls:
        assert "generation_route" not in call["user_prompt"]
        assert "source_aligned" not in call["system_prompt"]


def test_run_holdout_eval_refuses_documents_that_changed_since_sampling():
    manifest = HoldoutManifest(
        manifest_id="m1",
        created_at=NOW,
        seed=1,
        cases=(_case("c1", document_id="doc-1", source_sha256=SHA_A),),
    )

    with pytest.raises(HoldoutEvalError) as excinfo:
        run_holdout_eval(
            manifest,
            {"doc-1": _snapshot("doc-1", sha=SHA_B)},
            {},
            FakeGateway([]),
            pipeline_config=PipelineConfig(
                generator_model="generator",
                grader_model="grader",
            ),
            generated_at=NOW,
        )

    assert excinfo.value.code == FailureCode.SOURCE_CHANGED


def test_report_knows_it_is_stale_when_the_prompt_bundle_changes():
    """taxonomy나 판정 지침이 바뀌면 이전 정확도는 비교 대상이 아니다."""

    manifest = HoldoutManifest(
        manifest_id="m1",
        created_at=NOW,
        seed=1,
        cases=(_case("c1", document_id="doc-1"),),
    )
    bundle = build_prompt_bundle()
    report = run_holdout_eval(
        manifest,
        {"doc-1": _snapshot("doc-1")},
        {},
        FakeGateway([_assessment(), _assessment()]),
        pipeline_config=PipelineConfig(
            generator_model="generator",
            grader_model="grader",
        ),
        generated_at=NOW,
        prompt_bundle=bundle,
    )

    assert report.is_stale_against(bundle) is False

    pass1 = bundle.definition("pass1")
    from dataclasses import replace

    changed = bundle.with_definition(
        replace(pass1, system_prompt=pass1.system_prompt + "\n새 규칙")
    )
    assert report.is_stale_against(changed) is True
    assert report.contract_version == CONTRACT_SCHEMA_VERSION
