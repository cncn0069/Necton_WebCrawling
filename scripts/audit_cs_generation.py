"""C/S 생성 결과 다양성 감사 CLI (v1b — coverage/exact-dup/format/검수 샘플러).

설계 문서 docs/design-coverage-matrix-diversity-audit-20260723.md
"Target User & Narrowest Wedge":

    python scripts/audit_cs_generation.py \
      --plan output/audit/run-001/generation_plan.json \
      --input output/cs/generated.csv \
      --pdf-dir output/cs/pdfs \
      --sample-count 15 \
      --output-dir output/audit/run-001

generated CSV를 변경하거나 차단하지 않는다 — 감사 리포트와 검수 목록만
만든다. semantic(v1c)과 agency proxy(v1d)는 아직 구현하지 않아
diversity_summary.json에서 항상 "not_run"으로 표시된다.

주의: 이 CLI가 읽는 generated CSV에는 아직 agency_category/coverage_cell_key/
coverage_slot/coverage_plan_run_id 컬럼이 없다(§5의 --coverage-plan 생성기
연동이 끝나야 생긴다) — 그 전까지는 실제 generate_cs_pilot.py 출력을 넣으면
"필수 필드 누락"으로 fail-fast한다. 이는 알려진 제약이지 버그가 아니다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.audit.orchestrator import run_audit
from rd2.audit.row_contract import AuditContractError
from rd2.generators.generation_plan_schema import GenerationPlan
import json


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, required=True, help="generation_plan.json 경로")
    parser.add_argument("--input", type=Path, required=True, help="generated CSV 경로")
    parser.add_argument("--pdf-dir", type=Path, default=None, help="PDF가 있는 디렉터리(없으면 pdf_unavailable로만 기록)")
    parser.add_argument("--sample-count", type=int, default=15, help="검수 샘플 수(10~20 권장, 기본 15)")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    plan_data = json.loads(args.plan.read_text(encoding="utf-8"))
    plan = GenerationPlan.from_dict(plan_data, source_path=str(args.plan))

    try:
        summary = run_audit(
            plan=plan,
            input_csv=args.input,
            pdf_dir=args.pdf_dir,
            sample_count=args.sample_count,
            output_dir=args.output_dir,
        )
    except AuditContractError as exc:
        print(f"감사 실패(계약 위반): {exc}", file=sys.stderr)
        return 1

    run_info = summary["run"]
    print(
        f"audit 게시 완료: audit_run_id={run_info['audit_run_id']} "
        f"status={run_info['status']} ok_rows={run_info['row_count_ok']} "
        f"row_errors={run_info['row_error_count']} output_dir={args.output_dir}"
    )
    return 1 if run_info["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
