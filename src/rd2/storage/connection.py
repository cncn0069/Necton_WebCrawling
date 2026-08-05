"""읽기 전용 MariaDB 접속 — 스키마를 건드리지 않는다.

``DocumentStore``(db.py)는 생성자에서 ``CREATE TABLE``과 ALTER 마이그레이션을
돌린다. 수집 경로에는 필요한 동작이지만, 원문을 **고르기만** 하는 읽기 경로가
그걸 위해 운영 테이블 스키마를 건드릴 이유는 없다. 그래서 스크립트들이 각자
pymysql을 직접 열었고, 세 벌의 ``connect_mariadb()``가 생겼다.

문제는 중복 자체가 아니라 **그 셋이 서로 다른 안전장치를 갖고 있었다**는
것이다. 셋 중 하나만 RDS/로컬을 나눠 봤고 나머지 둘은 ``MARIADB_*``를 무조건
읽었다. .env의 ``MARIADB_HOST``를 RDS로 고쳐 쓰는 순간, 그 둘은 아무 표시 없이
운영 DB를 보게 된다. 그 착각은 산출물만 봐서는 드러나지 않는다.

여기 있는 것은 둘뿐이다.

    1. ``resolve_settings`` — CLI > (--from-rds면) ``RDS_MARIADB_*`` > ``MARIADB_*``
    2. ``connect`` — 그 설정으로 여는 pymysql 커넥션 (DDL 없음)

읽기 전용은 계정 권한이 아니라 **호출 규약**으로 지킨다. 이 모듈은
``commit()``을 부르지 않고, 이걸 쓰는 쪽도 INSERT/UPDATE를 보내지 않는다.
쓰기가 필요하면 ``DocumentStore``를 쓴다.
"""

from __future__ import annotations

import os
from typing import Any

import pymysql
import pymysql.cursors

#: ``--from-rds``일 때 읽는 접두사. 로컬용 ``MARIADB_*``와 이름을 나눠 둔다 —
#: 한 벌을 돌려쓰면 전환이 .env 편집으로 일어나고, 그건 되돌리는 걸 잊기 쉽다.
#: 전환은 플래그 하나로만 일어나게 한다.
RDS_ENV_PREFIX = "RDS_MARIADB_"
LOCAL_ENV_PREFIX = "MARIADB_"

#: RDS 자체는 퍼블릭 접근이 막혀 있어(deploy/README.md) 개발 PC에서는 EC2를
#: 거치는 SSH 터널의 로컬 끝을 가리키게 된다. 그때 host는 127.0.0.1이고 port만
#: 터널 포트로 바뀌므로, 두 값을 환경변수로 못 박기보다 CLI로 덮어쓰는 쪽이 낫다.
TUNNEL_HINT = (
    "RDS는 퍼블릭 접근이 막혀 있다. EC2로 터널을 열고 그 로컬 끝을 가리켜라:\n"
    "  ssh -i <키> -L 13306:<RDS 엔드포인트>:3306 <계정>@<EC2 호스트> -N\n"
    "  ... --from-rds --db-host 127.0.0.1 --db-port 13306"
)

#: 접속정보 키와 기본값. port만 기본이 있고 나머지는 없어야 한다 — host나
#: database에 기본값을 두면 "설정을 깜빡했다"가 "다른 DB를 봤다"로 조용히
#: 바뀐다.
_FIELDS: tuple[tuple[str, object | None], ...] = (
    ("host", None),
    ("port", 3306),
    ("user", None),
    ("password", None),
    ("database", None),
)


class ConnectionSettingsError(RuntimeError):
    """접속정보가 모자라거나 접속 자체가 실패했다."""


def resolve_settings(
    *,
    from_rds: bool = False,
    host: str | None = None,
    port: int | str | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """접속정보를 정한다. 우선순위는 CLI 인자 > 환경변수.

    ``from_rds``인데 ``RDS_MARIADB_*``도 인자도 없으면 **로컬로 조용히 떨어지지
    않고** 멈춘다. 조용한 fallback은 "RDS를 봤다"고 믿으면서 로컬 덤프를 읽는
    결과가 되는데, 그 착각은 산출물만 봐서는 드러나지 않는다.
    """

    env = os.environ if environ is None else environ
    prefix = RDS_ENV_PREFIX if from_rds else LOCAL_ENV_PREFIX
    overrides = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }

    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for key, default in _FIELDS:
        name = f"{prefix}{key.upper()}"
        value = overrides[key]
        if value is None:
            value = env.get(name, default)
        if value is None:
            missing.append(name)
        resolved[key] = value

    if missing:
        hint = TUNNEL_HINT if from_rds else "  .env를 확인하라."
        raise ConnectionSettingsError(f"접속정보가 없다: {', '.join(missing)}\n{hint}")

    resolved["port"] = int(resolved["port"])
    return resolved


def describe(settings: dict[str, Any]) -> str:
    """로그에 찍을 접속 대상. 비밀번호는 넣지 않는다."""

    return f"{settings['user']}@{settings['host']}:{settings['port']}/{settings['database']}"


def connect(
    settings: dict[str, Any] | None = None,
    *,
    dict_rows: bool = False,
    **overrides: Any,
):
    """읽기 전용 커넥션을 연다. 마이그레이션을 돌리지 않는다.

    ``settings`` 없이 부르면 ``resolve_settings(**overrides)``로 만든다.
    ``dict_rows``면 행이 dict로 온다(기존 ``cursorclass=DictCursor`` 자리).
    """

    if settings is None:
        settings = resolve_settings(**overrides)

    kwargs: dict[str, Any] = dict(settings, charset="utf8mb4")
    if dict_rows:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    try:
        return pymysql.connect(**kwargs)
    except pymysql.MySQLError as exc:
        raise ConnectionSettingsError(f"접속 실패({describe(settings)}): {exc}") from exc


def add_arguments(parser, *, database_help: str | None = None) -> None:
    """접속 관련 CLI 인자를 붙인다.

    스크립트마다 플래그 이름이 갈리면 "어제 쓰던 옵션"이 오늘 다른 DB를
    가리키게 된다. 이름은 여기서 한 번만 정한다.
    """

    parser.add_argument(
        "--from-rds",
        action="store_true",
        help=f"{RDS_ENV_PREFIX}* 환경변수로 접속한다. 터널을 쓸 때는 --db-host/--db-port로 덮어쓴다",
    )
    parser.add_argument("--db-host", default=None)
    parser.add_argument("--db-port", type=int, default=None)
    parser.add_argument("--db-user", default=None)
    parser.add_argument("--db-password", default=None)
    parser.add_argument(
        "--database",
        default=None,
        help=database_help or f"기본값은 .env의 {LOCAL_ENV_PREFIX}DATABASE",
    )


def settings_from_args(args) -> dict[str, Any]:
    """``add_arguments``로 붙인 인자를 ``resolve_settings``에 넘긴다."""

    return resolve_settings(
        from_rds=getattr(args, "from_rds", False),
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.database,
    )
