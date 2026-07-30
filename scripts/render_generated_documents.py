"""생성 계약 JSON/JSONL을 문서 유형별 PDF 묶음으로 렌더링한다."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from rd2.generators.generated_document_pipeline import (
    GeneratedDocumentPipelineError,
    render_generation_payload,
)
from rd2.generators.administrative_rule_rendering import (
    ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS,
)
from rd2.generators.official_document_rendering import (
    OFFICIAL_TEMPLATE_VARIANTS,
)
from rd2.generators.research_report_rendering import (
    RESEARCH_REPORT_TEMPLATE_VARIANTS,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output" / "pdf" / "generated_documents"


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


def main() -> None:
    template_choices = tuple(
        variant["slug"]
        for variant in (
            *OFFICIAL_TEMPLATE_VARIANTS,
            *RESEARCH_REPORT_TEMPLATE_VARIANTS,
            *ADMINISTRATIVE_RULE_TEMPLATE_VARIANTS,
        )
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="생성 계약 .json 또는 .jsonl")
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--per-template", type=int, default=1)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--template",
        action="append",
        choices=template_choices,
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
