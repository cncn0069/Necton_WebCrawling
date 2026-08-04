"""계층 경계 — 스크립트는 바깥 세상과 직접 말하지 않는다.

``scripts/``는 레이어가 아니라 **진입점**이다. 인자를 읽고, 조립하고, 부른다.
DB 커넥션을 열거나 모델을 호출하는 일은 어댑터의 몫이다.

이걸 규칙으로 적어 두는 이유는 취향 때문이 아니다. 스크립트가 각자 pymysql을
열면 안전장치도 각자 갖게 되고, 실제로 그랬다 — 세 벌의 ``connect_mariadb()``
중 로컬과 운영을 나눠 본 것은 하나뿐이었다. 나머지 둘은 ``.env``의
``MARIADB_HOST``가 가리키는 곳이면 어디든 조용히 붙었다. 그 차이는 코드를 나란히
놓고 봐야 보인다. 그래서 사람 대신 테스트가 본다.

어댑터가 있는 곳:

    pymysql   -> rd2.storage.connection / rd2.storage.db
    openai    -> rd2.source_generation.gateway / rd2.generators.generate
    httpx     -> rd2.adapters.*
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"

#: 어댑터를 거쳐야 하는 외부 의존.
FORBIDDEN_IN_SCRIPTS = {"pymysql", "openai", "httpx", "sqlite3"}

#: 예외는 이유와 함께만 남긴다. 이유를 못 적으면 예외가 아니라 빚이다.
#: 키는 ``scripts/`` 기준 상대경로다.
ALLOWED = {
    # 이 스크립트의 일 자체가 sqlite에서 mariadb로 옮기는 것이다. 옮기는 쪽
    # 포맷을 아는 것이 이 파일의 존재 이유라 어댑터 뒤로 숨길 대상이 아니다.
    ("maintenance/migrate_sqlite_to_mariadb.py", "sqlite3"),
}


def _toplevel_module_names(node: ast.AST) -> set[str]:
    """import 문에서 최상위 모듈 이름만 뽑는다.

    함수 안에 숨긴 import도 잡는다 — ``ast.walk``는 중첩을 가리지 않는다.
    지연 import는 경계를 넘는 것을 감추는 가장 흔한 방법이다.
    """

    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for alias in child.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(child, ast.ImportFrom):
            # `from . import x` 같은 상대 import는 module이 None이다.
            if child.level == 0 and child.module:
                found.add(child.module.split(".")[0])
    return found


def _script_paths() -> list[Path]:
    return sorted(_SCRIPTS.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(_SCRIPTS).as_posix()


@pytest.mark.parametrize("path", _script_paths(), ids=_relative)
def test_scripts_reach_the_outside_world_through_adapters(path: Path):
    imported = _toplevel_module_names(ast.parse(path.read_text(encoding="utf-8"), str(path)))
    violations = {
        name
        for name in imported & FORBIDDEN_IN_SCRIPTS
        if (_relative(path), name) not in ALLOWED
    }

    assert not violations, (
        f"{path.relative_to(_ROOT)}가 {', '.join(sorted(violations))}를 직접 import한다. "
        "어댑터를 거쳐라 — rd2.storage.connection(DB), "
        "rd2.source_generation.gateway 또는 rd2.generators.generate(OpenAI). "
        "정말 예외라면 이유를 달아 ALLOWED에 넣어라."
    )


def test_allowlist_has_no_stale_entries():
    """고쳐 놓고 예외만 남으면, 다음 사람은 그게 아직 필요한 줄 안다."""

    for relative_path, module in sorted(ALLOWED):
        path = _SCRIPTS / relative_path
        assert path.exists(), f"ALLOWED가 없는 파일을 가리킨다: {relative_path}"
        imported = _toplevel_module_names(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        assert module in imported, (
            f"{relative_path}는 더 이상 {module}를 import하지 않는다 — ALLOWED에서 지워라."
        )
