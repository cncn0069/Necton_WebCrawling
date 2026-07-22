"""통합 추출→후보탐지→LLM치환 파이프라인을 한 번에 실행한다.

기본은 canonical data/extracted/*.json.gz가 이미 있다고 가정하고 후보탐지와
LLM치환만 돌린다. --from-scratch를 주면 PDF/HWP/HWPX를 같은 v2 스키마로
추출하는 scripts/extract_documents.py부터 실행한다. 별도 annotated 사본이나
structured sidecar는 만들지 않는다. 각 단계는 기존 스크립트를 서브프로세스로
호출할 뿐이라, 개별 스크립트의 옵션·동작(재실행 시 파일 덮어쓰기 등)이
그대로 적용된다 — 이 스크립트는 순서대로 이어 부르는 편의 래퍼다.

사용 예:
    python scripts/run_pipeline.py --clause 5 --limit 1 --dry-run   # 프롬프트만 확인
    python scripts/run_pipeline.py --clause 5 --limit 3             # 실제 3건 치환(과금)
    python scripts/run_pipeline.py --clause all --limit 1 --dry-run
    python scripts/run_pipeline.py --clause 8 --from-scratch --source all --limit 5
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_PY = sys.executable
_ALL_CLAUSES = ["5", "6", "7", "8"]


def _run(args: list[str]) -> None:
    print(f"\n$ {' '.join(args)}")
    subprocess.run(args, cwd=_REPO_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clause", required=True, choices=[*_ALL_CLAUSES, "all"])
    parser.add_argument("--limit", type=int, default=1, help="run_llm_augment.py에 전달할 처리 문서 수(조항당)")
    parser.add_argument("--dry-run", action="store_true", help="LLM 실제 호출 없이 프롬프트만 확인(비용 없음)")
    parser.add_argument("--force", action="store_true", help="이미 처리된 문서도 재실행")
    parser.add_argument(
        "--allow-partial-candidates",
        action="store_true",
        help="partial extraction/candidate run을 후보 및 LLM 단계에서 명시적으로 허용",
    )
    parser.add_argument(
        "--from-scratch", action="store_true",
        help="통합 추출부터 전부 실행(기본은 canonical data/extracted가 있다고 보고 스킵)",
    )
    parser.add_argument(
        "--source",
        default="all",
        help=(
            '--from-scratch일 때 사용. 전역 후보 파일의 안전한 발행을 위해 통합 파이프라인은 '
            '"all"만 허용한다. 부분 추출은 extract_documents.py를 직접 사용한다.'
        ),
    )
    args = parser.parse_args()

    if args.source != "all":
        parser.error(
            "통합 파이프라인의 --source는 'all'만 허용합니다. 부분 파일럿은 "
            "extract_documents.py로 추출 결과만 확인한 뒤, 전역 후보 발행 전 "
            "--source all 추출을 실행하세요."
        )

    if args.from_scratch:
        extraction_command = [_PY, "scripts/extract_documents.py", "--source", args.source]
        if args.force:
            extraction_command.append("--force")
        _run(extraction_command)

    candidate_command = [_PY, "scripts/find_candidates.py", "--clause", args.clause]
    if args.allow_partial_candidates:
        candidate_command.append("--allow-partial")
    _run(candidate_command)

    clauses = _ALL_CLAUSES if args.clause == "all" else [args.clause]
    for clause_no in clauses:
        cmd = [_PY, "scripts/run_llm_augment.py", "--clause", clause_no, "--limit", str(args.limit)]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.force:
            cmd.append("--force")
        if args.allow_partial_candidates:
            cmd.append("--allow-partial-candidates")
        _run(cmd)

    print("\n완료.")


if __name__ == "__main__":
    main()
