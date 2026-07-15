"""추출→주석→후보탐지→LLM치환 파이프라인을 한 번에 실행한다.

기본은 data/annotated/가 이미 있다고 가정하고 후보탐지+LLM치환만 돌린다
(--from-scratch를 주면 추출·주석 단계부터 전부 실행). 각 단계는
scripts/extract_pdf_text.py 등 기존 스크립트를 서브프로세스로 그대로
호출할 뿐이라, 개별 스크립트의 옵션·동작(재실행 시 파일 덮어쓰기 등)이
그대로 적용된다 — 이 스크립트는 순서대로 이어 부르는 편의 래퍼다.

사용 예:
    python scripts/run_pipeline.py --clause 5 --limit 1 --dry-run   # 프롬프트만 확인
    python scripts/run_pipeline.py --clause 5 --limit 3             # 실제 3건 치환(과금)
    python scripts/run_pipeline.py --clause all --limit 1 --dry-run
    python scripts/run_pipeline.py --clause 8 --from-scratch --source moe --limit 5
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
        "--from-scratch", action="store_true", help="추출·주석 단계부터 전부 실행(기본은 data/annotated/가 이미 있다고 보고 스킵)"
    )
    parser.add_argument("--source", default="all", help="--from-scratch일 때만 사용 (molit/mohw/moe/PRISM/all)")
    args = parser.parse_args()

    if args.from_scratch:
        _run([_PY, "scripts/extract_pdf_text.py", "--source", args.source])
        _run([_PY, "scripts/annotate_documents.py", "--source", args.source])

    _run([_PY, "scripts/find_candidates.py", "--clause", args.clause])

    clauses = _ALL_CLAUSES if args.clause == "all" else [args.clause]
    for clause_no in clauses:
        cmd = [_PY, "scripts/run_llm_augment.py", "--clause", clause_no, "--limit", str(args.limit)]
        if args.dry_run:
            cmd.append("--dry-run")
        if args.force:
            cmd.append("--force")
        _run(cmd)

    print("\n완료.")


if __name__ == "__main__":
    main()
