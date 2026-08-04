"""C/S 생성 CSV 한 행의 원본·span·합성 경로를 Markdown으로 설명한다."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _json(value: str, default):
    try:
        return json.loads(value) if value else default
    except json.JSONDecodeError:
        return default


def explain_row(
    row: dict[str, str], *, csv_path: Path, repo_root: Path | None = None
) -> str:
    seed_type = row.get("seed_type", "")
    trace = _json(row.get("generation_trace_json", ""), {})
    field_source = _json(row.get("field_source", ""), {})
    line_ids = _json(row.get("seed_line_ids", ""), [])
    source_path = row.get("seed_source_path", "")
    source_abs = ((repo_root or Path.cwd()) / source_path).resolve() if source_path else None

    lines = [
        f"# 생성 근거: {row.get('row_id', '')}",
        "",
        "## 결과",
        "",
        f"- 제목: {row.get('title', '')}",
        f"- 기관: {row.get('ordering_agency', '')}",
        f"- 조항/세부유형: {row.get('clause_no', '')} / {row.get('cso_subclause_key', '')}",
        f"- 생성 유형: `{seed_type}`",
        f"- 모델/프롬프트: `{row.get('model', '')}` / `{row.get('prompt_version', '')}`",
        "",
        "## 생성 경로",
        "",
    ]
    if seed_type == "span_seeded":
        lines.extend(
            [
                "1. 수집된 실제 문서에서 후보 탐지 규칙이 span을 찾음",
                "2. extraction ID와 원본 해시를 검증함",
                "3. 지정된 line ID가 현재 추출본과 같은지 검증함",
                "4. span이 속한 블록과 제한된 앞뒤 문맥을 LLM 입력으로 사용함",
                "5. LLM이 원문에 있는 내용만 근거로 본문 필드를 재구성함",
                "",
                "## 원본 및 span",
                "",
                f"- 원본 상대경로: `{source_path or '없음'}`",
                f"- 현재 로컬 원본 존재: `{bool(source_abs and source_abs.exists())}`",
                f"- 원본 확인 경로: `{source_abs or '해당 없음'}`",
                f"- candidate ID: `{row.get('seed_candidate_id', '')}`",
                f"- candidate run ID: `{row.get('seed_candidate_run_id', '')}`",
                f"- extraction ID: `{row.get('seed_extraction_id', '')}`",
                f"- line IDs: `{line_ids}`",
                f"- span SHA-256: `{row.get('seed_text_sha256', '')}`",
                "",
                "### 매칭 span",
                "",
                row.get("matched_span_text", "") or "(비어 있음)",
                "",
                "### LLM 입력 문맥",
                "",
                row.get("seed_context_text", "")
                or "(구버전 CSV에는 저장되지 않아 정확한 문맥 창을 복원할 수 없음)",
            ]
        )
    elif seed_type == "synthetic_fallback":
        scenario = trace.get("scenario_prompt") or row.get("seed_context_text", "")
        lines.extend(
            [
                "1. 사용할 수 있는 실제 span 후보가 없거나 목표 건수보다 부족했음",
                "2. 조항에 등록된 독립 합성 시나리오를 선택함",
                "3. 기관명·생산일자를 별도 선택함",
                "4. LLM이 제목과 본문을 합성함",
                "",
                "## 원본 및 시나리오",
                "",
                "- 원본 파일: 없음 — 완전 합성 폴백",
                f"- 시나리오 번호: `{trace.get('scenario_index', '구버전 CSV라 미기록')}`",
                f"- 시나리오: {scenario or '구버전 CSV라 정확한 값 미기록'}",
                f"- 기관 선택 출처: `{trace.get('agency_selection_source', field_source.get('ordering_agency', ''))}`",
            ]
        )
    else:
        lines.extend(
            [
                "- 원본 span이나 LLM 호출 없이 결정적 템플릿에서 생성됨",
                f"- 상세: `{trace or field_source}`",
            ]
        )

    lines.extend(
        [
            "",
            "## 필드별 출처",
            "",
            "```json",
            json.dumps(field_source, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 기계 판독 추적정보",
            "",
            "```json",
            json.dumps(trace, ensure_ascii=False, indent=2),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--row-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    with args.csv.open(encoding="utf-8-sig", newline="") as handle:
        row = next(
            (item for item in csv.DictReader(handle) if item.get("row_id") == args.row_id),
            None,
        )
    if row is None:
        raise SystemExit(f"row_id를 찾을 수 없습니다: {args.row_id}")
    report = explain_row(row, csv_path=args.csv, repo_root=args.repo_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output.resolve())
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
