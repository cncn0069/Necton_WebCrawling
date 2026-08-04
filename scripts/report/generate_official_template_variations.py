"""공문 템플릿마다 seed 기반 레이아웃 변주 PDF를 생성한다."""

from __future__ import annotations

import argparse
from pathlib import Path

from generate_official_template_previews import _SHARED_CONTEXT
from rd2.generators.official_document_rendering import (
    render_official_document_variations,
)


def generate_variations(
    output_dir: Path,
    *,
    per_template: int = 3,
    base_seed: int = 20260728,
) -> list[dict[str, object]]:
    return render_official_document_variations(
        _SHARED_CONTEXT,
        output_dir,
        per_template=per_template,
        base_seed=base_seed,
        enforce_expected_pages=True,
        reject_legacy_identity=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "변주 PDF를 쓸 디렉터리 "
            "(예: output/pdf/official_template_layout_variations)"
        ),
    )
    parser.add_argument("--per-template", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260728)
    args = parser.parse_args()
    generate_variations(
        args.output_dir,
        per_template=args.per_template,
        base_seed=args.seed,
    )


if __name__ == "__main__":
    main()
