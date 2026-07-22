"""Legacy HWP/HWPX entry point backed by canonical extraction v2.

Prefer ``scripts/extract_documents.py`` for new calls. This compatibility CLI
accepts the former options but only writes compact ``*.hwp[x].json.gz`` files
and the shared extraction run manifest.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import ensure_src_on_path

ensure_src_on_path()

from rd2.extraction.pipeline import (  # noqa: E402
    RUN_MANIFEST_NAME,
    iter_source_documents,
    run_extraction,
)

_REPO_ROOT = Path(__file__).parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_EXTRACTED_ROOT = _DATA_ROOT / "extracted"
_HWP_SUFFIXES = {".hwp", ".hwpx"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--hwp", type=Path, default=None, help="single HWP/HWPX document")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.hwp is not None:
        document = args.hwp.resolve()
        if document.suffix.lower() not in _HWP_SUFFIXES:
            parser.error("--hwp must reference an HWP or HWPX file")
        documents = [document]
    else:
        try:
            documents = [
                path
                for path in iter_source_documents(_DATA_ROOT, source=args.source)
                if path.suffix.lower() in _HWP_SUFFIXES
            ]
        except ValueError as exc:
            parser.error(str(exc))
        if args.limit is not None:
            documents = documents[: args.limit]

    if not documents:
        parser.error("no HWP/HWPX documents matched the requested scope")

    manifest = run_extraction(
        documents,
        data_root=_DATA_ROOT,
        extracted_root=_EXTRACTED_ROOT,
        force=args.force,
    )
    counts = manifest["counts"]
    print(
        f"{manifest['status']}: total={counts['total']} processed={counts['processed']} "
        f"skipped={counts['skipped']} succeeded={counts['succeeded']} failed={counts['failed']}"
    )
    print(f"manifest: {_EXTRACTED_ROOT / RUN_MANIFEST_NAME}")
    return 0 if manifest["status"] == "complete" or args.allow_partial else 1


if __name__ == "__main__":
    raise SystemExit(main())
