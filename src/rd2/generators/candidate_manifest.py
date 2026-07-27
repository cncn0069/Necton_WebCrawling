"""find_candidates.py가 발행하는 후보 manifest/jsonl 로더.

이전에는 scripts/generate_cs_pilot.py 안에만 있어 다른 스크립트가 깨끗하게
import할 수 없었다. scripts/plan_cs_generation.py(coverage planner CLI)도 같은
로더가 필요해 라이브러리 함수로 옮긴다 — 동작은 그대로다.
"""

from __future__ import annotations

import json
from pathlib import Path

_CANDIDATE_MANIFEST_NAME = "_manifest.json"

# candidates.py는 조항 5/6/7/8만 span 탐지 로직이 있다 — 1~4호는 항상 폴백.
SPAN_CLAUSES = ("5", "6", "7", "8")


def load_candidate_manifest(
    candidates_dir: Path,
    *,
    allow_partial: bool = False,
) -> dict:
    path = candidates_dir / _CANDIDATE_MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"후보 manifest가 없습니다: {path}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"후보 manifest를 읽을 수 없습니다: {path} ({exc})") from exc

    status = manifest.get("status")
    if status != "complete" and not (allow_partial and status == "partial"):
        raise RuntimeError(
            f"후보 생성이 완결되지 않았습니다(status={status!r}). "
            "--allow-partial-candidates를 명시하지 않으면 유료 생성을 시작하지 않습니다."
        )
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]:
        raise RuntimeError("후보 manifest에 run_id가 없습니다")
    if not isinstance(manifest.get("counts"), dict):
        raise RuntimeError("후보 manifest에 counts가 없습니다")
    if not isinstance(manifest.get("rule_version"), str) or not manifest["rule_version"]:
        raise RuntimeError("후보 manifest에 rule_version이 없습니다")
    return manifest


def fetch_all_span_candidates(
    candidates_dir: Path,
    *,
    expected_run_id: str | None = None,
    expected_rule_version: str | None = None,
    expected_counts: dict[str, int] | None = None,
) -> dict[str, list[dict]]:
    """data/candidates/clause_{5,6,7,8}.jsonl을 읽어 clause_no로 버킷팅한다.

    candidate dict 자체에는 clause_no가 들어있지 않다(candidates.py의
    _base_candidate 참고) — 어느 파일에서 나왔는지로만 조항을 구분한다.
    """
    buckets: dict[str, list[dict]] = {}
    for clause_no in SPAN_CLAUSES:
        path = candidates_dir / f"clause_{clause_no}.jsonl"
        candidates: list[dict] = []
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if line:
                        candidate = json.loads(line)
                        if expected_run_id is not None and candidate.get("run_id") != expected_run_id:
                            raise ValueError(
                                f"{path}:{line_no}: 후보 run_id가 manifest와 불일치: "
                                f"record={candidate.get('run_id')!r}, "
                                f"manifest={expected_run_id!r}"
                            )
                        if (
                            expected_rule_version is not None
                            and candidate.get("rule_version") != expected_rule_version
                        ):
                            raise ValueError(
                                f"{path}:{line_no}: 후보 rule_version이 manifest와 불일치: "
                                f"record={candidate.get('rule_version')!r}, "
                                f"manifest={expected_rule_version!r}"
                            )
                        candidates.append(candidate)
        if expected_counts is not None:
            expected_count = expected_counts.get(clause_no)
            if not isinstance(expected_count, int) or expected_count < 0:
                raise ValueError(f"후보 manifest counts에 {clause_no}호 개수가 없음")
            if len(candidates) != expected_count:
                raise ValueError(
                    f"{path}: 후보 개수가 manifest와 불일치: "
                    f"records={len(candidates)}, manifest={expected_count}"
                )
        buckets[clause_no] = candidates
    return buckets
