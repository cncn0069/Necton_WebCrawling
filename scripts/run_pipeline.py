"""추출(PDF+HWP)→주석→후보탐지 파이프라인을 한 번에 실행한다.

기본은 data/annotated/가 이미 있다고 가정하고 후보탐지만 돌린다
(--from-scratch를 주면 추출·주석 단계부터 전부 실행). 각 단계는
scripts/extract_pdf_text.py 등 기존 스크립트를 서브프로세스로 그대로
순서대로(직렬로, 하나 끝나야 다음 시작) 호출할 뿐이라, 개별 스크립트의
옵션·동작(재실행 시 파일 덮어쓰기 등)이 그대로 적용된다 — 이 스크립트는
순서대로 이어 부르는 편의 래퍼다.

2026-07-20: LLM 치환·PDF 재구성 단계는 범위 축소로 폐기됨(AUGMENTATION_STATUS.md
참고) — 이 파이프라인은 정규식 기반 후보탐지까지만 담당한다.

사용 예:
    python scripts/run_pipeline.py --clause 5              # 후보탐지만
    python scripts/run_pipeline.py --clause all --from-scratch --source moe
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
    parser.add_argument(
        "--from-scratch", action="store_true", help="추출·주석 단계부터 전부 실행(기본은 data/annotated/가 이미 있다고 보고 스킵)"
    )
    parser.add_argument(
        "--source",
        default="all",
        help='--from-scratch일 때만 사용. data/{source}/ 폴더명 아무거나 가능(고정 목록 아님) — '
        '"all"이면 존재하는 출처 폴더 전부.',
    )
    args = parser.parse_args()

    if args.from_scratch:
        _run([_PY, "scripts/extract_pdf_text.py", "--source", args.source])
        _run([_PY, "scripts/extract_hwp_text.py", "--source", args.source])
        _run([_PY, "scripts/annotate_documents.py", "--source", args.source])

    _run([_PY, "scripts/find_candidates.py", "--clause", args.clause])

    print("\n완료.")


if __name__ == "__main__":
    main()
