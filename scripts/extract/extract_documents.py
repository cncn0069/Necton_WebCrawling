"""Extract PDF/HWP/HWPX/XLSX sources into canonical v2 ``.json.gz`` artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path


from rd2.extraction.pipeline import (  # noqa: E402
    OCR_QUEUE_MANIFEST_NAME,
    RUN_MANIFEST_NAME,
    SUPPORTED_SUFFIXES,
    iter_source_documents,
    run_extraction,
)

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_OCR_QUEUE_ROOT = _DATA_ROOT / "ocr_queue"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="all",
        help='data/{source}/ directory name; "all" discovers every source directory',
    )
    parser.add_argument(
        "--format",
        dest="source_format",
        choices=("all", "pdf", "hwp", "hwpx", "xlsx", "xlsm"),
        default="all",
        help="source format to extract",
    )
    parser.add_argument("--document", type=Path, help="extract one document under data/")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process at most this many deterministically ordered documents",
    )
    parser.add_argument("--force", action="store_true", help="re-extract current artifacts")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="exit zero even when the run manifest contains failures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.document is not None:
        document = args.document.resolve()
        if document.suffix.lower() not in SUPPORTED_SUFFIXES:
            parser.error("--document must be a PDF, HWP, HWPX, XLSX, or XLSM file")
        requested_suffix = None if args.source_format == "all" else f".{args.source_format}"
        if requested_suffix and document.suffix.lower() != requested_suffix:
            parser.error("--document extension does not match --format")
        documents = [document]
    else:
        try:
            documents = list(
                iter_source_documents(
                    _DATA_ROOT,
                    source=args.source,
                    source_format=args.source_format,
                )
            )
        except ValueError as exc:
            parser.error(str(exc))
        if args.limit is not None:
            documents = documents[: args.limit]

    if not documents:
        parser.error("no supported source documents matched the requested scope")

    manifest = run_extraction(
        documents,
        data_root=_DATA_ROOT,
        extracted_root=_EXTRACTED_ROOT,
        ocr_queue_root=_OCR_QUEUE_ROOT,
        force=args.force,
    )
    counts = manifest["counts"]
    print(
        f"{manifest['status']}: total={counts['total']} processed={counts['processed']} "
        f"skipped={counts['skipped']} succeeded={counts['succeeded']} failed={counts['failed']}"
    )
    for failure in manifest["failures"]:
        print(f"[failure] {failure['source_path']}: {failure['error']}")
    print(f"manifest: {_EXTRACTED_ROOT / RUN_MANIFEST_NAME}")
    print(f"ocr queue: {_OCR_QUEUE_ROOT / OCR_QUEUE_MANIFEST_NAME}")

    if manifest["status"] == "partial" and not args.allow_partial:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
