"""현행 생성기 프롬프트와 최소판을 **같은 조건에서** 비교한다.

바꾸는 것은 생성 단계의 프롬프트 하나뿐이다. 원문, 판별 결과, 잠긴 목표,
채점 프롬프트, 모델을 전부 공유한다 — 그래야 차이가 프롬프트에서 온 것이라고
말할 수 있다.

    원문 1건
      -> 판별기 1회 (두 갈래가 공유)
      -> 플래너 (결정론적, 공유)
      -> A: 현행 생성기 프롬프트 (3,400~10,500자)
         B: 최소판     (422~1,014자)
      -> 채점기 각 1회 (같은 프롬프트)

``mask_restoration``으로 잠기는 원문은 건너뛴다. 그 route는 생성기 프롬프트를
쓰지 않으므로 A/B의 대상이 아니다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402

from rd2.source_generation.classification_taxonomy import (  # noqa: E402
    ClauseNumber,
    SUBCLAUSES_BY_CLAUSE,
    expected_classification,
)
from rd2.source_generation.contracts import (  # noqa: E402
    GeneratedDocumentIR,
    GenerationArtifact,
    GenerationMode,
    GenerationRoute,
    GenerationTarget,
    TargetClassification,
)
from rd2.source_generation.document_select import (  # noqa: E402
    SelectionConfig,
    prepare_document_selection,
    render_full_source,
)
from rd2.source_generation.minimal_prompt import (  # noqa: E402
    MINIMAL_PROMPT_VERSION,
    render_minimal_generator_system_prompt,
    render_minimal_generator_user_prompt,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    OpenAIResponsesGateway,
    PipelineConfig,
    RetryingGateway,
    build_generation_plan,
    execute_classification,
    execute_consistency_validation,
    execute_generation,
    model_sha256,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402

load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "scripts"))
from run_seoul_official_batch import (  # noqa: E402
    _snapshot_from_hwpx,
    _snapshot_from_pdf,
)


def _targets() -> list[GenerationTarget]:
    clause = ClauseNumber.CLAUSE_6
    return [
        GenerationTarget(
            classification=TargetClassification(
                expected_classification(clause).value
            ),
            clause_no=clause,
            subclause_key=subclause,
            generation_mode=GenerationMode.COUNTERFACTUAL,
        )
        for subclause in sorted(
            SUBCLAUSES_BY_CLAUSE[clause], key=lambda item: item.value
        )
    ]


def _minimal_generation(
    *,
    snapshot,
    plan,
    gateway,
    config,
) -> tuple[GeneratedDocumentIR | None, str, str | None]:
    """최소판 프롬프트로 한 번 호출한다.

    현행 경로가 넘기는 판별 결과 JSON·계획 JSON·seed·repair code를 **주지
    않는다.** 최소판의 요점이 그것들을 뺀 상태에서 조항 사례만으로 되는지
    보는 것이므로, 편의를 위해 일부만 되돌리면 비교가 무의미해진다.
    """

    subclause = plan.final_target.subclause_key
    system_prompt = render_minimal_generator_system_prompt(subclause)
    try:
        call = gateway.parse(
            model=config.generator_model,
            system_prompt=system_prompt,
            user_prompt=render_minimal_generator_user_prompt(
                render_full_source(snapshot)
            ),
            response_model=GeneratedDocumentIR,
            max_output_tokens=config.max_generator_output_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - 한 건 실패가 배치를 끊지 않는다
        return None, system_prompt, f"{type(exc).__name__}: {exc}"
    if not isinstance(call.parsed, GeneratedDocumentIR):
        return None, system_prompt, "wrong contract type"
    return call.parsed, system_prompt, None


def _score(
    *,
    label: str,
    document: GeneratedDocumentIR | None,
    error: str | None,
    artifact: GenerationArtifact | None,
    assessment,
    plan,
    gateway,
    config,
    prompt_bundle,
    system_prompt: str,
) -> dict:
    record = {
        "arm": label,
        "system_prompt_chars": len(system_prompt),
        "generation_error": error,
    }
    if document is None or artifact is None:
        return record
    record.update(
        {
            "title": document.title,
            "blocks": [block.kind for block in document.blocks],
            "body": document.body_text,
            "body_chars": len(document.body_text),
        }
    )
    validation = execute_consistency_validation(
        assessment=assessment,
        plan=plan,
        artifact=artifact,
        gateway=gateway,
        config=config,
        prompt_bundle=prompt_bundle,
    )
    if validation.failure is not None:
        record["validation_failure"] = validation.failure.message
        return record
    a = validation.assessment
    record.update(
        {
            "validation_classification": a.classification.value,
            "validation_clause": (
                a.clause_no.value if a.clause_no else None
            ),
            "validation_subclause": (
                a.subclause_key.value if a.subclause_key else None
            ),
            "validation_form": a.document_form.value,
            "validation_rationale": a.rationale,
            "target_hit": (
                a.clause_no == plan.final_target.clause_no
                and a.subclause_key == plan.final_target.subclause_key
            ),
        }
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--source-name", default="seoul_opengov")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--classifier-model", default="gpt-4o")
    parser.add_argument("--generator-model", default="gpt-4o")
    parser.add_argument("--validator-model", default="gpt-4o-mini")
    args = parser.parse_args()

    files = sorted(
        [*args.source_dir.rglob("*.hwpx"), *args.source_dir.rglob("*.pdf")]
    )
    if not files:
        print(f"입력 파일이 없다: {args.source_dir}")
        return 1

    gateway = RetryingGateway(
        OpenAIResponsesGateway(OpenAI(api_key=os.environ["OPENAI_API_KEY"])),
        max_attempts=2,
    )
    config = PipelineConfig(
        classifier_model=args.classifier_model,
        generator_model=args.generator_model,
        validator_model=args.validator_model,
        reference_date=date.today(),
        source_sensitive_mode=False,
    )
    selection_config = SelectionConfig()
    prompt_bundle = build_prompt_bundle(selection_config)
    targets = _targets()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    records_path = args.out_dir / "ab_records.jsonl"

    done = 0
    with records_path.open("w", encoding="utf-8") as out:
        for path in files:
            if done >= args.count:
                break
            if path.suffix.lower() == ".pdf":
                built = _snapshot_from_pdf(path, source=args.source_name)
            else:
                built = _snapshot_from_hwpx(
                    path, ROOT / "data", source=args.source_name
                )
            if built is None:
                continue
            snapshot, _title = built
            prepared = prepare_document_selection(snapshot, selection_config)
            if prepared.selection is None:
                continue
            selection = prepared.selection

            classification = execute_classification(
                snapshot=snapshot,
                selection=selection,
                gateway=gateway,
                config=config,
                selection_config=selection_config,
                prompt_bundle=prompt_bundle,
            )
            if classification.assessment is None:
                continue
            assessment = classification.assessment

            target = targets[done % len(targets)]
            try:
                plan = build_generation_plan(
                    assessment=assessment,
                    requested_target=target,
                    snapshot=snapshot,
                    selection=selection,
                )
            except Exception:  # noqa: BLE001
                continue
            if plan.generation_route == GenerationRoute.MASK_RESTORATION:
                # 생성기 프롬프트를 쓰지 않는 route라 비교 대상이 아니다.
                continue

            done += 1
            print(
                f"[{done}/{args.count}] {snapshot.source_document_id} "
                f"{plan.final_target.subclause_key.value} "
                f"({plan.generation_route.value})"
            )

            current_definition = prompt_bundle.generator_definition_for_form(
                assessment.source_classification.document_form,
                sensitive=False,
                subclause_key=plan.final_target.subclause_key,
            )
            generation = execute_generation(
                snapshot=snapshot,
                selection=selection,
                assessment=assessment,
                plan=plan,
                gateway=gateway,
                config=config,
                selection_config=selection_config,
                prompt_bundle=prompt_bundle,
            )
            artifact_a = generation.artifact
            arm_a = _score(
                label="current",
                artifact=artifact_a,
                document=(
                    generation.artifact.generated_document
                    if generation.artifact
                    else None
                ),
                error=(
                    generation.failure.message if generation.failure else None
                ),
                assessment=assessment,
                plan=plan,
                gateway=gateway,
                config=config,
                prompt_bundle=prompt_bundle,
                system_prompt=current_definition.system_prompt,
            )

            document_b, system_b, error_b = _minimal_generation(
                snapshot=snapshot,
                plan=plan,
                gateway=gateway,
                config=config,
            )
            artifact_b = None
            if document_b is not None and artifact_a is not None:
                artifact_b = artifact_a.model_copy(
                    update={"generated_document": document_b}
                )
            arm_b = _score(
                label="minimal",
                artifact=artifact_b,
                document=document_b,
                error=error_b,
                assessment=assessment,
                plan=plan,
                gateway=gateway,
                config=config,
                prompt_bundle=prompt_bundle,
                system_prompt=system_b,
            )

            out.write(
                json.dumps(
                    {
                        "source_document_id": snapshot.source_document_id,
                        "source_file": path.name,
                        "source_text": "\n\n".join(
                            block.text
                            for page in snapshot.pages
                            for block in page.blocks
                        ),
                        "document_form": (
                            assessment.source_classification.document_form.value
                        ),
                        "generation_route": plan.generation_route.value,
                        "final_target": plan.final_target.model_dump(mode="json"),
                        "arms": [arm_a, arm_b],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out.flush()

    (args.out_dir / "summary.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prompt_bundle": prompt_bundle.version,
                "minimal_prompt": MINIMAL_PROMPT_VERSION,
                "generator_model": args.generator_model,
                "validator_model": args.validator_model,
                "source_dir": str(args.source_dir),
                "compared": done,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{done}건 비교 -> {records_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
