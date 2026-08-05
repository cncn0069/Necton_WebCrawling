"""Safe resolution for the two persisted ``body_file_path`` conventions.

Older collection rows are relative to ``data/``.  Generated PDFs are relative
to the repository root (for example ``output/batch/...``).  Database values
are never absolute and cannot traverse either root, including through a
symlink.
"""

from __future__ import annotations

from pathlib import Path


def resolve_body_file_path(
    body_file_path: str,
    *,
    files_root: Path,
    repo_root: Path,
) -> Path | None:
    """Return an existing, in-bound body file for a persisted relative path."""

    stored_path = Path(body_file_path)
    if stored_path.is_absolute() or ".." in stored_path.parts:
        return None

    resolved_repo_root = repo_root.resolve()
    resolved_files_root = files_root.resolve()
    # New repo-relative paths are tried first, so ``data/foo.pdf`` remains
    # unambiguous while legacy ``source/type/file.pdf`` still falls back to
    # the data root.
    for root in (resolved_repo_root, resolved_files_root):
        candidate = (root / stored_path).resolve()
        if root not in (candidate, *candidate.parents):
            continue
        if candidate.is_file():
            return candidate
    return None
