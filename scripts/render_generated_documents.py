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
    BalancedTemplateSelector,
)
from rd2.generators.generated_document_pipeline import (
    GeneratedDocumentPipelineError,
    render_generation_payload,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output" / "pdf" / "generated_documents"
_SUPPORTED_INPUT_SUFFIXES = frozenset({".json", ".jsonl", ".txt"})


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
        document_id = _output_id(payload, index)
        if document_id in used_document_ids:
            document_id = f"{document_id}-{index:05d}"
        used_document_ids.add(document_id)
        document_output_dir = output_dir / document_id
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
        except (
            GeneratedDocumentPipelineError,
            RuntimeError,
            ValueError,
            OSError,
        ) as exc:
            batch_manifest.append(
                {
                    "document_id": document_id,
                    "status": "rejected",
                    "error": str(exc),
                }
            )
            continue

        batch_manifest.append(
            {
                "document_id": document_id,
                "status": "ok",
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
            document_id = _unique_document_id(
                payload,
                document_index,
                used_document_ids,
            )
            document_type = _document_type(payload)
            assignment = selector.select(
                document_type,
                item_key=f"{source_file}:{payload_index}",
            )
            document_output_dir = output_dir / document_id
            selection = assignment.to_dict()
            selection["source_file"] = source_file
            selection["payload_index"] = payload_index

            try:
                rendered = render_generation_payload(
                    payload,
                    document_output_dir,
                    allow_failed=allow_failed,
                    per_template=1,
                    base_seed=assignment.render_seed,
                    variation_offset=assignment.variation_offset,
                    template_slugs={assignment.template_slug},
                )
                if len(rendered) != 1:
                    raise RuntimeError(
                        "Balanced rendering must produce exactly one output, "
                        f"got {len(rendered)}"
                    )
            except (
                GeneratedDocumentPipelineError,
                RuntimeError,
                ValueError,
                OSError,
            ) as exc:
                documents.append(
                    {
                        "document_id": document_id,
                        "source_file": source_file,
                        "payload_index": payload_index,
                        "document_type": document_type,
                        "status": "rejected",
                        "stage": "render",
                        "selection": selection,
                        "error": str(exc),
                    }
                )
                continue

            for entry in rendered:
                entry["batch_selection"] = selection
            _write_document_manifest(document_output_dir, rendered)
            documents.append(
                {
                    "document_id": document_id,
                    "source_file": source_file,
                    "payload_index": payload_index,
                    "document_type": document_type,
                    "status": "ok",
                    "render_count": 1,
                    "output_dir": str(document_output_dir),
                    "selection": selection,
                }
            )

    batch_manifest = {
        "mode": "balanced_random",
        "input_dir": str(input_dir),
        "selection_seed": resolved_seed,
        "variation_count": variation_count,
        "document_count": document_index,
        "success_count": sum(
            entry["status"] == "ok" for entry in documents
        ),
        "rejected_count": sum(
            entry["status"] == "rejected" for entry in documents
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
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
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
            selection = entry.get("selection") or {}
            suffix = (
                " -> "
                f"{selection.get('template_slug')}/"
                f"variation-{selection.get('variation_index')}"
                if entry["status"] == "ok"
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
                if entry["status"] == "ok"
                else f": {entry['error']}"
            )
        )
    if rejected:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
