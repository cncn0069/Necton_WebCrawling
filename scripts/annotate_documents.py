"""Compatibility notice for the removed persisted-annotation stage.

Canonical extraction v2 is annotated only in memory while candidate detection
runs. This command intentionally writes nothing to ``data/annotated``.
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="all", help=argparse.SUPPRESS)
    parser.add_argument("--limit", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    parser.parse_args(argv)
    print(
        "annotation 사본 저장 단계는 제거되었습니다. "
        "scripts/find_candidates.py가 canonical v2 추출본을 한 번 읽고 메모리에서 주석합니다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
