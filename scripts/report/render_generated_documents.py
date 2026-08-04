"""생성 계약 파일 또는 디렉터리를 문서 유형별 PDF로 렌더링한다."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
from typing import Any

from rd2.generators.balanced_batch_selection import (
    ALL_TEMPLATE_SLUGS,
    BalancedTemplateAssignment,
    BalancedTemplateSelector,
    template_slugs_for_document_type,
)
from rd2.generators.generated_document_pipeline import (
    GeneratedDocumentPipelineError,
    render_generation_payload,
)
from rd2.generators.output_naming import (
    rename_rendered_files,
    requested_output_filename,
)
from rd2.generators.payload_document_type import (
    PayloadDocumentTypeResolution,
    apply_payload_document_type,
    resolve_payload_document_type,
)
from rd2.generators.paged_output import RenderedSourceTextError
from rd2.generators.pdf_sensitive_evidence import (
    verify_rendered_sensitive_evidence,
)
from rd2.generators.synthetic_scan import render_synthetic_scan_pdf
from rd2.generators.synthetic_handwriting import (
    render_synthetic_handwriting_pdf,
)

_SUPPORTED_INPUT_SUFFIXES = frozenset({".json", ".jsonl", ".txt"})
SUCCESSFUL_RENDER_STATUSES = frozenset({"ok", "ok_truncated"})
_COMPACT_VARIATION_INDEX = 2
_MAX_SOURCE_TEXT_RENDER_ATTEMPTS = 3


def _load_payloads(input_path: Path) -> list[dict[str, Any]]:
    if input_path.suffix.lower() == ".jsonl":
        payloads = [
            json.loads(line)
            for line in input_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        parsed = json.loads(input_path.read_text(encoding="utf-8"))
        payloads = parsed if isinstance(parsed, list) else [parsed]

    if not payloads:
        raise ValueError("Input contains no generation payloads")
    if not all(isinstance(payload, dict) for payload in payloads):
        raise ValueError("Every input payload must be a JSON object")
    return payloads


def _output_id(payload: dict[str, Any], index: int) -> str:
    requested = requested_output_filename(payload, index)
    if requested is not None:
        return Path(requested).stem
    receipt = payload.get("receipt")
    request_id = receipt.get("request_id") if isinstance(receipt, dict) else None
    raw = str(request_id or f"document-{index:05d}")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        return f"document-{index:05d}"
    if len(safe) > 96:
        digest = sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:80].rstrip('-._')}-{digest}"
    return safe


def _document_type(payload: dict[str, Any]) -> str | None:
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    source_classification = result.get("source_classification")
    if not isinstance(source_classification, dict):
        return None
    value = source_classification.get("document_type")
    return str(value) if isinstance(value, str) and value else None


def _input_files(input_dir: Path) -> list[Path]:
    files = sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in _SUPPORTED_INPUT_SUFFIXES
    )
    if not files:
        supported = ", ".join(sorted(_SUPPORTED_INPUT_SUFFIXES))
        raise ValueError(
            f"Input directory has no supported files ({supported}): {input_dir}"
        )
    return files


def _unique_document_id(
    payload: dict[str, Any],
    index: int,
    used_document_ids: set[str],
) -> str:
    document_id = _output_id(payload, index)
    if document_id in used_document_ids:
        document_id = f"{document_id}-{index:05d}"
    used_document_ids.add(document_id)
    return document_id


def _write_document_manifest(
    output_dir: Path,
    manifest: list[dict[str, object]],
) -> None:
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _cleanup_rendered_artifacts(
    output_dir: Path,
    rendered: list[dict[str, object]],
) -> None:
    """최종 후처리가 실패한 문서의 게시 파일만 제거한다."""

    resolved_output_dir = output_dir.resolve()
    for entry in rendered:
        for key in ("pdf", "html"):
            raw_path = entry.get(key)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            artifact_path = Path(raw_path)
            try:
                resolved_artifact = artifact_path.resolve()
            except OSError:
                continue
            if (
                resolved_artifact == resolved_output_dir
                or resolved_output_dir not in resolved_artifact.parents
            ):
                continue
            artifact_path.unlink(missing_ok=True)
    (output_dir / "manifest.json").unlink(missing_ok=True)
    for directory in sorted(
        (path for path in output_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        output_dir.rmdir()
    except OSError:
        pass


def _renderer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """v2 pipeline result를 공문 렌더러의 작은 envelope로 투영한다.

    원본 수집 계약의 ``ordering_agency``는 생성 IR의 본문 필드가 아니다.
    렌더 경계에서 ``generated_document.agency_name``으로 한 번만 옮겨 발행기관
    문서정보로 쓴다. 로고 파일 경로는 외부 입력에서 받지 않는다.
    """

    agency_name = next(
        (
            str(payload[key]).strip()
            for key in ("agency_name", "ordering_agency")
            if isinstance(payload.get(key), str) and str(payload[key]).strip()
        ),
        None,
    )

    def with_agency_name(document: dict[str, Any]) -> dict[str, Any]:
        if document.get("agency_name") or agency_name is None:
            return document
        return {**document, "agency_name": agency_name}

    result = payload.get("result")
    if isinstance(result, dict):
        document = result.get("generated_document")
        if not isinstance(document, dict):
            return payload
        projected_document = with_agency_name(document)
        if projected_document is document:
            return payload
        return {
            **payload,
            "result": {
                **result,
                "generated_document": projected_document,
            },
        }
    artifact = payload.get("generation_artifact")
    plan = payload.get("generation_plan")
    if not isinstance(artifact, dict) or not isinstance(plan, dict):
        return payload
    document = artifact.get("generated_document")
    if not isinstance(document, dict):
        return payload
    assessment = payload.get("source_assessment")
    source_classification = (
        assessment.get("source_classification")
        if isinstance(assessment, dict)
        else None
    )
    return {
        **payload,
        "result": {
            "contract_version": artifact.get(
                "contract_version",
                document.get("contract_version"),
            ),
            "generation_route": plan.get("generation_route"),
            "generation_target": plan.get("final_target"),
            "generated_document": with_agency_name(document),
            "source_classification": source_classification,
        },
        "receipt": payload.get("generation_receipt"),
        "provenance": artifact.get("provenance"),
    }


def _prepare_renderer_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], PayloadDocumentTypeResolution]:
    projected = _renderer_payload(payload)
    resolution = resolve_payload_document_type(projected)
    return apply_payload_document_type(projected, resolution), resolution


def _uses_verbatim_renderer(payload: dict[str, Any]) -> bool:
    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    return bool(
        result.get("generation_route") == "mask_restoration"
        or result.get("verbatim_render")
    )


def _combined_render_status(rendered: list[dict[str, object]]) -> str:
    if not rendered:
        raise RuntimeError("Successful render returned no outputs")
    statuses = {str(entry.get("status") or "") for entry in rendered}
    unexpected = statuses - SUCCESSFUL_RENDER_STATUSES
    if unexpected:
        raise RuntimeError(
            "Successful render returned non-success status(es): "
            + ", ".join(sorted(unexpected))
        )
    return "ok_truncated" if "ok_truncated" in statuses else "ok"


def _source_text_retry_candidates(
    assignment: BalancedTemplateAssignment,
    document_type: str | None,
) -> tuple[dict[str, object], ...]:
    """균등 선택 결과 뒤에 최대 두 개의 compact 대안을 붙인다."""

    candidates: list[dict[str, object]] = [
        {
            "template_slug": assignment.template_slug,
            "variation_index": assignment.variation_index,
            "variation_offset": assignment.variation_offset,
            "reason": "balanced_selection",
        }
    ]
    if assignment.renderer_family == "verbatim":
        return tuple(candidates)

    if assignment.variation_index != _COMPACT_VARIATION_INDEX:
        candidates.append(
            {
                "template_slug": assignment.template_slug,
                "variation_index": _COMPACT_VARIATION_INDEX,
                "variation_offset": _COMPACT_VARIATION_INDEX - 1,
                "reason": "same_template_compact",
            }
        )

    family_slugs = template_slugs_for_document_type(document_type)
    try:
        selected_index = family_slugs.index(assignment.template_slug)
    except ValueError:
        ordered_alternates = family_slugs
    else:
        ordered_alternates = (
            family_slugs[selected_index + 1 :]
            + family_slugs[:selected_index]
        )
    for template_slug in ordered_alternates:
        candidate = {
            "template_slug": template_slug,
            "variation_index": _COMPACT_VARIATION_INDEX,
            "variation_offset": _COMPACT_VARIATION_INDEX - 1,
            "reason": "alternate_template_compact",
        }
        if candidate not in candidates:
            candidates.append(candidate)
        if len(candidates) == _MAX_SOURCE_TEXT_RENDER_ATTEMPTS:
            break
    return tuple(candidates)


def _render_with_source_text_retries(
    payload: dict[str, Any],
    document_output_dir: Path,
    *,
    assignment: BalancedTemplateAssignment,
    document_type: str | None,
    allow_failed: bool,
    attempt_log: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """원문 누락 판정에만 compact·대체 템플릿을 순서대로 시도한다."""

    candidates = _source_text_retry_candidates(assignment, document_type)
    for attempt_number, candidate in enumerate(candidates, start=1):
        attempt = {
            "attempt": attempt_number,
            **candidate,
        }
        try:
            rendered = render_generation_payload(
                payload,
                document_output_dir,
                allow_failed=allow_failed,
                per_template=1,
                base_seed=assignment.render_seed,
                variation_offset=int(candidate["variation_offset"]),
                template_slugs={str(candidate["template_slug"])},
            )
            if len(rendered) != 1:
                raise RuntimeError(
                    "Balanced rendering must produce exactly one output, "
                    f"got {len(rendered)}"
                )
        except RenderedSourceTextError as exc:
            attempt.update(
                {
                    "status": "source_text_missing",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            attempt_log.append(attempt)
            if attempt_number == len(candidates):
                raise
            continue
        except (
            GeneratedDocumentPipelineError,
            RuntimeError,
            ValueError,
            OSError,
        ) as exc:
            attempt.update(
                {
                    "status": "rejected",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            attempt_log.append(attempt)
            raise

        attempt["status"] = _combined_render_status(rendered)
        attempt_log.append(attempt)
        return rendered, candidate

    raise RuntimeError("Source-text retry candidates were unexpectedly empty")


def _finalize_rendered_document(
    payload: dict[str, Any],
    document_output_dir: Path,
    rendered: list[dict[str, object]],
    requested_filename: str | None,
    *,
    assignment: BalancedTemplateAssignment | None = None,
) -> None:
    rename_rendered_files(rendered, requested_filename)
    evidence_results = verify_rendered_sensitive_evidence(payload, rendered)
    evidence_by_pdf = {str(item["pdf"]): item for item in evidence_results}
    for entry in rendered:
        evidence = evidence_by_pdf.get(str(entry["pdf"]))
        if evidence is not None:
            entry["sensitive_evidence"] = evidence
        if assignment is None:
            continue
        if not assignment.synthetic_handwriting:
            entry["synthetic_handwriting"] = {
                "applied": False,
                "seed": assignment.synthetic_handwriting_seed,
            }
        else:
            original_validation_scope = entry.get(
                "source_text_validation_scope"
            )
            handwriting_result = render_synthetic_handwriting_pdf(
                Path(str(entry["pdf"])),
                Path(str(entry["pdf"])),
                seed=assignment.synthetic_handwriting_seed,
            )
            handwriting_result[
                "pre_handwriting_source_text_validation_scope"
            ] = original_validation_scope
            entry["synthetic_handwriting"] = handwriting_result
            entry["source_text_validation_scope"] = (
                "pre_handwriting_pdf"
            )
        if not assignment.synthetic_scan:
            entry["synthetic_scan"] = {
                "applied": False,
                "seed": assignment.synthetic_scan_seed,
            }
            continue

        original_validation_scope = entry.get(
            "source_text_validation_scope"
        )
        scan_result = render_synthetic_scan_pdf(
            Path(str(entry["pdf"])),
            Path(str(entry["pdf"])),
            seed=assignment.synthetic_scan_seed,
        )
        scan_result["pre_scan_source_text_validation_scope"] = (
            original_validation_scope
        )
        security_marking = entry.get("security_marking")
        if isinstance(security_marking, dict):
            security_marking["stage"] = "pre_scan"
            security_marking["baked_into_scan"] = True
        entry["synthetic_scan"] = scan_result
        entry["source_text_validation_scope"] = "pre_scan_pdf"
    _write_document_manifest(document_output_dir, rendered)


def render_input_file(
    input_path: Path,
    output_dir: Path,
    *,
    allow_failed: bool = False,
    per_template: int = 1,
    base_seed: int | None = None,
    template_slugs: set[str] | None = None,
) -> list[dict[str, Any]]:
    payloads = _load_payloads(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_manifest: list[dict[str, Any]] = []
    used_document_ids: set[str] = set()

    for index, payload in enumerate(payloads, start=1):
        payload, document_type_resolution = _prepare_renderer_payload(payload)
        requested_filename = requested_output_filename(payload, index)
        document_id = _output_id(payload, index)
        if document_id in used_document_ids:
            document_id = f"{document_id}-{index:05d}"
        used_document_ids.add(document_id)
        document_output_dir = output_dir / document_id
        rendered: list[dict[str, object]] = []
        try:
            rendered = render_generation_payload(
                payload,
                document_output_dir,
                allow_failed=allow_failed,
                per_template=per_template,
                base_seed=(
                    base_seed + index - 1
                    if base_seed is not None
                    else None
                ),
                template_slugs=template_slugs,
            )
            _finalize_rendered_document(
                payload,
                document_output_dir,
                rendered,
                requested_filename,
            )
        except (
            GeneratedDocumentPipelineError,
            RuntimeError,
            ValueError,
            OSError,
        ) as exc:
            _cleanup_rendered_artifacts(document_output_dir, rendered)
            batch_manifest.append(
                {
                    "document_id": document_id,
                    "document_type": _document_type(payload),
                    "document_type_resolution": (
                        document_type_resolution.to_dict()
                    ),
                    "status": "rejected",
                    "error": str(exc),
                }
            )
            continue

        batch_manifest.append(
            {
                "document_id": document_id,
                "document_type": _document_type(payload),
                "document_type_resolution": document_type_resolution.to_dict(),
                "status": _combined_render_status(rendered),
                "output_filename": requested_filename,
                "render_count": len(rendered),
                "output_dir": str(document_output_dir),
            }
        )

    (output_dir / "batch_manifest.json").write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return batch_manifest


def render_input_directory(
    input_dir: Path,
    output_dir: Path,
    *,
    allow_failed: bool = False,
    selection_seed: int | None = None,
    variation_count: int = 3,
) -> dict[str, Any]:
    """디렉터리 전체를 문서 유형별 균등 랜덤 서식 한 건씩 렌더링한다."""

    if not input_dir.is_dir():
        raise ValueError(f"Input is not a directory: {input_dir}")
    resolved_input_dir = input_dir.resolve()
    resolved_output_dir = output_dir.resolve()
    if (
        resolved_output_dir == resolved_input_dir
        or resolved_input_dir in resolved_output_dir.parents
    ):
        raise ValueError(
            "output_dir must be outside input_dir to avoid re-reading outputs"
        )
    resolved_seed = (
        selection_seed if selection_seed is not None else secrets.randbits(63)
    )
    selector = BalancedTemplateSelector(
        seed=resolved_seed,
        variation_count=variation_count,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    documents: list[dict[str, Any]] = []
    used_document_ids: set[str] = set()
    document_index = 0

    for input_file in _input_files(input_dir):
        source_file = input_file.relative_to(input_dir).as_posix()
        try:
            payloads = _load_payloads(input_file)
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            documents.append(
                {
                    "source_file": source_file,
                    "status": "rejected",
                    "stage": "input",
                    "error": str(exc),
                }
            )
            continue

        for payload_index, payload in enumerate(payloads, start=1):
            document_index += 1
            payload, document_type_resolution = _prepare_renderer_payload(payload)
            requested_filename = requested_output_filename(
                payload,
                document_index,
            )
            document_id = _unique_document_id(
                payload,
                document_index,
                used_document_ids,
            )
            document_type = _document_type(payload)
            assignment = selector.select(
                document_type,
                item_key=f"{source_file}:{payload_index}",
                renderer_family=(
                    "verbatim" if _uses_verbatim_renderer(payload) else None
                ),
            )
            document_output_dir = output_dir / document_id
            selection = assignment.to_dict()
            selection["source_file"] = source_file
            selection["payload_index"] = payload_index

            render_attempts: list[dict[str, object]] = []
            rendered: list[dict[str, object]] = []
            try:
                rendered, accepted_selection = _render_with_source_text_retries(
                    payload,
                    document_output_dir,
                    assignment=assignment,
                    document_type=document_type,
                    allow_failed=allow_failed,
                    attempt_log=render_attempts,
                )
                for entry in rendered:
                    entry["batch_selection"] = selection
                    entry["render_attempts"] = render_attempts
                _finalize_rendered_document(
                    payload,
                    document_output_dir,
                    rendered,
                    requested_filename,
                    assignment=assignment,
                )
            except (
                GeneratedDocumentPipelineError,
                RuntimeError,
                ValueError,
                OSError,
            ) as exc:
                _cleanup_rendered_artifacts(document_output_dir, rendered)
                documents.append(
                    {
                        "document_id": document_id,
                        "source_file": source_file,
                        "payload_index": payload_index,
                        "document_type": document_type,
                        "document_type_resolution": (
                            document_type_resolution.to_dict()
                        ),
                        "status": "rejected",
                        "stage": "render",
                        "selection": selection,
                        "render_attempts": render_attempts,
                        "error": str(exc),
                    }
                )
                continue

            render_status = _combined_render_status(rendered)
            documents.append(
                {
                    "document_id": document_id,
                    "source_file": source_file,
                    "payload_index": payload_index,
                    "document_type": document_type,
                    "document_type_resolution": (
                        document_type_resolution.to_dict()
                    ),
                    "status": render_status,
                    "output_filename": requested_filename,
                    "render_count": 1,
                    "output_dir": str(document_output_dir),
                    "selection": selection,
                    "accepted_selection": accepted_selection,
                    "render_attempts": render_attempts,
                    "synthetic_scan": rendered[0].get("synthetic_scan"),
                    "synthetic_handwriting": rendered[0].get(
                        "synthetic_handwriting"
                    ),
                }
            )

    batch_manifest = {
        "mode": "balanced_random",
        "input_dir": str(input_dir),
        "selection_seed": resolved_seed,
        "variation_count": variation_count,
        "document_count": document_index,
        "success_count": sum(
            entry["status"] in SUCCESSFUL_RENDER_STATUSES
            for entry in documents
        ),
        "rejected_count": sum(
            entry["status"] == "rejected" for entry in documents
        ),
        "synthetic_scan_count": sum(
            bool(
                isinstance(entry.get("synthetic_scan"), dict)
                and entry["synthetic_scan"].get("applied")
            )
            for entry in documents
        ),
        "synthetic_handwriting_count": sum(
            bool(
                isinstance(entry.get("synthetic_handwriting"), dict)
                and entry["synthetic_handwriting"].get("applied")
            )
            for entry in documents
        ),
        "documents": documents,
    }
    (output_dir / "batch_manifest.json").write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return batch_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        help=(
            "생성 계약 .json/.jsonl/.txt 파일 또는 해당 파일이 든 디렉터리. "
            "디렉터리는 문서당 균등 랜덤 서식 한 건을 생성합니다."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="렌더링 결과를 쓸 디렉터리 (예: output/pdf/generated_documents)",
    )
    parser.add_argument("--per-template", type=int, default=1)
    parser.add_argument(
        "--seed",
        type=int,
        help=(
            "재현용 seed. 디렉터리 실행에서 생략하면 실행마다 새 seed를 "
            "만들어 batch_manifest.json에 기록합니다."
        ),
    )
    parser.add_argument(
        "--variation-count",
        type=int,
        default=3,
        choices=(1, 2, 3),
        help="디렉터리 균등 배치에서 사용할 구조 변주 수 (기본: 3)",
    )
    parser.add_argument(
        "--template",
        action="append",
        choices=ALL_TEMPLATE_SLUGS,
        help=(
            "렌더링할 템플릿. 생략하면 입력 document_type에 맞는 "
            "템플릿 전체를 사용합니다."
        ),
    )
    parser.add_argument(
        "--allow-failed-input",
        action="store_true",
        help="failure가 있는 입력도 명시적으로 렌더링합니다.",
    )
    args = parser.parse_args()

    if args.input.is_dir():
        if args.template:
            parser.error("--template is only available for single-file input")
        if args.per_template != 1:
            parser.error(
                "--per-template is fixed to 1 for balanced directory input"
            )
        manifest = render_input_directory(
            args.input,
            args.output_dir,
            allow_failed=args.allow_failed_input,
            selection_seed=args.seed,
            variation_count=args.variation_count,
        )
        print(
            "[batch] "
            f"seed={manifest['selection_seed']} "
            f"ok={manifest['success_count']} "
            f"rejected={manifest['rejected_count']}"
        )
        for entry in manifest["documents"]:
            selection = (
                entry.get("accepted_selection")
                or entry.get("selection")
                or {}
            )
            suffix = (
                " -> "
                f"{selection.get('template_slug')}/"
                f"variation-{selection.get('variation_index')}/"
                + "+".join(
                    (
                        *(
                            ("handwriting",)
                            if isinstance(
                                entry.get("synthetic_handwriting"), dict
                            )
                            and entry["synthetic_handwriting"].get(
                                "applied"
                            )
                            else ()
                        ),
                        (
                            "scan"
                            if isinstance(entry.get("synthetic_scan"), dict)
                            and entry["synthetic_scan"].get("applied")
                            else "digital"
                        ),
                    )
                )
                if entry["status"] in SUCCESSFUL_RENDER_STATUSES
                else f": {entry.get('error', 'rejected')}"
            )
            print(
                f"[{entry['status']}] "
                f"{entry.get('document_id', entry['source_file'])}{suffix}"
            )
        if manifest["rejected_count"]:
            raise SystemExit(1)
        return

    manifest = render_input_file(
        args.input,
        args.output_dir,
        allow_failed=args.allow_failed_input,
        per_template=args.per_template,
        base_seed=args.seed,
        template_slugs=set(args.template) if args.template else None,
    )
    rejected = [entry for entry in manifest if entry["status"] == "rejected"]
    for entry in manifest:
        print(
            f"[{entry['status']}] {entry['document_id']}"
            + (
                f" -> {entry['output_dir']}"
                if entry["status"] in SUCCESSFUL_RENDER_STATUSES
                else f": {entry['error']}"
            )
        )
    if rejected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
