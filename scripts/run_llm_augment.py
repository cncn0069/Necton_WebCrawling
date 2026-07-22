"""data/candidates/clause_{N}.jsonl의 후보를 문서 단위로 묶어 LLM에 보내고,
치환 결과를 data/augmented/llm/에 저장한다 (Hard-example 증강).

사용 예:
    python scripts/run_llm_augment.py --clause 8 --limit 1 --dry-run  # 프롬프트만 확인
    python scripts/run_llm_augment.py --clause 8 --limit 1            # 실제 호출 1건
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.augmentation.llm_augment import (  # noqa: E402
    CandidateValidationError,
    augment_document,
    build_messages,
    validate_candidates_for_document,
)
from rd2.storage.db import DocumentStore  # noqa: E402

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_CANDIDATES_ROOT = _DATA_ROOT / "candidates"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_AUGMENTED_LLM_ROOT = _DATA_ROOT / "augmented" / "llm"
_CANDIDATE_MANIFEST = "_manifest.json"


def _load_candidates_by_doc(
    clause_no: str,
    *,
    expected_run_id: str | None = None,
    expected_rule_version: str | None = None,
    expected_count: int | None = None,
) -> dict[str, list[dict]]:
    path = _CANDIDATES_ROOT / f"clause_{clause_no}.jsonl"
    by_doc: dict[str, list[dict]] = defaultdict(list)
    extraction_ids_by_source: dict[str, str] = {}
    seen_candidate_ids: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            c = json.loads(line)
            source_path = c.get("source_path")
            candidate_id = c.get("candidate_id")
            extraction_id = c.get("extraction_id")
            line_ids = c.get("line_ids")
            text_sha256 = c.get("text_sha256")
            run_id = c.get("run_id")
            if not isinstance(source_path, str) or not source_path:
                raise ValueError(f"{path}:{line_no}: source_path가 없는 v2 후보")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise ValueError(f"{path}:{line_no}: candidate_id가 없는 v2 후보")
            if candidate_id in seen_candidate_ids:
                raise ValueError(f"{path}:{line_no}: 중복 candidate_id: {candidate_id}")
            if not isinstance(extraction_id, str) or not extraction_id:
                raise ValueError(f"{path}:{line_no}: extraction_id가 없는 v2 후보")
            if not isinstance(line_ids, list) or not line_ids:
                raise ValueError(f"{path}:{line_no}: line_ids가 없는 v2 후보")
            if not isinstance(text_sha256, str) or not text_sha256:
                raise ValueError(f"{path}:{line_no}: text_sha256가 없는 v2 후보")
            if expected_run_id is not None and run_id != expected_run_id:
                raise ValueError(
                    f"{path}:{line_no}: 후보 run_id가 manifest와 불일치: "
                    f"record={run_id!r}, manifest={expected_run_id!r}"
                )
            if (
                expected_rule_version is not None
                and c.get("rule_version") != expected_rule_version
            ):
                raise ValueError(
                    f"{path}:{line_no}: 후보 rule_version이 manifest와 불일치: "
                    f"record={c.get('rule_version')!r}, manifest={expected_rule_version!r}"
                )

            previous_extraction_id = extraction_ids_by_source.setdefault(source_path, extraction_id)
            if previous_extraction_id != extraction_id:
                raise ValueError(
                    f"{path}:{line_no}: 같은 source_path에 서로 다른 extraction_id가 섞임: "
                    f"{source_path}"
                )
            seen_candidate_ids.add(candidate_id)
            by_doc[source_path].append(c)
    if expected_count is not None and len(seen_candidate_ids) != expected_count:
        raise ValueError(
            f"{path}: 후보 개수가 manifest와 불일치: "
            f"records={len(seen_candidate_ids)}, manifest={expected_count}"
        )
    return by_doc


def _load_candidate_manifest(
    root: Path = _CANDIDATES_ROOT,
    *,
    allow_partial: bool = False,
) -> dict:
    """유료 호출이 완결된 한 번의 후보 생성 결과만 소비하게 한다."""
    path = root / _CANDIDATE_MANIFEST
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
            "find_candidates.py를 정상 완료하거나 --allow-partial-candidates를 "
            "명시해야 합니다."
        )
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]:
        raise RuntimeError("후보 manifest에 run_id가 없습니다")
    if not isinstance(manifest.get("counts"), dict):
        raise RuntimeError("후보 manifest에 counts가 없습니다")
    if not isinstance(manifest.get("rule_version"), str) or not manifest["rule_version"]:
        raise RuntimeError("후보 manifest에 rule_version이 없습니다")
    return manifest


def _output_path(source_path: str, clause_no: str) -> Path:
    # source_path 예: "data/molit/policy_material/4623_....pdf"
    rel = Path(source_path).relative_to("data")
    return _AUGMENTED_LLM_ROOT / rel.parent / f"{rel.name}.clause{clause_no}.json"


def _validate_existing_augmented_output(
    path: Path,
    *,
    source_path: str,
    candidates: list[dict],
    manifest: dict,
    clause_no: str,
) -> None:
    """Prove an existing output belongs to the current candidate run."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"기존 증강 결과를 읽을 수 없음: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("기존 증강 결과의 최상위 값이 JSON 객체가 아님")
    if not candidates:
        raise ValueError("현재 문서 후보가 비어 있음")

    first = candidates[0]
    expected_identity = {
        "source_path": source_path,
        "extraction_id": first.get("extraction_id"),
        "candidate_run_id": manifest.get("run_id"),
        "candidate_rule_version": manifest.get("rule_version"),
        "clause_no": clause_no,
    }
    for field, expected in expected_identity.items():
        actual = payload.get(field)
        if actual != expected:
            raise ValueError(
                f"{field} 불일치: saved={actual!r}, current={expected!r}"
            )

    selections = payload.get("selections")
    if not isinstance(selections, list):
        raise ValueError("기존 증강 결과의 selections가 배열이 아님")
    candidates_by_id = {candidate.get("candidate_id"): candidate for candidate in candidates}
    selection_fields = {
        "extraction_id": "extraction_id",
        "text_sha256": "text_sha256",
        "line_ids": "line_ids",
        "page": "page",
        "original": "text",
    }
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            raise ValueError(f"selections[{index}]가 JSON 객체가 아님")
        candidate_id = selection.get("candidate_id")
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(
                f"selections[{index}] candidate_id가 현재 후보에 없음: {candidate_id!r}"
            )
        if selection.get("clause") != clause_no:
            raise ValueError(f"selections[{index}] clause 불일치")
        for output_field, candidate_field in selection_fields.items():
            if selection.get(output_field) != candidate.get(candidate_field):
                raise ValueError(f"selections[{index}] {output_field} 불일치")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clause", required=True, choices=["5", "6", "7", "8"])
    parser.add_argument("--limit", type=int, default=1, help="처리할 문서 수")
    parser.add_argument("--dry-run", action="store_true", help="실제 호출 없이 프롬프트만 출력")
    parser.add_argument("--force", action="store_true", help="이미 처리된 문서도 재실행")
    parser.add_argument(
        "--allow-partial-candidates",
        action="store_true",
        help="실패 문서가 있는 partial 후보 run도 명시적으로 허용",
    )
    args = parser.parse_args()

    manifest = _load_candidate_manifest(allow_partial=args.allow_partial_candidates)
    expected_count = manifest["counts"].get(args.clause)
    if not isinstance(expected_count, int) or expected_count < 0:
        raise RuntimeError(f"후보 manifest counts에 {args.clause}호 개수가 없습니다")
    by_doc = _load_candidates_by_doc(
        args.clause,
        expected_run_id=manifest["run_id"],
        expected_rule_version=manifest["rule_version"],
        expected_count=expected_count,
    )
    print(f"[{args.clause}호] 후보 있는 문서 {len(by_doc)}개 중 최대 {args.limit}개 처리\n")

    planned_docs: list[tuple[str, list[dict], Path]] = []
    for source_path, candidates in by_doc.items():
        if len(planned_docs) >= args.limit:
            break
        out_path = _output_path(source_path, args.clause)
        if out_path.exists() and not args.force and not args.dry_run:
            try:
                _validate_existing_augmented_output(
                    out_path,
                    source_path=source_path,
                    candidates=candidates,
                    manifest=manifest,
                    clause_no=args.clause,
                )
                validate_candidates_for_document(
                    candidates,
                    data_root=_DATA_ROOT,
                    extracted_root=_EXTRACTED_ROOT,
                )
            except (ValueError, CandidateValidationError, FileNotFoundError, OSError) as exc:
                raise RuntimeError(
                    f"stale/invalid augmented output: {out_path}: {exc}; "
                    "현재 후보로 다시 생성하려면 --force를 사용하세요"
                ) from exc
            continue
        planned_docs.append((source_path, candidates, out_path))

    # 선택된 전체 batch를 먼저 검증한다. N번째 문서가 stale인 걸 뒤늦게 알아 이미
    # 1..N-1 유료 호출 비용을 쓴 상태가 되지 않도록 첫 호출 전에 모두 확인한다.
    for source_path, candidates, _ in planned_docs:
        try:
            validate_candidates_for_document(
                candidates,
                data_root=_DATA_ROOT,
                extracted_root=_EXTRACTED_ROOT,
            )
        except (CandidateValidationError, FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"stale/invalid candidate batch: {source_path}: {exc}") from exc

    if not planned_docs:
        print("완료: 0개 문서 처리")
        return

    # 원본 O트랙 문서의 실제 메타데이터(제목/기관/생산일자)를 참고용으로 붙이기
    # 위한 DB 조회 — dry-run은 실제 저장을 안 하니 연결 자체를 안 만든다.
    store = None if args.dry_run else DocumentStore()

    processed = 0
    for source_path, candidates, out_path in planned_docs:

        print(f"=== {source_path} (후보 {len(candidates)}개) ===")

        if args.dry_run:
            messages = build_messages(candidates, args.clause)
            for m in messages:
                print(f"--- {m['role']} ---")
                print(m["content"])
            print()
            processed += 1
            continue

        selections = augment_document(candidates, args.clause)
        if not selections:
            print("  (LLM이 아무것도 선택하지 않음 — 현재 run의 빈 결과로 저장)")

        for s in selections:
            orig_len, syn_len = len(s["original"]), len(s["synthetic"])
            print(
                f"  candidate_id={s['candidate_id']} page={s['page']} "
                f"[{s['transformation']}]"
            )
            print(f"    원문({orig_len}자): {s['original']!r}")
            print(f"    치환({syn_len}자): {s['synthetic']!r}")
            print(f"    근거: {s['reason']}")

        body_file_path = str(Path(source_path).relative_to("data"))
        origin_document = store.get_by_body_file_path(body_file_path) if store else None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "source_path": source_path,
                    "source": candidates[0]["source"],
                    "doc_type": candidates[0]["doc_type"],
                    "doc_id": candidates[0]["doc_id"],
                    "extraction_id": candidates[0]["extraction_id"],
                    "candidate_run_id": manifest["run_id"] if manifest else None,
                    "candidate_rule_version": manifest.get("rule_version") if manifest else None,
                    "clause_no": args.clause,
                    "origin_document": origin_document,
                    "selections": selections,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  저장: {out_path.relative_to(_REPO_ROOT)}\n")
        processed += 1

    if store:
        store.close()

    print(f"완료: {processed}개 문서 처리")


if __name__ == "__main__":
    main()
