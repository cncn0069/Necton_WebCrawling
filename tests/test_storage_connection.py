"""읽기 전용 접속정보 해석 — 어느 DB를 보는지가 사고 없이 정해지는가.

여기서 지키려는 것은 편의가 아니라 **로컬 덤프와 운영 RDS가 섞이지 않는
것**이다. 잘못 골라도 질의는 성공하고 산출물도 그럴듯하게 나오므로, 틀린
선택은 실행 중에 드러나지 않는다. 그래서 "모자라면 멈춘다"를 테스트로 박는다.
"""

from __future__ import annotations

import pytest

from rd2.storage.connection import (
    ConnectionSettingsError,
    describe,
    resolve_settings,
)

_LOCAL_ENV = {
    "MARIADB_HOST": "127.0.0.1",
    "MARIADB_USER": "local_user",
    "MARIADB_PASSWORD": "local_pw",
    "MARIADB_DATABASE": "rd2_dump",
}

_RDS_ENV = {
    "RDS_MARIADB_HOST": "rds.example.internal",
    "RDS_MARIADB_PORT": "33746",
    "RDS_MARIADB_USER": "rds_user",
    "RDS_MARIADB_PASSWORD": "rds_pw",
    "RDS_MARIADB_DATABASE": "rd2",
}


class TestResolveSettings:
    def test_reads_local_env_by_default(self):
        settings = resolve_settings(environ=dict(_LOCAL_ENV))

        assert settings["host"] == "127.0.0.1"
        assert settings["database"] == "rd2_dump"

    def test_port_defaults_to_3306_and_is_an_int(self):
        settings = resolve_settings(environ=dict(_LOCAL_ENV))

        assert settings["port"] == 3306

    def test_port_from_env_is_coerced_to_int(self):
        """pymysql은 port에 문자열을 받으면 접속 단계에서 죽는다."""

        settings = resolve_settings(environ={**_LOCAL_ENV, "MARIADB_PORT": "13306"})

        assert settings["port"] == 13306

    def test_explicit_argument_beats_env(self):
        settings = resolve_settings(host="10.0.0.9", environ=dict(_LOCAL_ENV))

        assert settings["host"] == "10.0.0.9"

    def test_missing_env_var_names_what_is_missing(self):
        env = {k: v for k, v in _LOCAL_ENV.items() if k != "MARIADB_HOST"}

        with pytest.raises(ConnectionSettingsError, match="MARIADB_HOST"):
            resolve_settings(environ=env)


class TestRdsSwitch:
    def test_from_rds_reads_the_rds_prefix(self):
        settings = resolve_settings(from_rds=True, environ={**_LOCAL_ENV, **_RDS_ENV})

        assert settings["host"] == "rds.example.internal"
        assert settings["port"] == 33746
        assert settings["database"] == "rd2"

    def test_from_rds_never_falls_back_to_local(self):
        """조용한 fallback은 "RDS를 봤다"고 믿으면서 로컬 덤프를 읽게 만든다.

        그 착각은 산출물만 봐서는 드러나지 않으므로 멈추는 쪽이 맞다.
        """

        with pytest.raises(ConnectionSettingsError, match="RDS_MARIADB_HOST"):
            resolve_settings(from_rds=True, environ=dict(_LOCAL_ENV))

    def test_tunnel_hint_is_shown_when_rds_settings_are_missing(self):
        """RDS는 퍼블릭 접근이 막혀 있어 터널 없이는 붙지 못한다."""

        with pytest.raises(ConnectionSettingsError, match="ssh -i"):
            resolve_settings(from_rds=True, environ={})

    def test_cli_overrides_point_the_rds_switch_at_a_tunnel(self):
        settings = resolve_settings(
            from_rds=True,
            host="127.0.0.1",
            port=13306,
            environ={**_LOCAL_ENV, **_RDS_ENV},
        )

        assert (settings["host"], settings["port"]) == ("127.0.0.1", 13306)
        # 터널로 덮어써도 계정·DB는 RDS 쪽 값이어야 한다.
        assert settings["database"] == "rd2"


class TestDescribe:
    def test_does_not_leak_the_password(self):
        settings = resolve_settings(environ={**_LOCAL_ENV, "MARIADB_PASSWORD": "s3cret"})

        assert "s3cret" not in describe(settings)

    def test_names_the_database_that_was_chosen(self):
        settings = resolve_settings(environ=dict(_LOCAL_ENV))

        assert describe(settings) == "local_user@127.0.0.1:3306/rd2_dump"
