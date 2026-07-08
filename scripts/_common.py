"""스크립트 공통 헬퍼 — src/ 경로 등록 (plan-eng-review DRY 지적, 2026-07-08).

각 scripts/*.py가 직접 실행될 때(`python scripts/foo.py`) src/ 아래 패키지를
import할 수 있도록 경로를 등록한다. 이 모듈 자체는 실행되는 스크립트와 같은
디렉터리에 있어 파이썬이 실행 시 자동으로 sys.path에 넣어주는 디렉터리
안에 있으므로, 별도 sys.path 조작 없이 각 스크립트에서 바로 import 가능하다.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_src_on_path() -> None:
    src = str(Path(__file__).parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
