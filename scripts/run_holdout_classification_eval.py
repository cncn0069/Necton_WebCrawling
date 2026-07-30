"""held-out 원문으로 분류 정확도를 재는 CLI.

파이프라인이 보고하는 계획<->blind 검사 일치율은 생성 문서에 대한 숫자이고, 생성 프롬프트가
그 일치율을 직접 최적화 대상으로 삼고 있다. 이 명령은 **실제 라벨이 붙은 원문**에
대한 정확도를 재서 그 일치율을 맥락에 놓는다. 둘의 차이가 곧 생성 문서가 실제보다
쉬운 정도다.

기본값은 실호출을 하지 않는다. manifest를 만들고 표본 구성만 보여주는 것까지가
기본이고, 실제 LLM 호출은 ``--execute``를 명시할 때만 일어난다. 호출 수는
``사례 수 x 2``(판별 모델 + validator 모델)이고 그대로 과금된다.

사용 예:

    # 1) 후보에서 평가셋을 뽑고 구성만 확인 (호출 없음, 무료)
    python scripts/run_holdout_classification_eval.py \\
        --candidates data/holdout/candidates.json \\
        --manifest-out data/holdout/manifest.json

    # 2) 실제로 채점 (과금됨)
    python scripts/run_holdout_classification_eval.py \\
        --manifest data/holdout/manifest.json \\
        --snapshots data/holdout/snapshots.json \\
        --classifier-model gpt-5 --validator-model gpt-5-mini \\
        --report-out data/holdout/report.json \\
        --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rd2.source_generation.contracts import SourceDocumentSnapshot  # noqa: E402
from rd2.source_generation.holdout_eval import (  # noqa: E402
    HoldoutCase,
    HoldoutEvalConfig,
    HoldoutManifest,
    build_stratified_manifest,
    run_holdout_eval,
)
from rd2.source_generation.pipeline import (  # noqa: E402
    PipelineConfig,
    default_openai_gateway,
)
from rd2.source_generation.prompts import build_prompt_bundle  # noqa: E402


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _print_manifest_summary(manifest: HoldoutManifest) -> None:
    print(f"manifest_id     : {manifest.manifest_id}")
    print(f"manifest_sha256 : {manifest.sha256}")
    print(f"seed            : {manifest.seed}")
    print(f"cases           : {len(manifest.cases)}")
    print("stratum 구성:")
    for stratum, count in sorted(manifest.stratum_counts().items()):
        print(f"  {stratum:<28} {count}")


def _print_report_summary(report_json: str) -> None:
    report = json.loads(report_json)
    print(f"\nmanifest_sha256      : {report['manifest_sha256']}")
    print(f"taxonomy_version     : {report['taxonomy_version']}")
    print(f"validator_prompt_sha256 : {report['validator_prompt_sha256']}")
    for result in report["results"]:
        accuracy = result["accuracy"]
        print(f"\n[{result['model_id']}] 채점 {accuracy['scored']}건")
        for label, key in (
            ("문서유형", "document_type_accuracy"),
            ("C/S/O ", "classification_accuracy"),
            ("조항  ", "clause_accuracy"),
        ):
            print(f"  {label} {accuracy[key]:.3f}")
        if accuracy["subclause_scored"]:
            print(
                f"  세부조항 {accuracy['subclause_accuracy']:.3f} "
                f"(정답 있는 {accuracy['subclause_scored']}건 기준)"
            )
        else:
            print("  세부조항 (정답 라벨이 없어 채점 제외)")
        over = result["over_flagging"]
        if over["open_scored"]:
            print(
                f"  오탐률 {over['over_flagging_rate']:.3f} "
                f"— 공개문서 {over['open_scored']}건 중 "
                f"{over['flagged_c_or_s']}건을 C/S로 판정"
            )
        if result["failed_case_ids"]:
            print(f"  실패 {len(result['failed_case_ids'])}건")
        confused = [
            pair for pair in result["boundary_pairs"] if pair["confusions"] > 0
        ]
        if confused:
            print("  경계쌍 혼동:")
            for pair in confused:
                print(
                    f"    {pair['left']} <-> {pair['right']}: "
                    f"{pair['confusions']}건"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--titles", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--per-stratum", type=int, default=12)
    parser.add_argument("--exclude", type=Path, help="생성 입력으로 이미 쓴 문서 ID 목록")
    parser.add_argument("--classifier-model")
    parser.add_argument("--validator-model")
    parser.add_argument("--max-output-tokens", type=int, default=4_000)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 LLM 호출을 수행한다. 사례 수 x 2회가 과금된다.",
    )
    args = parser.parse_args()

    if not args.candidates and not args.manifest:
        parser.error("--candidates 또는 --manifest 중 하나가 필요하다")

    if args.manifest:
        manifest = HoldoutManifest.model_validate(_load_json(args.manifest))
    else:
        excluded = tuple(_load_json(args.exclude)) if args.exclude else ()
        candidates = [
            HoldoutCase.model_validate(item) for item in _load_json(args.candidates)
        ]
        manifest = build_stratified_manifest(
            candidates,
            manifest_id=f"holdout-{args.seed}",
            created_at=datetime.now(UTC),
            seed=args.seed,
            per_stratum=args.per_stratum,
            excluded_document_ids=excluded,
        )

    _print_manifest_summary(manifest)

    if args.manifest_out:
        _write_json(args.manifest_out, manifest.model_dump_json(indent=2))
        print(f"\nmanifest 저장: {args.manifest_out}")

    if not args.execute:
        estimated = len(manifest.cases) * 2
        print(
            f"\n[dry-run] LLM 호출 없음. --execute를 주면 약 {estimated}회 호출된다."
        )
        return 0

    for required in (
        "snapshots",
        "classifier_model",
        "validator_model",
        "report_out",
    ):
        if not getattr(args, required):
            parser.error(f"--execute에는 --{required.replace('_', '-')}가 필요하다")

    snapshots = {
        document_id: SourceDocumentSnapshot.model_validate(payload)
        for document_id, payload in _load_json(args.snapshots).items()
    }
    titles = _load_json(args.titles) if args.titles else {}

    report = run_holdout_eval(
        manifest,
        snapshots,
        titles,
        default_openai_gateway(),
        pipeline_config=PipelineConfig(
            classifier_model=args.classifier_model,
            generator_model=args.classifier_model,
            validator_model=args.validator_model,
        ),
        generated_at=datetime.now(UTC),
        prompt_bundle=build_prompt_bundle(),
        config=HoldoutEvalConfig(max_output_tokens=args.max_output_tokens),
    )

    report_json = report.model_dump_json(indent=2)
    _write_json(args.report_out, report_json)
    _print_report_summary(report_json)
    print(f"\n리포트 저장: {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
