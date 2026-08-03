"""제5~8호와 행정상태 대표 사례를 새 판별→생성→검사 파이프라인으로 만든다.

각 케이스는 다섯 단계 journal과 typed artifact를 남긴다. 중간 실패 뒤 다시
실행하면 마지막으로 검증된 단계부터 이어지며, validator는 생성 IR만 본다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rd2.administrative_status import ADMIN_STATUS_TEXT_POLICIES, AdminStatus  # noqa: E402
from rd2.canonical import NORMALIZATION_VERSION, canonical_sha256  # noqa: E402
from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    SUBCLAUSE_LABELS,
    ClauseNumber,
    SubclauseKey,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GeneratedDocumentIR,
    GenerationMode,
    GenerationTarget,
    SourceDocumentSnapshot,
    SourcePage,
    SourceTextBlock,
    TargetClassification,
)
from rd2.source_generation.document_select import (  # noqa: E402
    SelectionConfig,
    prepare_document_selection,
)
from rd2.source_generation.journal import run_three_stage_with_journal  # noqa: E402
from rd2.source_generation.legacy_synthetic import (  # noqa: E402
    FullySyntheticContext,
    FullySyntheticDocumentGenerator,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    PipelineConfig,
    RetryingGateway,
    StructuredOutputGateway,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402


@dataclass(frozen=True)
class PreviewCase:
    case_id: str
    sample_axis: str
    sample_value: str
    target: GenerationTarget
    sensitive_seed: str | None = None
    required_content_markers: tuple[str, ...] = ()


CLAUSE_CASES = (
    (
        ClauseNumber.CLAUSE_5,
        SubclauseKey.BID_CONTRACT,
        (
            "민원통합플랫폼 구축 용역의 공고 전 평가안. 기술적합성 35점, "
            "이행계획 25점, 보안대책 20점, 가격평가 20점으로 총 100점이다. "
            "기술평가 통과선은 75점, 우선협상대상자 협상 상한은 "
            "380,000,000원이다. 평가위원 후보는 E-04, E-11, E-17이며 "
            "공고 전에는 배점표·통과선·위원 후보·협상 상한을 공개하지 않는다."
        ),
        ("기술적합성", "75점", "380,000,000원", "공고 전"),
    ),
    (
        ClauseNumber.CLAUSE_6,
        SubclauseKey.PERSONNEL_PII,
        (
            "2026년 전산직 채용 면접 결과. 지원자 김민서는 필기 86점, "
            "면접 82점, 종합 84.4점이며 새 가상 개인 연락처를 부여한다. "
            "지원자 박하준은 필기 79점, 면접 91점, 종합 83.8점이며 새 가상 "
            "개인 연락처를 부여한다. 두 지원자의 이름과 연락처를 같은 표 행에 "
            "직접 연결하고 마스킹 문자는 쓰지 않는다."
        ),
        ("김민서", "84.4", "박하준", "연락처"),
    ),
    (
        ClauseNumber.CLAUSE_7,
        SubclauseKey.UNIT_COST,
        (
            "정수장 필터모듈 공급사 M-21의 단가 협상자료. 모듈 1개당 재료비 "
            "42,800원, 노무비 18,500원, 물류비 4,700원, 일반관리비 "
            "3,400원으로 산정원가는 69,400원이다. 업체 제안단가는 "
            "72,000원, 기관 목표단가는 70,200원이며 연간 예정수량은 "
            "12,000개다. 원가 구성과 목표단가는 계약 체결 전 공개하지 않는다."
        ),
        ("M-21", "69,400원", "70,200원", "계약 체결 전"),
    ),
    (
        ClauseNumber.CLAUSE_8,
        SubclauseKey.REAL_ESTATE_SPECULATION,
        (
            "가람역세권 정비예정구역 사전 검토. 검토구역은 A-17·A-18·B-03 "
            "필지 총 84,200㎡이고 추정보상비는 18,400,000,000원이다. "
            "주민공람 예정일은 2026-09-21이다. 공람 전 필지목록·보상 "
            "추정액·경계도면이 공개되면 토지거래 집중과 가격 급등 우려가 있다."
        ),
        ("A-17", "84,200㎡", "18,400,000,000원", "공람 전"),
    ),
)

ADMIN_CONTENT_MARKERS: dict[AdminStatus, tuple[str, ...]] = {
    AdminStatus.APPROVAL_PENDING: ("기안", "결재"),
    AdminStatus.RELEASE_NOT_DUE: ("공개 예정일", "대외 공개"),
    AdminStatus.DRAFT: ("작성", "확정"),
    AdminStatus.INTERNAL_REVIEW: ("담당 부서", "검토"),
    AdminStatus.ATTACHMENT_MISSING: ("붙임", "등록"),
    AdminStatus.DISCLOSURE_REVIEW: ("정보공개 범위", "심사"),
    AdminStatus.AGENCY_CONSULT: ("관계기관", "회신"),
    AdminStatus.DEIDENTIFY_PENDING: ("비식별", "외부 제공"),
    AdminStatus.SYSTEM_REGISTRATION_ERROR: ("전자문서 시스템", "오류"),
    AdminStatus.DOCUMENT_DISPOSITION: ("문서 분류", "보존"),
    AdminStatus.PETITION_IN_PROGRESS: ("민원", "사실관계"),
    AdminStatus.AUDIT_IN_PROGRESS: ("감사", "처분"),
}


class PreviewSyntheticGenerator(FullySyntheticDocumentGenerator):
    """원문 비사용 경로에서 목표만 받아 합성 IR을 만드는 어댑터."""

    def __init__(self, gateway: StructuredOutputGateway, model: str) -> None:
        self.gateway = gateway
        self.model = model
        self.last_prompt = ""
        self.last_response_id = ""

    def generate(
        self,
        *,
        target: GenerationTarget,
        context: FullySyntheticContext,
    ) -> GeneratedDocumentIR:
        self.last_prompt = json.dumps(
            {
                "instruction": (
                    "원문을 사용하지 않는 완전 합성 대한민국 공공문서를 작성하라. "
                    "실존 개인정보·기관 기밀은 쓰지 않고, 목표 세부유형의 보호 "
                    "대상과 공개 시 구체적 지장이 본문에 드러나게 한다."
                ),
                "target": target.model_dump(mode="json"),
                "subclause_label": (
                    SUBCLAUSE_LABELS[target.subclause_key]
                    if target.subclause_key
                    else ""
                ),
                "administrative_status_context": [
                    ADMIN_STATUS_TEXT_POLICIES[status].detail
                    for status in target.administrative_statuses
                ],
                "scenario": {
                    "id": context.scenario_id,
                    "ordering_agency": context.ordering_agency,
                    "production_date": context.production_date,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        call = self.gateway.parse(
            model=self.model,
            system_prompt=(
                "당신은 학습용 합성 공공문서 생성기다. 입력 목표에 맞는 "
                "GeneratedDocumentIR만 반환한다."
            ),
            user_prompt=self.last_prompt,
            response_model=GeneratedDocumentIR,
            max_output_tokens=8_000,
        )
        self.last_response_id = call.response_id
        if not isinstance(call.parsed, GeneratedDocumentIR):
            raise ValueError("synthetic preview generator returned wrong contract")
        return call.parsed


def preview_cases() -> tuple[PreviewCase, ...]:
    cases = [
        PreviewCase(
            case_id=f"clause-{clause.value}",
            sample_axis="clause",
            sample_value=clause.value,
            target=GenerationTarget(
                classification=TargetClassification.S,
                clause_no=clause,
                subclause_key=subclause,
                generation_mode=GenerationMode.COUNTERFACTUAL,
            ),
            sensitive_seed=seed,
            required_content_markers=markers,
        )
        for clause, subclause, seed, markers in CLAUSE_CASES
    ]
    cases.extend(
        PreviewCase(
            case_id=f"admin-{index:02d}-{status.name.lower()}",
            sample_axis="admin_status",
            sample_value=status.value,
            target=GenerationTarget(
                classification=TargetClassification.S,
                administrative_statuses=(status,),
                generation_mode=GenerationMode.COUNTERFACTUAL,
            ),
            required_content_markers=ADMIN_CONTENT_MARKERS[status],
        )
        for index, status in enumerate(AdminStatus, start=1)
    )
    return tuple(cases)


def _synthetic_public_snapshot() -> SourceDocumentSnapshot:
    """외부 모델 전송에 안전한 실존 정보 없는 공개형 기준 문서."""

    texts = (
        "지역 행정서비스 처리시간 개선안",
        "조사범위: 민원창구 5개소, 처리사례 1,240건, 이용자 설문 360명",
        "현황: 평균 대기시간 23분, 동일정보 반복입력 비율 42%, 재방문율 18%",
        (
            "대안 A: 통합신청 화면 도입, 구축비 186,000,000원, "
            "2개 민원창구에서 4개월간 시범운영"
        ),
        (
            "대안 B: 예약시간제 확대, 월 운영비 12,500,000원, "
            "예상 대기시간 15분"
        ),
        "검토의견: 대안 A를 우선 추진하고 시범운영 성과를 평가",
    )
    blocks = tuple(
        SourceTextBlock(block_id=f"source:b{index}", text=text)
        for index, text in enumerate(texts)
    )
    return SourceDocumentSnapshot(
        source_document_id="synthetic-public-policy-source-001",
        source="preview_synthetic",
        manifest_key="preview/synthetic-public-policy-source-001",
        source_sha256=hashlib.sha256(
            "\n".join(texts).encode("utf-8")
        ).hexdigest(),
        pages=(SourcePage(page_number=1, blocks=blocks),),
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _content_quality_issues(
    case: PreviewCase,
    document: GeneratedDocumentIR,
) -> tuple[str, ...]:
    text = f"{document.title}\n{document.body_text}"
    issues = [
        f"missing required content marker: {marker}"
        for marker in case.required_content_markers
        if marker not in text
    ]
    for pattern in (
        "내용을 포함하고 있습니다",
        "내용이 포함되어 있습니다",
        "상황을 다룹니다",
        "문서입니다",
        "보고서입니다",
    ):
        if pattern in text:
            issues.append(f"meta-description phrase present: {pattern}")
    if len(set(re.findall(r"\d[\d,]*(?:\.\d+)?", text))) < 2:
        issues.append("fewer than two concrete numeric facts")
    if not any(block.kind != "paragraph" for block in document.blocks):
        issues.append("no structured content block")
    return tuple(issues)


def _case_summary(case: PreviewCase, case_dir: Path) -> dict[str, str]:
    result_path = case_dir / "pipeline_result.json"
    if not result_path.exists():
        return {
            "case_id": case.case_id,
            "sample_axis": case.sample_axis,
            "sample_value": case.sample_value,
            "status": "missing",
            "case_dir": str(case_dir.resolve()),
        }
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assessment = payload.get("source_assessment") or {}
    source_label = assessment.get("source_classification") or {}
    plan = payload.get("generation_plan") or {}
    target = plan.get("final_target") or {}
    generation = payload.get("generation_artifact") or {}
    generated = generation.get("generated_document") or {}
    consistency = payload.get("consistency_assessment") or {}
    comparison = payload.get("comparison") or {}
    failure = payload.get("failure") or {}
    quality = payload.get("content_quality") or {}
    return {
        "case_id": case.case_id,
        "sample_axis": case.sample_axis,
        "sample_value": case.sample_value,
        "status": "failed" if failure else "ok",
        "source_classification": source_label.get("classification", ""),
        "source_clause_no": source_label.get("clause_no") or "",
        "final_classification": target.get("classification", ""),
        "final_clause_no": target.get("clause_no") or "",
        "final_subclause_key": target.get("subclause_key") or "",
        "generation_route": plan.get("generation_route", ""),
        "generated_title": generated.get("title", ""),
        "validation_classification": consistency.get("classification", ""),
        "validation_clause_no": consistency.get("clause_no") or "",
        "validation_subclause_key": consistency.get("subclause_key") or "",
        "classification_match": str(
            comparison.get("classification_match", "")
        ),
        "clause_match": str(comparison.get("clause_match", "")),
        "subclause_match": str(comparison.get("subclause_match", "")),
        "subject_role_match": str(
            comparison.get("subject_role_match", "")
        ),
        "requires_review": str(comparison.get("requires_review", "")),
        "failure_stage": failure.get("stage", ""),
        "failure_code": failure.get("code", ""),
        "failure_message": failure.get("message", ""),
        "content_quality_pass": str(quality.get("passed", "")),
        "content_quality_issues": " | ".join(quality.get("issues") or []),
        "case_dir": str(case_dir.resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT
        / f"output/source_generation_previews_{date.today():%Y%m%d}",
    )
    parser.add_argument("--classifier-model")
    parser.add_argument("--generator-model")
    parser.add_argument("--validator-model")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)
    if args.max_attempts < 1:
        raise ValueError("max-attempts must be at least 1")

    load_dotenv(ROOT / ".env")
    classifier_model = args.classifier_model or os.environ.get(
        "SOURCE_CLASSIFIER_MODEL",
        "gpt-4o",
    )
    generator_model = args.generator_model or os.environ.get(
        "SOURCE_GENERATOR_MODEL",
        classifier_model,
    )
    validator_model = args.validator_model or os.environ.get(
        "SOURCE_VALIDATOR_MODEL",
        "gpt-4o-mini",
    )
    if validator_model in {classifier_model, generator_model}:
        raise ValueError(
            "validator model must differ from classifier and generator"
        )

    snapshot = (
        SourceDocumentSnapshot.model_validate_json(
            args.snapshot.read_text(encoding="utf-8")
        )
        if args.snapshot is not None
        else _synthetic_public_snapshot()
    )
    selection_config = SelectionConfig()
    prepared = prepare_document_selection(snapshot, selection_config)
    if prepared.selection is None:
        raise RuntimeError(
            "preview snapshot unexpectedly requires relevance selection"
        )
    selection = prepared.selection
    prompt_bundle = build_prompt_bundle(selection_config)

    gateway: StructuredOutputGateway | None = None
    synthetic_generator: PreviewSyntheticGenerator | None = None
    if not args.summary_only:
        gateway = RetryingGateway(
            OpenAIResponsesGateway(
                OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            ),
            max_attempts=args.max_attempts,
        )
        synthetic_generator = PreviewSyntheticGenerator(
            gateway,
            generator_model,
        )
    base_config = PipelineConfig(
        classifier_model=classifier_model,
        generator_model=generator_model,
        validator_model=validator_model,
        reference_date=date.today(),
    )
    all_cases = preview_cases()
    requested = set(args.case)
    cases = tuple(
        case
        for case in all_cases
        if not requested or case.case_id in requested
    )
    unknown = requested - {case.case_id for case in all_cases}
    if unknown:
        raise ValueError(f"unknown preview cases: {sorted(unknown)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        args.output_dir / "run_manifest.json",
        {
            "contract_version": "2.0.0",
            "classifier_model": classifier_model,
            "generator_model": generator_model,
            "validator_model": validator_model,
            "source_document_id": snapshot.source_document_id,
            "source_sha256": snapshot.source_sha256,
            "selection_sha256": selection.selection_sha256,
            "classifier_prompt_sha256": prompt_bundle.definition(
                "classifier"
            ).sha256,
            "generator_prompt_sha256": prompt_bundle.definition(
                "generator"
            ).sha256,
            "validator_prompt_sha256": prompt_bundle.definition(
                "validator"
            ).sha256,
            "cases": [case.case_id for case in cases],
        },
    )

    for index, case in enumerate(cases, start=1):
        case_dir = args.output_dir / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        if args.summary_only:
            continue
        assert gateway is not None
        assert synthetic_generator is not None
        run_dir = (
            case_dir / f"rerun-{uuid4().hex[:10]}"
            if args.force
            else case_dir / "journal"
        )
        config = PipelineConfig(
            classifier_model=base_config.classifier_model,
            generator_model=base_config.generator_model,
            validator_model=base_config.validator_model,
            reference_date=base_config.reference_date,
            source_sensitive_mode=(
                case.target.clause_no == ClauseNumber.CLAUSE_6
            ),
        )
        print(
            f"[{index}/{len(cases)}] {case.case_id}",
            flush=True,
        )
        audit_config_sha256 = canonical_sha256(
            {"preview_audit": "not_executed", "case_id": case.case_id},
            normalization_version=NORMALIZATION_VERSION,
        )
        journaled = run_three_stage_with_journal(
            run_dir=run_dir,
            run_id=f"preview-{case.case_id}",
            snapshot=snapshot,
            selection=selection,
            counterfactual_target=case.target,
            gateway=gateway,
            config=config,
            audit_config_sha256=audit_config_sha256,
            selection_config=selection_config,
            prompt_bundle=prompt_bundle,
            sensitive_seed=case.sensitive_seed,
            fully_synthetic_generator=synthetic_generator,
            fully_synthetic_context=FullySyntheticContext(
                scenario_id=f"preview-{case.case_id}",
                ordering_agency="가상행정기관",
                production_date="2026-07-29",
            ),
        )
        result = journaled.pipeline_result
        payload = result.model_dump(
            mode="json",
            exclude_computed_fields=False,
        )
        if result.generation_artifact is not None:
            document = result.generation_artifact.generated_document
            issues = _content_quality_issues(case, document)
            payload["content_quality"] = {
                "passed": not issues,
                "issues": list(issues),
                "required_content_markers": list(
                    case.required_content_markers
                ),
            }
            (case_dir / "generated_body.txt").write_text(
                f"{document.title}\n\n{document.body_text}\n",
                encoding="utf-8",
            )
            _write_json(
                case_dir / "generated_ir.json",
                document.model_dump(mode="json"),
            )
        else:
            payload["content_quality"] = {
                "passed": False,
                "issues": ["generation artifact is missing"],
                "required_content_markers": list(
                    case.required_content_markers
                ),
            }
        payload["journal"] = {
            "resumed_stages": [
                stage.value for stage in journaled.resumed_stages
            ],
            "executed_stages": [
                stage.value for stage in journaled.executed_stages
            ],
            "next_stage": (
                journaled.next_stage.value
                if journaled.next_stage is not None
                else None
            ),
            "invalidation_reason": journaled.invalidation_reason,
        }
        _write_json(case_dir / "target.json", case.target.model_dump(mode="json"))
        _write_json(case_dir / "pipeline_result.json", payload)

    summaries = [
        _case_summary(case, args.output_dir / case.case_id)
        for case in cases
    ]
    fieldnames = sorted({key for row in summaries for key in row})
    with (args.output_dir / "preview_summary.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summaries)
    with (args.output_dir / "preview_records.jsonl").open(
        "w",
        encoding="utf-8",
    ) as handle:
        for row in summaries:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    ok_count = sum(row.get("status") == "ok" for row in summaries)
    quality_count = sum(
        row.get("content_quality_pass") == "True" for row in summaries
    )
    print(
        (
            f"completed: {ok_count}/{len(summaries)}, "
            f"content-quality: {quality_count}/{len(summaries)} "
            f"-> {args.output_dir.resolve()}"
        ),
        flush=True,
    )
    return (
        0
        if ok_count == len(summaries)
        and quality_count == len(summaries)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
