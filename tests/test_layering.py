"""계층 경계 — 책임은 위에서 아래로만 간다.

두 가지를 본다.

    1. ``scripts/``는 바깥 세상과 직접 말하지 않는다 (아래 원래 규칙)
    2. ``src/rd2`` 안의 패키지 의존이 한 방향이다 (``PACKAGE_RANK``)

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


#: ``src/rd2`` 패키지의 층. 숫자가 작을수록 아래고, import는 **더 작은 숫자로만**
#: 갈 수 있다. 같은 층끼리도 못 간다 — 같은 숫자를 준 것들(``schema``·``canonical``,
#: ``storage``·``disclosure``)은 서로를 모르는 형제라는 뜻이다.
#:
#: 이 표는 설계가 아니라 **현재 그래프를 받아 적은 것**이다. 층을 새로 만들려고
#: 코드를 옮기지 않았고, 반대로 그래프가 이 표를 어기면 코드 쪽이 틀린 것이다.
#:
#: 여기 오기까지 걸린 것은 딱 두 줄이었다. ``augmentation.llm_augment``와
#: ``source_generation.c_track_templates``가 조문 정의를 쓰려고
#: ``rd2.generators.clause_data``를 import했는데, ``generators``는 이미 그 둘을
#: import하고 있었다. 어휘가 행위 안에 살아서 생긴 양방향이라, 어휘를
#: ``rd2.disclosure``로 내리자 사라졌다.
PACKAGE_RANK = {
    "schema": 0,
    "canonical": 0,
    "administrative_status": 0,
    "storage": 1,
    "disclosure": 1,
    "extractors": 1,
    "extraction": 2,
    "augmentation": 3,
    "source_generation": 4,
    "generators": 5,
    "adapters": 5,
    "audit": 6,
}

_SRC = _ROOT / "src" / "rd2"


def _rd2_package(module: str) -> str | None:
    """``rd2.x.y`` -> ``x``. ``rd2`` 밖이면 ``None``."""

    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "rd2":
        return None
    return parts[1].removesuffix(".py")


def _rd2_module_targets(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
            if child.module.startswith("rd2"):
                found.add(child.module)
        elif isinstance(child, ast.Import):
            for alias in child.names:
                if alias.name.startswith("rd2"):
                    found.add(alias.name)
    return found


def _src_paths() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize(
    "path", _src_paths(), ids=lambda p: p.relative_to(_SRC).as_posix()
)
def test_packages_only_import_downward(path: Path):
    rel = path.relative_to(_SRC)
    here = rel.parts[0] if len(rel.parts) > 1 else rel.stem
    if here not in PACKAGE_RANK:
        pytest.skip(f"{here}는 층이 지정되지 않았다")

    violations: list[str] = []
    for module in sorted(_rd2_module_targets(ast.parse(path.read_text(encoding="utf-8"), str(path)))):
        there = _rd2_package(module)
        if there is None or there == here or there not in PACKAGE_RANK:
            continue
        if PACKAGE_RANK[there] >= PACKAGE_RANK[here]:
            violations.append(
                f"{module} (층 {PACKAGE_RANK[there]} >= {here}의 층 {PACKAGE_RANK[here]})"
            )

    assert not violations, (
        f"{rel.as_posix()}가 위층 또는 같은 층을 import한다: {'; '.join(violations)}. "
        "쓰는 쪽을 옮기지 말고, 두 층이 함께 보는 것을 아래로 내려라 — "
        "clause_data를 rd2.disclosure로 내린 것과 같은 방식이다."
    )


#: OpenAI 어댑터. 자기를 부르는 쪽을 알면 안 되는 모듈이다.
_GATEWAY = _ROOT / "src" / "rd2" / "source_generation" / "gateway.py"


def test_openai_gateway_does_not_import_its_callers():
    """책임은 위에서 아래로만 간다 — 어댑터가 도메인을 import하지 않는다.

    ``pipeline``에서 떼어낼 때 ``contracts``의 ``FailureCode``·``TokenUsage``
    import가 딸려 왔었다. 아래층이 위층을 아는 모양인데, 정작 그 코드를 읽는
    소비자는 하나도 없어서 아무도 눈치채지 못했다. ``pipeline``이 사라진 뒤로도
    한참 남아 있었다.

    폴더 이름이 ``source_generation``이라 같은 일이 다시 일어나기 쉽다 — 옆 파일이
    전부 도메인이니 import 한 줄이 자연스러워 보인다. 그래서 사람 대신 테스트가
    본다.
    """

    imported = _toplevel_module_names(
        ast.parse(_GATEWAY.read_text(encoding="utf-8"), str(_GATEWAY))
    )

    assert "rd2" not in imported, (
        f"{_GATEWAY.relative_to(_ROOT)}가 rd2를 import한다. 이 어댑터는 자기를 "
        "부르는 쪽을 몰라야 한다 — 필요한 어휘는 여기서 정의하고(CallFailure·"
        "CallUsage), 계약으로 올리는 일은 받는 쪽이 한다."
    )


#: weasyprint를 import하기 전에 MSYS2 DLL 경로와 ssl을 확정 짓는 모듈.
_WEASYPRINT_RUNTIME = _SRC / "generators" / "weasyprint_runtime.py"


def _weasyprint_importers() -> list[Path]:
    paths = [*_src_paths(), *_script_paths()]
    return [
        path
        for path in paths
        if path != _WEASYPRINT_RUNTIME
        and "weasyprint" in _toplevel_module_names(
            ast.parse(path.read_text(encoding="utf-8"), str(path))
        )
    ]


def test_weasyprint_is_imported_through_its_runtime():
    """준비를 렌더러에 얹으면 import 순서가 준비 여부를 정하게 된다.

    윈도우에서 weasyprint는 MSYS2의 Pango/GObject DLL을 쓴다. 검색 경로를
    얹는 일은 ``import weasyprint`` **전에** 끝나야 하는데, 그 준비가
    ``official_document_rendering`` 한 곳에만 있었고 나머지 아홉 렌더러는
    weasyprint를 직접 import했다. ``balanced_batch_selection``이
    ``administrative_rule_rendering``을 먼저 집는 순간 준비 없이 닿아서
    ``OSError: cannot load library ... libgobject-2.0-0.dll``로 죽었다 —
    같은 코드가 import 순서에 따라 되기도 하고 안 되기도 했다.

    ``weasyprint_runtime``이 준비와 ``HTML``을 같이 들고 있으면 가져가는
    행위가 곧 준비다. 순서를 지킬 일이 없어진다.
    """

    offenders = [
        path.relative_to(_ROOT).as_posix() for path in _weasyprint_importers()
    ]

    assert not offenders, (
        f"{', '.join(offenders)}가 weasyprint를 직접 import한다. "
        "rd2.generators.weasyprint_runtime에서 HTML/URLFetcher를 가져와라 — "
        "그 모듈이 MSYS2 DLL 경로와 ssl을 먼저 확정 짓는다."
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
