"""테스트에서 ``scripts/`` 아래 스크립트를 import할 수 있게 한다.

스크립트는 패키지가 아니라 진입점이라 ``rd2``처럼 설치되지 않는다. 그런데
CLI의 인자 처리나 종료 코드도 테스트 대상이라, 테스트는 스크립트를 모듈로
불러올 수 있어야 한다.

예전에는 테스트 파일마다 같은 3줄을 적어 ``scripts/``를 sys.path에 넣었다.
스크립트를 목적별 폴더로 나누는 순간 그 11벌이 전부 틀린 경로를 가리켰다.
경로를 아는 곳은 여기 하나로 둔다 — 폴더가 또 바뀌어도 고칠 자리는 이 파일이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _register_script_dirs() -> None:
    # 스크립트끼리도 서로를 import한다(예: report/의 두 파일). 그 경우도
    # 자기 폴더가 sys.path에 있어야 하므로 하위 폴더를 모두 넣는다.
    for directory in sorted(d for d in _SCRIPTS.iterdir() if d.is_dir() and d.name != "__pycache__"):
        path = str(directory)
        if path not in sys.path:
            sys.path.insert(0, path)


_register_script_dirs()
