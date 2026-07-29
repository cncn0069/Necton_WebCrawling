"""C/S coverage plan 전용 CLI — LLM 호출이나 RDS 반영 없이 계획만 만든다.

설계 문서 docs/design-coverage-matrix-diversity-audit-20260723.md
"Target User & Narrowest Wedge" 절의 v1a 최소 유용 범위:

    python scripts/plan_cs_generation.py \
      --c-target 30000 --s-target 30000 \
      --output-dir output/audit/run-001

candidate manifest(find_candidates.py 산출물)를 읽고, 실측 span 후보를
(세부조항, 문서유형, 기관군) 단위로 프로파일링한 뒤, exact-total largest
remainder 배분으로 generation_plan.json/.csv를 atomic 게시한다. v1b(auditor)와
generate_cs_pilot.py의 --coverage-plan 연동은 이 스코프에 없다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _common import ensure_src_on_path

ensure_src_on_path()

import pymysql
import pymysql.cursors
from dotenv import load_dotenv

from rd2.canonical import compute_candidate_profile_digest
from rd2.generators.agency_categories import AGENCY_CATEGORIES
from rd2.generators.candidate_manifest import fetch_all_span_candidates, load_candidate_manifest
from rd2.generators.candidate_profile import profile_candidates, write_candidate_profiles_atomic
from rd2.generators.generation_plan_schema import build_generation_plan, write_generation_plan_atomic

load_dotenv()

DEFAULT_AGENCY_WEIGHTS: dict[str, float] = {
    category: 1.0 / len(AGENCY_CATEGORIES) for category in AGENCY_CATEGORIES
}


def connect_mariadb() -> "pymysql.connections.Connection":
    """운영 MariaDB에 읽기 전용으로 연결한다(generate_cs_pilot.py와 동일 패턴).

    이 스크립트 어디에도 connection.commit()/INSERT/UPDATE를 호출하지 않는
    것으로 읽기 전용을 코드 수준에서 보장한다.
    """
    try:
        return pymysql.connect(
            host=os.environ["MARIADB_HOST"],
            port=int(os.environ.get("MARIADB_PORT", 3306)),
            user=os.environ["MARIADB_USER"],
            password=os.environ["MARIADB_PASSWORD"],
            database=os.environ["MARIADB_DATABASE"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except KeyError as exc:
        raise RuntimeError(f"MariaDB 접속 정보 누락: {exc} 환경변수가 .env에 없습니다.") from exc
    except pymysql.MySQLError as exc:
        raise RuntimeError(f"MariaDB 접속 실패: {exc}") from exc


def parse_agency_weights(raw: str | None) -> dict[str, float]:
    if raw is None:
        return dict(DEFAULT_AGENCY_WEIGHTS)
    text = raw
    candidate_path = Path(raw)
    if candidate_path.exists():
        text = candidate_path.read_text(encoding="utf-8")
    weights = json.loads(text)
    if not isinstance(weights, dict):
        raise ValueError("--agency-weights는 JSON object여야 합니다")
    missing = set(AGENCY_CATEGORIES) - set(weights)
    if missing:
        raise ValueError(f"--agency-weights에 기관군이 빠졌습니다: {sorted(missing)}")
    extra = set(weights) - set(AGENCY_CATEGORIES)
    if extra:
        raise ValueError(f"--agency-weights에 알 수 없는 기관군이 있습니다: {sorted(extra)}")
    return {k: float(v) for k, v in weights.items()}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--c-target", type=int, default=30000)
    parser.add_argument("--s-target", type=int, default=30000)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-per-valid-cell", type=int, default=5)
    parser.add_argument(
        "--admin-status-ratio",
        type=float,
        default=0.10,
        help=(
            "전체 생성물 중 행정상태를 부여할 목표 비율. 기본 0.10(10%%), "
            "0이면 행정상태 생성 안 함."
        ),
    )
    parser.add_argument(
        "--agency-weights",
        type=str,
        default=None,
        help="JSON object(기관군->비중) 또는 그 JSON을 담은 파일 경로. 기본값은 5개 기관군 균등 20%%.",
    )
    parser.add_argument(
        "--allocation-seed",
        type=int,
        default=42,
        help="run manifest에 기록만 됨 — v1a 배분기는 완전 결정적이라 실제 난수는 쓰지 않음.",
    )
    parser.add_argument("--max-rows-per-candidate", type=int, default=1)
    parser.add_argument("--candidates-dir", type=Path, default=Path("data/candidates"))
    parser.add_argument("--allow-partial-candidates", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    agency_weights = parse_agency_weights(args.agency_weights)

    candidate_manifest = load_candidate_manifest(
        args.candidates_dir, allow_partial=args.allow_partial_candidates
    )
    candidates_by_clause = fetch_all_span_candidates(
        args.candidates_dir,
        expected_run_id=candidate_manifest["run_id"],
        expected_rule_version=candidate_manifest["rule_version"],
        expected_counts=candidate_manifest["counts"],
    )

    conn = connect_mariadb()
    try:
        profile_rows = profile_candidates(candidates_by_clause, conn=conn)
    finally:
        conn.close()

    profile_manifest = write_candidate_profiles_atomic(
        args.candidates_dir, profile_rows, candidate_manifest=candidate_manifest
    )
    candidate_profile_digest = compute_candidate_profile_digest(profile_manifest)

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan = build_generation_plan(
        c_target=args.c_target,
        s_target=args.s_target,
        minimum_per_valid_cell=args.minimum_per_valid_cell,
        admin_status_ratio=args.admin_status_ratio,
        agency_weights=agency_weights,
        allocation_seed=args.allocation_seed,
        max_rows_per_candidate=args.max_rows_per_candidate,
        candidate_profile_rows=profile_rows,
        candidate_manifest={
            "run_id": candidate_manifest["run_id"],
            "rule_version": candidate_manifest["rule_version"],
        },
        candidate_profile_digest=candidate_profile_digest,
        created_at=created_at,
    )
    write_generation_plan_atomic(args.output_dir, plan)

    print(
        f"generation plan 게시 완료: run_id={plan.run_id} "
        f"cells={len(plan.cells)} output_dir={args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
